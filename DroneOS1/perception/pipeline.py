"""Perception processing pipeline: captures, detects, geolocates, aggregates, and broadcasts."""

import asyncio
import math
import time
from typing import Dict, List, Optional, Any, Tuple

from DroneOS1.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS1.perception.detector import GarbageDetector
from DroneOS1.perception.frame_sources import FrameSource
from DroneOS1.perception.geolocate import pixel_to_lat_lon, lat_lon_to_meters, pixel_to_ray_ned, compute_ray_to_point_cpa
from DroneOS1.shared.protocol.messages import DetectionItem, DetectionMessage
from DroneOS1.shared.utils.logger import setup_logger

try:
    from DroneOS1.perception.diagnostics import log_raw_detection, log_peer_exclusion_eval
except ImportError:
    log_raw_detection = None
    log_peer_exclusion_eval = None

logger = setup_logger("PerceptionPipeline")


class PerceptionPipeline:
    """
    Coordinates asynchronous frame acquisition, ML inference, ground geolocation,
    temporal track aggregation, and network broadcasting.
    Runs as an isolated background task that never blocks or faults the flight controller.
    """

    def __init__(
        self,
        drone_id: str,
        frame_source: FrameSource,
        detector: GarbageDetector,
        flight_controller,
        network_adapter,
        config,
        session_id: Optional[str] = None,
        session_generation: Optional[int] = None,
        swarm_manager: Optional[Any] = None,
    ):
        self.drone_id = drone_id
        self.frame_source = frame_source
        self.detector = detector
        self.fc = flight_controller
        self.network = network_adapter
        self.config = config
        self.session_id = session_id
        self.session_generation = session_generation
        self.swarm_manager = swarm_manager
        self.peer_exclusion_radius_m = float(getattr(config, "peer_exclusion_radius_m", 4.0))

        self.aggregator = TrackAggregator(
            drone_id=drone_id,
            merge_radius_m=getattr(config, "merge_radius_m", 2.5),
            min_hits=getattr(config, "min_hits", 2),
            update_interval_s=getattr(config, "update_interval_s", 10.0),
            max_tracks=getattr(config, "max_tracks", 2000),
            min_temporal_separation_s=getattr(config, "min_temporal_separation_s", 2.0),
            min_drone_displacement_m=getattr(config, "min_drone_displacement_m", 1.5),
        )

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._frame_id = 0
        self._disabled_by_error = False
        self.last_rejection_reason: Optional[str] = None
        self.last_step_stats: dict = {}
        self._deferred_buffer: List[Dict[str, Any]] = []

    async def start(self) -> None:
        """Start perception loop as an isolated asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self.run_loop())

    async def stop(self) -> None:
        """Stop perception pipeline and release resources."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.frame_source.close()
        self.detector.close()
        logger.info("Perception pipeline stopped.")

    async def run_loop(self) -> None:
        """Main periodic perception processing loop."""
        self._running = True
        logger.info("Perception pipeline loop started (max_fps=%.1f).", self.config.max_fps)
        interval = 1.0 / max(0.1, self.config.max_fps)

        while self._running:
            loop_start = time.monotonic()
            try:
                await self._step()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Unexpected error in perception pipeline loop: %s", e)
                # Graceful backoff to protect CPU and flight stability
                await asyncio.sleep(2.0)

            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.01, interval - elapsed)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    async def _step(self) -> None:
        """Single frame iteration: gate, acquire, infer, geolocate, aggregate, broadcast."""
        self.last_rejection_reason = None
        self.last_step_stats = {
            "reached_camera": False,
            "reached_inference": False,
            "raw_detections": 0,
            "geo_detections": 0,
            "emitted_items": 0,
            "rejection_reason": None,
        }

        # 1. Detector load check (asynchronous to avoid freezing main asyncio loop)
        loaded = True
        if hasattr(self.detector, "ensure_loaded") and callable(self.detector.ensure_loaded):
            res = self.detector.ensure_loaded()
            if asyncio.iscoroutine(res):
                loaded = await res
            else:
                loaded = res
        elif hasattr(self.detector, "load") and callable(self.detector.load):
            res = self.detector.load()
            if asyncio.iscoroutine(res):
                loaded = await res
            else:
                loaded = res

        if loaded is False:
            self.last_rejection_reason = "DETECTOR_NOT_LOADED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            if not getattr(self, "_load_failed_logged", False):
                logger.error("Perception detector not loaded; retrying with backoff.")
                self._load_failed_logged = True
            return

        # 2. Early telemetry gating BEFORE acquiring a frame
        pre_telem = await self.fc.get_telemetry()
        if pre_telem is None:
            self.last_rejection_reason = "TELEMETRY_UNAVAILABLE_PRE"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Pre-capture telemetry is unavailable (None)")
            return

        # Must be armed
        is_armed = getattr(pre_telem, "armed_state", None) == "ARMED"
        if not is_armed:
            self.last_rejection_reason = f"VEHICLE_NOT_ARMED (armed_state='{getattr(pre_telem, 'armed_state', None)}')"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Vehicle not armed (state=%s)", getattr(pre_telem, "armed_state", None))
            return

        # GPS fix valid
        if not getattr(pre_telem, "gps_valid", False):
            self.last_rejection_reason = "GPS_INVALID"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: GPS fix is invalid")
            return

        # Altitude above minimum
        alt = float(getattr(pre_telem, "altitude", 0.0) or 0.0)
        if alt < self.config.min_altitude_m:
            self.last_rejection_reason = f"ALTITUDE_BELOW_MINIMUM (alt={alt:.2f}m < {self.config.min_altitude_m:.2f}m)"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Altitude %.2fm below min %.2fm", alt, self.config.min_altitude_m)
            return

        # Tilt angle limits (|roll|, |pitch| <= max_tilt_deg)
        roll = float(getattr(pre_telem, "roll", 0.0) or 0.0)
        pitch = float(getattr(pre_telem, "pitch", 0.0) or 0.0)
        if abs(roll) > self.config.max_tilt_deg or abs(pitch) > self.config.max_tilt_deg:
            self.last_rejection_reason = f"TILT_EXCEEDED_PRE (roll={roll:.1f}deg, pitch={pitch:.1f}deg > {self.config.max_tilt_deg:.1f}deg)"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Tilt exceeded (roll=%.1f, pitch=%.1f)", roll, pitch)
            return

        pre_lat = getattr(pre_telem, "latitude", None)
        pre_lon = getattr(pre_telem, "longitude", None)
        if pre_lat is None or pre_lon is None or (pre_lat == 0.0 and pre_lon == 0.0):
            self.last_rejection_reason = f"INVALID_LAT_LON_PRE (lat={pre_lat}, lon={pre_lon})"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Invalid coordinates (%.6f, %.6f)", pre_lat or 0, pre_lon or 0)
            return

        # 3. Acquire frame
        frame = await self.frame_source.get_frame()
        if frame is None or frame.image is None:
            self.last_rejection_reason = "FRAME_ACQUISITION_FAILED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Camera frame acquisition returned None")
            return

        self.last_step_stats["reached_camera"] = True

        # 4. Fresh telemetry snapshot immediately after capture for geolocation
        telem = await self.fc.get_telemetry()
        if telem is None:
            self.last_rejection_reason = "TELEMETRY_UNAVAILABLE_POST"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Post-capture telemetry is unavailable (None)")
            return

        # Freshness and synchronization validation
        now = time.time()
        max_telem_age = getattr(self.config, "max_telemetry_age_s", 2.0)
        max_frame_age = getattr(self.config, "max_frame_age_s", 2.0)
        max_sync_skew = getattr(self.config, "max_sync_skew_s", 1.0)

        telem_ts = getattr(telem, "timestamp", None)
        frame_ts = getattr(frame, "timestamp", None)

        # Only validate against wall-clock time if timestamp is a real epoch (> year 2001)
        if telem_ts is not None and float(telem_ts) > 1_000_000_000:
            if (now - float(telem_ts)) > max_telem_age:
                self.last_rejection_reason = f"STALE_TELEMETRY (age={now - float(telem_ts):.2f}s > {max_telem_age:.2f}s)"
                self.last_step_stats["rejection_reason"] = self.last_rejection_reason
                logger.debug("Dropping frame: telemetry is stale (age=%.2fs > %.2fs)", now - float(telem_ts), max_telem_age)
                return

        if frame_ts is not None and float(frame_ts) > 1_000_000_000:
            if (now - float(frame_ts)) > max_frame_age:
                self.last_rejection_reason = f"STALE_FRAME (age={now - float(frame_ts):.2f}s > {max_frame_age:.2f}s)"
                self.last_step_stats["rejection_reason"] = self.last_rejection_reason
                logger.debug("Dropping frame: frame is stale (age=%.2fs > %.2fs)", now - float(frame_ts), max_frame_age)
                return

        # Frame-telemetry cross-synchronization skew check
        if telem_ts is not None and frame_ts is not None and float(telem_ts) > 0 and float(frame_ts) > 0:
            skew = abs(float(frame_ts) - float(telem_ts))
            if skew > max_sync_skew:
                self.last_rejection_reason = f"EXCESSIVE_SKEW (skew={skew:.2f}s > {max_sync_skew:.2f}s)"
                self.last_step_stats["rejection_reason"] = self.last_rejection_reason
                logger.debug("Dropping frame: excessive frame-telemetry skew (skew=%.2fs > %.2fs)", skew, max_sync_skew)
                return

        drone_lat = getattr(telem, "latitude", None)
        drone_lon = getattr(telem, "longitude", None)
        if drone_lat is None or drone_lon is None or (drone_lat == 0.0 and drone_lon == 0.0):
            self.last_rejection_reason = f"INVALID_LAT_LON_POST (lat={drone_lat}, lon={drone_lon})"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            return

        alt = float(getattr(telem, "altitude", 0.0) or 0.0)
        roll = float(getattr(telem, "roll", 0.0) or 0.0)
        pitch = float(getattr(telem, "pitch", 0.0) or 0.0)
        if abs(roll) > self.config.max_tilt_deg or abs(pitch) > self.config.max_tilt_deg:
            self.last_rejection_reason = f"TILT_EXCEEDED_POST (roll={roll:.1f}deg, pitch={pitch:.1f}deg)"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            return

        yaw = float(getattr(telem, "yaw", getattr(telem, "heading", 0.0)) or 0.0)

        # 4. Inference
        self.last_step_stats["reached_inference"] = True
        try:
            detections = await self.detector.detect(frame.image)
        except Exception as e:
            self.last_rejection_reason = f"INFERENCE_FAILED: {e}"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.error("Detector inference failed: %s", e)
            return

        self.last_step_stats["raw_detections"] = len(detections) if detections else 0
        now_curr = time.time()

        # Re-evaluate previously deferred detections buffered from recent frames
        recovered_from_buffer = self._process_deferred_buffer(now_curr)

        if not detections and not recovered_from_buffer:
            self.last_rejection_reason = "ZERO_DETECTIONS"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            # Fix ZERO_DETECTIONS path: aggregator still gets a prune/tick call with empty list
            self.aggregator.update([], now=frame.timestamp)
            return

        self._frame_id += 1
        h, w = frame.image.shape[:2]
        cam_cfg = self.config.camera

        # 5. Geolocation
        geo_dets: List[GeoreferencedDetection] = []
        if recovered_from_buffer:
            geo_dets.extend(recovered_from_buffer)

        for det_idx, det in enumerate(detections or []):
            trace_id = f"{self.drone_id}-f{self._frame_id}-d{det_idx}-{int(now_curr*1000)}"
            x1, y1, x2, y2 = det.bbox_xyxy
            bbox_list = [round(float(c), 2) for c in (x1, y1, x2, y2)]

            drone_gps_data = {
                "lat": drone_lat,
                "lon": drone_lon,
                "alt": alt,
                "roll": round(roll, 2),
                "pitch": round(pitch, 2),
                "yaw": round(yaw, 2)
            }

            if log_raw_detection:
                log_raw_detection(
                    drone_id=self.drone_id,
                    frame_id=self._frame_id,
                    det_idx=det_idx,
                    trace_id=trace_id,
                    bbox_xyxy=bbox_list,
                    confidence=round(float(det.confidence), 4),
                    label=det.label,
                    drone_gps=drone_gps_data
                )

            u = (x1 + x2) / 2.0
            v = (y1 + y2) / 2.0

            res = pixel_to_lat_lon(
                u=u,
                v=v,
                img_w=w,
                img_h=h,
                hfov_deg=cam_cfg.hfov_deg,
                mount_pitch_deg=cam_cfg.mount_pitch_deg,
                roll_deg=roll,
                pitch_deg=pitch,
                yaw_deg=yaw,
                drone_alt=alt,
                ground_elevation_m=self.config.ground_elevation_m,
                drone_lat=drone_lat,
                drone_lon=drone_lon,
                max_range_m=self.config.max_range_m,
            )
            if res is None:
                if log_peer_exclusion_eval:
                    log_peer_exclusion_eval(
                        drone_id=self.drone_id,
                        frame_id=self._frame_id,
                        det_idx=det_idx,
                        trace_id=trace_id,
                        projected_ground={"lat": None, "lon": None, "agl": None},
                        peer_registry_contents=list(self.swarm_manager.registry.peers.keys()) if (self.swarm_manager and hasattr(self.swarm_manager, "registry")) else [],
                        peers=[],
                        decision="REJECTED",
                        reason="GEOLOCATION_RAY_INTERSECTION_FAILED"
                    )
                continue

            glat, glon, agl = res

            # 3D Camera Ray in NED frame
            ray_ned = pixel_to_ray_ned(
                u=u,
                v=v,
                img_w=w,
                img_h=h,
                hfov_deg=cam_cfg.hfov_deg,
                mount_pitch_deg=cam_cfg.mount_pitch_deg,
                roll_deg=roll,
                pitch_deg=pitch,
                yaw_deg=yaw,
            )

            is_peer, decision, exclusion_reason, matched_peer_id, matched_dist, registry_contents, peers_eval = (
                self._evaluate_peer_exclusion(
                    glat=glat,
                    glon=glon,
                    agl=agl,
                    u=u,
                    v=v,
                    w=w,
                    h=h,
                    roll=roll,
                    pitch=pitch,
                    yaw=yaw,
                    drone_lat=drone_lat,
                    drone_lon=drone_lon,
                    alt=alt,
                    now_curr=now_curr,
                    ray_ned=ray_ned,
                )
            )

            if log_peer_exclusion_eval:
                log_peer_exclusion_eval(
                    drone_id=self.drone_id,
                    frame_id=self._frame_id,
                    det_idx=det_idx,
                    trace_id=trace_id,
                    projected_ground={"lat": glat, "lon": glon, "agl": agl},
                    peer_registry_contents=registry_contents,
                    peers=peers_eval,
                    decision=decision,
                    reason=exclusion_reason,
                    matched_peer_id=matched_peer_id,
                    matched_distance_m=round(matched_dist, 3) if matched_dist is not None else None,
                )

            if is_peer:
                logger.info(
                    "Excluding detection at (%.6f, %.6f): matches peer %s (dist=%.2fm, reason=%s)",
                    glat, glon, matched_peer_id, matched_dist or 0.0, exclusion_reason
                )
                continue

            if decision == "DEFERRED":
                buf_window = float(getattr(self.config, "deferred_buffer_window_s", 3.0))
                logger.warning(
                    "Deferring detection at (%.6f, %.6f): %s (buffered for %.1fs window)",
                    glat, glon, exclusion_reason, buf_window
                )
                self._deferred_buffer.append({
                    "det": det,
                    "glat": glat,
                    "glon": glon,
                    "agl": agl,
                    "u": u,
                    "v": v,
                    "w": w,
                    "h": h,
                    "roll": roll,
                    "pitch": pitch,
                    "yaw": yaw,
                    "drone_lat": drone_lat,
                    "drone_lon": drone_lon,
                    "alt": alt,
                    "deadline": now_curr + buf_window,
                })
                continue

            geo_dets.append(
                GeoreferencedDetection(
                    label=det.label,
                    confidence=det.confidence,
                    lat=glat,
                    lon=glon,
                    altitude_agl=agl,
                    drone_lat=drone_lat,
                    drone_lon=drone_lon,
                )
            )

        self.last_step_stats["geo_detections"] = len(geo_dets)
        if not geo_dets:
            if any(item.get("deadline") for item in self._deferred_buffer):
                self.last_rejection_reason = "DETECTIONS_DEFERRED"
            else:
                self.last_rejection_reason = "GEOLOCATION_ALL_FAILED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            # Tick aggregator with empty list so unconfirmed/stale tracks are pruned
            self.aggregator.update([], now=frame.timestamp)
            return

        # 6. Aggregation
        emitted_items = self.aggregator.update(geo_dets, now=frame.timestamp)
        self.last_step_stats["emitted_items"] = len(emitted_items) if emitted_items else 0
        if not emitted_items:
            self.last_rejection_reason = "AGGREGATION_NO_EMISSION"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            return

        self.last_rejection_reason = None
        self.last_step_stats["rejection_reason"] = None

        # 7. Chunk and Broadcast (<= 10 items per message)
        is_synthetic = bool(getattr(self.frame_source, "is_synthetic", False))
        chunk_size = 10

        for i in range(0, len(emitted_items), chunk_size):
            chunk = emitted_items[i : i + chunk_size]
            items = [
                DetectionItem(
                    id=it.id,
                    label=it.label,
                    confidence=round(it.confidence, 4),
                    lat=round(it.lat, 7),
                    lon=round(it.lon, 7),
                    hits=it.hits,
                    status=it.status,
                    altitude_agl=round(it.altitude_agl, 2) if it.altitude_agl is not None else None,
                )
                for it in chunk
            ]
            msg = DetectionMessage(
                sender_id=self.drone_id,
                timestamp=time.time(),
                detections=items,
                synthetic=is_synthetic,
                frame_id=self._frame_id,
                drone_session_id=self.session_id,
                session_generation=self.session_generation,
            )
            try:
                await self.network.broadcast_message(msg)
            except Exception as e:
                logger.warning("Failed to broadcast detection message: %s", e)

    def _evaluate_peer_exclusion(
        self,
        glat: float,
        glon: float,
        agl: float,
        u: float,
        v: float,
        w: int,
        h: int,
        roll: float,
        pitch: float,
        yaw: float,
        drone_lat: float,
        drone_lon: float,
        alt: float,
        now_curr: float,
        ray_ned: Optional[Tuple[float, float, float]] = None,
    ) -> Tuple[bool, str, str, Optional[str], Optional[float], List[str], List[Dict[str, Any]]]:
        """
        Evaluate candidate ground detection against active peer drones.
        Enforces configurable 3D exclusion volume, ignores inactive/dead peers,
        and uses separate peer_position_max_age_s for staleness gating.
        """
        cam_cfg = self.config.camera
        if ray_ned is None:
            ray_ned = pixel_to_ray_ned(
                u=u,
                v=v,
                img_w=w,
                img_h=h,
                hfov_deg=cam_cfg.hfov_deg,
                mount_pitch_deg=cam_cfg.mount_pitch_deg,
                roll_deg=roll,
                pitch_deg=pitch,
                yaw_deg=yaw,
            )

        registry_contents: List[str] = []
        peers_eval: List[Dict[str, Any]] = []
        is_peer = False
        matched_peer_id = None
        matched_dist = None
        exclusion_reason = "ACCEPTED_GENUINE_TARGET"
        decision = "ACCEPTED"

        max_3d_cap = getattr(self.config, "peer_exclusion_max_3d_radius_m", None)
        if max_3d_cap is not None and isinstance(max_3d_cap, (int, float)):
            exclusion_radius_3d = min(float(self.peer_exclusion_radius_m), float(max_3d_cap))
        else:
            exclusion_radius_3d = float(self.peer_exclusion_radius_m)

        unpositioned_or_stale_peers: List[str] = []

        if self.swarm_manager and hasattr(self.swarm_manager, "registry"):
            registry_contents = list(self.swarm_manager.registry.peers.keys())
            for peer in self.swarm_manager.registry.peers.values():
                p_id = getattr(peer, "drone_id", "unknown")
                p_active = getattr(peer, "is_active", True)
                p_lat = getattr(peer, "lat", None)
                p_lon = getattr(peer, "lon", None)
                p_alt = getattr(peer, "alt", 0.0) or 0.0
                last_pos_time = getattr(
                    peer,
                    "last_position_time",
                    getattr(peer, "last_pos", getattr(peer, "last_seen", getattr(peer, "timestamp", None)))
                )
                pos_age = (now_curr - float(last_pos_time)) if (last_pos_time is not None and isinstance(last_pos_time, (int, float))) else None
                max_age = float(getattr(self.config, "peer_position_max_age_s", 5.0))
                has_pos = (p_lat is not None and p_lon is not None and isinstance(p_lat, (int, float)) and isinstance(p_lon, (int, float)))
                is_stale = (pos_age is not None and pos_age > max_age)

                # Requirement 7: Only defer when an *active* peer's position is missing/stale.
                # Inactive/dead peers never trigger deferral.
                if p_active and (not has_pos or is_stale):
                    unpositioned_or_stale_peers.append(p_id)

                # Inactive peers without fresh valid positions cannot match and are skipped
                if not p_active and (not has_pos or is_stale):
                    continue

                dist_3d_perp = None
                s_depth = None
                eligible = has_pos and not is_stale

                if has_pos and ray_ned is not None:
                    p_dn, p_de = lat_lon_to_meters(p_lat, p_lon, drone_lat, drone_lon)
                    p_dd = -(p_alt - alt)
                    s_depth, dist_3d_perp = compute_ray_to_point_cpa(ray_ned, (p_dn, p_de, p_dd))

                peer_summary = {
                    "peer_id": p_id,
                    "is_active": p_active,
                    "lat": p_lat,
                    "lon": p_lon,
                    "alt": p_alt,
                    "has_position": has_pos,
                    "last_position_time": last_pos_time,
                    "position_age_s": round(pos_age, 3) if pos_age is not None else None,
                    "is_stale": is_stale,
                    "distance_m": round(dist_3d_perp, 3) if dist_3d_perp is not None else None,
                    "depth_s_m": round(s_depth, 3) if s_depth is not None else None,
                    "eligible": eligible,
                }
                peers_eval.append(peer_summary)

                if eligible and dist_3d_perp is not None:
                    if s_depth > 0.0:
                        is_airborne = (p_alt > (self.config.ground_elevation_m + 1.0))
                        if is_airborne:
                            if dist_3d_perp < exclusion_radius_3d:
                                is_peer = True
                                matched_peer_id = p_id
                                matched_dist = dist_3d_perp
                                exclusion_reason = (
                                    f"REJECTED_PEER_EXCLUSION_MATCH_3D (peer={p_id}, "
                                    f"d_perp={dist_3d_perp:.2f}m < {exclusion_radius_3d:.2f}m, "
                                    f"depth={s_depth:.1f}m, alt={p_alt:.1f}m)"
                                )
                                decision = "REJECTED"
                                break
                        else:
                            dn_g, de_g = lat_lon_to_meters(glat, glon, p_lat, p_lon)
                            d_ground = math.hypot(dn_g, de_g)
                            if d_ground < exclusion_radius_3d:
                                is_peer = True
                                matched_peer_id = p_id
                                matched_dist = d_ground
                                exclusion_reason = (
                                    f"REJECTED_LANDED_PEER_MATCH (peer={p_id}, "
                                    f"dist_ground={d_ground:.2f}m < {exclusion_radius_3d:.2f}m)"
                                )
                                decision = "REJECTED"
                                break

            if not is_peer:
                if unpositioned_or_stale_peers:
                    decision = "DEFERRED"
                    exclusion_reason = f"DEFERRED_UNCERTAIN_PEER_TELEMETRY (stale_or_missing={unpositioned_or_stale_peers})"
                elif not registry_contents:
                    exclusion_reason = "ACCEPTED_REGISTRY_EMPTY"
                    decision = "ACCEPTED"
                else:
                    exclusion_reason = "ACCEPTED_NO_PEER_MATCH"
                    decision = "ACCEPTED"
        else:
            exclusion_reason = "ACCEPTED_NO_SWARM_MANAGER"
            decision = "ACCEPTED"

        return is_peer, decision, exclusion_reason, matched_peer_id, matched_dist, registry_contents, peers_eval

    def _process_deferred_buffer(self, now_curr: float) -> List[GeoreferencedDetection]:
        """
        Re-evaluates buffered deferred detections against newly arrived peer positions.
        Returns newly confirmed detections whose peer uncertainty cleared.
        """
        accepted: List[GeoreferencedDetection] = []
        still_buffered: List[Dict[str, Any]] = []

        for item in self._deferred_buffer:
            if now_curr > item["deadline"]:
                logger.debug(
                    "Deferred detection at (%.6f, %.6f) buffer window expired without peer telemetry; dropping.",
                    item["glat"], item["glon"],
                )
                continue

            is_peer, decision, reason, _, _, _, _ = self._evaluate_peer_exclusion(
                glat=item["glat"],
                glon=item["glon"],
                agl=item["agl"],
                u=item["u"],
                v=item["v"],
                w=item["w"],
                h=item["h"],
                roll=item["roll"],
                pitch=item["pitch"],
                yaw=item["yaw"],
                drone_lat=item["drone_lat"],
                drone_lon=item["drone_lon"],
                alt=item["alt"],
                now_curr=now_curr,
            )

            if is_peer:
                logger.info("Deferred detection matches peer %s upon telemetry update; excluding.", reason)
            elif decision == "ACCEPTED":
                logger.info(
                    "Deferred detection at (%.6f, %.6f) confirmed after peer telemetry update.",
                    item["glat"], item["glon"],
                )
                accepted.append(
                    GeoreferencedDetection(
                        label=item["det"].label,
                        confidence=item["det"].confidence,
                        lat=item["glat"],
                        lon=item["glon"],
                        altitude_agl=item["agl"],
                        drone_lat=item["drone_lat"],
                        drone_lon=item["drone_lon"],
                    )
                )
            else:
                still_buffered.append(item)

        self._deferred_buffer = still_buffered
        return accepted

