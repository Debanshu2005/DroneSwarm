"""Perception processing pipeline: captures, detects, geolocates, aggregates, and broadcasts."""

import asyncio
import math
import time
from typing import List, Optional

from DroneOS2.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS2.perception.detector import GarbageDetector
from DroneOS2.perception.frame_sources import FrameSource
from DroneOS2.perception.geolocate import pixel_to_lat_lon
from DroneOS2.shared.protocol.messages import DetectionItem, DetectionMessage
from DroneOS2.shared.utils.logger import setup_logger

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
    ):
        self.drone_id = drone_id
        self.frame_source = frame_source
        self.detector = detector
        self.fc = flight_controller
        self.network = network_adapter
        self.config = config

        self.aggregator = TrackAggregator(
            drone_id=drone_id,
            merge_radius_m=getattr(config, "merge_radius_m", 2.5),
            min_hits=getattr(config, "min_hits", 2),
            update_interval_s=getattr(config, "update_interval_s", 10.0),
            max_tracks=getattr(config, "max_tracks", 2000),
        )

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._frame_id = 0
        self._disabled_by_error = False
        self.last_rejection_reason: Optional[str] = None
        self.last_step_stats: dict = {}

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
                logger.error("Perception detector failed to load (missing weights or ultralytics); stopping perception loop.")
                self._load_failed_logged = True
            self._running = False
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
        if not detections:
            self.last_rejection_reason = "ZERO_DETECTIONS"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            return

        self._frame_id += 1
        h, w = frame.image.shape[:2]
        cam_cfg = self.config.camera

        # 5. Geolocation
        geo_dets: List[GeoreferencedDetection] = []
        for det in detections:
            x1, y1, x2, y2 = det.bbox_xyxy
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
            if res is not None:
                glat, glon, agl = res
                geo_dets.append(
                    GeoreferencedDetection(
                        label=det.label,
                        confidence=det.confidence,
                        lat=glat,
                        lon=glon,
                        altitude_agl=agl,
                    )
                )

        self.last_step_stats["geo_detections"] = len(geo_dets)
        if not geo_dets:
            self.last_rejection_reason = "GEOLOCATION_ALL_FAILED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
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
            )
            try:
                await self.network.broadcast_message(msg)
            except Exception as e:
                logger.warning("Failed to broadcast detection message: %s", e)

