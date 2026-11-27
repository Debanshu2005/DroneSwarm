"""Perception processing pipeline: captures, detects, geolocates, aggregates, and broadcasts."""

import asyncio
import math
import time
from typing import Dict, List, Optional, Any, Tuple

from DroneOS.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.frame_sources import FrameSource
from DroneOS.perception.types import Frame
from DroneOS.perception.geolocate import pixel_to_lat_lon, lat_lon_to_meters, meters_to_lat_lon, pixel_to_ray_ned, compute_ray_to_point_cpa
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage
from DroneOS.shared.utils.logger import setup_logger

try:
    from DroneOS.perception.diagnostics import log_raw_detection, log_peer_exclusion_eval
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

        cam_cfg_init = getattr(config, "camera", None)
        raw_hfov = getattr(cam_cfg_init, "hfov_deg", 90.0)
        cam_hfov = float(raw_hfov) if isinstance(raw_hfov, (int, float)) else 90.0
        raw_mp = getattr(cam_cfg_init, "mount_pitch_deg", 90.0)
        cam_mount_pitch = float(raw_mp) if isinstance(raw_mp, (int, float)) else 90.0
        raw_mode = getattr(config, "vehicle_artifact_mode", "auto")
        artifact_mode = raw_mode.strip().lower() if isinstance(raw_mode, str) else "auto"
        raw_tol = getattr(config, "vehicle_artifact_pixel_tol_px", 50.0)
        artifact_tol_px = float(raw_tol) if isinstance(raw_tol, (int, float)) else 50.0
        raw_ratio = getattr(config, "vehicle_artifact_shift_ratio", 0.25)
        artifact_shift_ratio = float(raw_ratio) if isinstance(raw_ratio, (int, float)) else 0.25
        raw_floor = getattr(config, "vehicle_artifact_pixel_floor_px", 8.0)
        artifact_floor_px = float(raw_floor) if isinstance(raw_floor, (int, float)) else 8.0
        self.aggregator = TrackAggregator(
            drone_id=drone_id,
            merge_radius_m=getattr(config, "merge_radius_m", 2.5),
            min_hits=getattr(config, "min_hits", 2),
            update_interval_s=getattr(config, "update_interval_s", 10.0),
            max_tracks=getattr(config, "max_tracks", 2000),
            min_temporal_separation_s=getattr(config, "min_temporal_separation_s", 2.0),
            min_drone_displacement_m=getattr(config, "min_drone_displacement_m", 1.5),
            gps_sigma_m=getattr(config, "gps_sigma_m", 0.5),
            vehicle_artifact_pixel_tol_px=artifact_tol_px,
            vehicle_artifact_shift_ratio=artifact_shift_ratio,
            vehicle_artifact_pixel_floor_px=artifact_floor_px,
            hfov_deg=cam_hfov,
            mount_pitch_deg=cam_mount_pitch,
            vehicle_artifact_mode=artifact_mode,
        )
        if artifact_mode == "off":
            logger.info("[%s] Vehicle-artifact rejection active mode=off (disabled)", drone_id)
        elif artifact_mode == "fixed":
            logger.info(
                "[%s] Vehicle-artifact rejection active mode=fixed (fixed_tol=%.1fpx)",
                drone_id,
                artifact_tol_px,
            )
        else:
            mount_offset_deg = abs(cam_mount_pitch - 90.0)
            if mount_offset_deg <= 10.0:
                logger.info(
                    "[%s] Vehicle-artifact rejection active mode=auto (nadir expected-shift, mount_pitch=%.1fdeg, shift_ratio=%.2f, floor=%.1fpx, fallback=%.1fpx)",
                    drone_id,
                    cam_mount_pitch,
                    artifact_shift_ratio,
                    artifact_floor_px,
                    artifact_tol_px,
                )
            else:
                logger.info(
                    "[%s] Vehicle-artifact rejection active mode=auto (oblique mount_pitch=%.1fdeg, offset=%.1fdeg > 10deg -> fixed-pixel fallback=%.1fpx)",
                    drone_id,
                    cam_mount_pitch,
                    mount_offset_deg,
                    artifact_tol_px,
                )

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._frame_id = 0
        self._disabled_by_error = False
        self.last_rejection_reason: Optional[str] = None
        self.last_step_stats: dict = {}
        self._deferred_buffer: List[Dict[str, Any]] = []

        # Decoupled frame capture state (runs capture in dedicated task; keeps latest frame only)
        self.decoupled_capture = bool(getattr(config, "decoupled_capture", True))
        self._capture_running: bool = False
        self._capture_task: Optional[asyncio.Task] = None
        self._latest_frame: Optional[Frame] = None
        self._latest_telem: Optional[Any] = None
        self._latest_frame_id: int = 0
        self._last_processed_frame_id: int = -1
        self._new_frame_event: asyncio.Event = asyncio.Event()

        # First-detection diagnostic timeline state (first 60s)
        self._pipeline_start_time: Optional[float] = None
        self._diag_first_inference_s: Optional[float] = None
        self._diag_first_raw_detection_s: Optional[float] = None
        self._diag_first_geo_detection_s: Optional[float] = None
        self._diag_first_confirmed_alert_s: Optional[float] = None
        self._diag_peers_discovered_s: Optional[float] = None
        self._diag_rejection_histogram: Dict[str, int] = {}
        self._diag_summary_printed: bool = False

        # Profiling window metrics (~5 s periodic reporting)
        self._profile_window_start = time.monotonic()
        self._profile_frames = 0
        self._profile_sim_get_images_ms: List[float] = []
        self._profile_capture_ms: List[float] = []
        self._profile_preprocess_ms: List[float] = []
        self._profile_inference_ms: List[float] = []
        self._profile_postprocess_ms: List[float] = []
        self._profile_tracker_ms: List[float] = []
        self._profile_e2e_ms: List[float] = []
        self._profile_max_raw_conf = 0.0

    def _record_profile_sample(
        self,
        sim_get_images_ms: float,
        capture_ms: float,
        preprocess_ms: float,
        inference_ms: float,
        postprocess_ms: float,
        tracker_ms: float,
        e2e_ms: float,
        raw_conf: float,
    ) -> None:
        """Record timing metrics for one processed frame and log summary every ~5 seconds."""
        self._profile_frames += 1
        self._profile_sim_get_images_ms.append(sim_get_images_ms)
        self._profile_capture_ms.append(capture_ms)
        self._profile_preprocess_ms.append(preprocess_ms)
        self._profile_inference_ms.append(inference_ms)
        self._profile_postprocess_ms.append(postprocess_ms)
        self._profile_tracker_ms.append(tracker_ms)
        self._profile_e2e_ms.append(e2e_ms)
        if raw_conf > self._profile_max_raw_conf:
            self._profile_max_raw_conf = raw_conf

        now_m = time.monotonic()
        dt = now_m - self._profile_window_start
        if dt >= 5.0 and self._profile_frames > 0:
            mean_sim = sum(self._profile_sim_get_images_ms) / len(self._profile_sim_get_images_ms)
            mean_cap = sum(self._profile_capture_ms) / len(self._profile_capture_ms)
            mean_pre = sum(self._profile_preprocess_ms) / len(self._profile_preprocess_ms)
            mean_inf = sum(self._profile_inference_ms) / len(self._profile_inference_ms)
            mean_post = sum(self._profile_postprocess_ms) / len(self._profile_postprocess_ms)
            mean_trk = sum(self._profile_tracker_ms) / len(self._profile_tracker_ms)
            mean_e2e = sum(self._profile_e2e_ms) / len(self._profile_e2e_ms)
            fps = self._profile_frames / dt

            logger.info(
                "[%s][PROFILE ~5s] e2e=%.1fms (effective_fps=%.2f) | capture=%.1fms (simGetImages=%.1fms) | "
                "preprocess=%.1fms | inference=%.1fms | postprocess+filter=%.1fms | tracker=%.1fms | "
                "max_raw_conf=%.4f (frames=%d)",
                self.drone_id,
                mean_e2e,
                fps,
                mean_cap,
                mean_sim,
                mean_pre,
                mean_inf,
                mean_post,
                mean_trk,
                self._profile_max_raw_conf,
                self._profile_frames,
            )

            self._profile_window_start = now_m
            self._profile_frames = 0
            self._profile_sim_get_images_ms.clear()
            self._profile_capture_ms.clear()
            self._profile_preprocess_ms.clear()
            self._profile_inference_ms.clear()
            self._profile_postprocess_ms.clear()
            self._profile_tracker_ms.clear()
            self._profile_e2e_ms.clear()
            self._profile_max_raw_conf = 0.0

    def _flush_confirmed_alerts(self) -> None:
        """Log any newly confirmed track events with frames and latency to confirm."""
        if hasattr(self.aggregator, "last_confirmed_events"):
            while self.aggregator.last_confirmed_events:
                evt = self.aggregator.last_confirmed_events.pop(0)
                if self._diag_first_confirmed_alert_s is None and self._pipeline_start_time is not None:
                    self._diag_first_confirmed_alert_s = round(time.monotonic() - self._pipeline_start_time, 2)
                logger.info(
                    "[%s][ALERT CONFIRMED] id=%s label=%s conf=%.3f lat=%.6f lon=%.6f | "
                    "confirmed in %d frames, %.2f s",
                    self.drone_id,
                    evt.get("id"),
                    evt.get("label"),
                    evt.get("confidence", 0.0),
                    evt.get("lat", 0.0),
                    evt.get("lon", 0.0),
                    evt.get("frames", 0),
                    evt.get("seconds", 0.0),
                )

    def _record_step_timeline(
        self,
        ready_state: str,
        alt_gate: str = "N/A",
        tilt_gate: str = "N/A",
        frame_age_s: Optional[float] = None,
        telem_age_s: Optional[float] = None,
        sync_skew_s: Optional[float] = None,
        rejection_reason: Optional[str] = None,
        raw_detections: int = 0,
        max_raw_conf: float = 0.0,
        deferred: bool = False,
        defer_reason: str = "none",
    ) -> None:
        """Record and log per-step timeline diagnostics during the first 60 seconds."""
        if self._pipeline_start_time is None:
            self._pipeline_start_time = time.monotonic()
        now_m = time.monotonic()
        elapsed = now_m - self._pipeline_start_time

        # Track peer discovery time
        if self._diag_peers_discovered_s is None and self.swarm_manager and hasattr(self.swarm_manager, "registry"):
            active_peers = list(self.swarm_manager.registry.peers.keys())
            if active_peers:
                self._diag_peers_discovered_s = round(elapsed, 2)
                logger.info(
                    "[%s][TIMELINE PEER DISCOVERY] peers discovered at T+%.2fs: %s",
                    self.drone_id,
                    self._diag_peers_discovered_s,
                    active_peers,
                )

        reason = rejection_reason or "NONE"
        self._diag_rejection_histogram[reason] = self._diag_rejection_histogram.get(reason, 0) + 1

        # Check aggregator tracks
        max_hits = 0
        min_hits_met = False
        sep_met = False
        disp_met = False
        if hasattr(self, "aggregator") and hasattr(self.aggregator, "tracks") and isinstance(self.aggregator.tracks, dict):
            for trk in self.aggregator.tracks.values():
                h = getattr(trk, "hits", 0)
                if isinstance(h, int) and h > max_hits:
                    max_hits = h
                dh = getattr(trk, "distinct_hits", 0)
                req_h = getattr(self.aggregator, "min_hits", 2)
                if isinstance(dh, int) and isinstance(req_h, int) and dh >= req_h:
                    min_hits_met = True
                min_sep = getattr(self.aggregator, "min_temporal_separation_s", 2.0)
                hit_t = getattr(trk, "last_distinct_hit_time", 0.0)
                seen_t = getattr(trk, "first_seen", 0.0)
                if isinstance(min_sep, (int, float)) and isinstance(hit_t, (int, float)) and isinstance(seen_t, (int, float)):
                    if (hit_t - seen_t) >= min_sep:
                        sep_met = True
                if getattr(trk, "last_distinct_drone_lat", None) is not None and getattr(trk, "last_drone_lat", None) is not None:
                    disp_met = True

        if elapsed <= 60.0:
            f_age_str = f"{frame_age_s:.2f}s" if frame_age_s is not None else "N/A"
            t_age_str = f"{telem_age_s:.2f}s" if telem_age_s is not None else "N/A"
            skew_str = f"{sync_skew_s:.2f}s" if sync_skew_s is not None else "N/A"
            logger.info(
                "[%s][TIMELINE T+%.2fs] state=%s alt_gate=%s tilt_gate=%s frame_age=%s telem_age=%s "
                "skew=%s reason=%s raw_dets=%d max_conf=%.4f deferred=%s(%s) hits=%d "
                "criteria=(hits:%s,sep:%s,disp:%s)",
                self.drone_id,
                elapsed,
                ready_state,
                alt_gate,
                tilt_gate,
                f_age_str,
                t_age_str,
                skew_str,
                reason,
                raw_detections,
                max_raw_conf,
                deferred,
                defer_reason,
                max_hits,
                min_hits_met,
                sep_met,
                disp_met,
            )

        if elapsed >= 60.0 and not self._diag_summary_printed:
            self._print_timeline_summary()

    def _print_timeline_summary(self) -> None:
        """Print summary of first-detection events and rejection histogram for the first 60s."""
        if self._diag_summary_printed:
            return
        self._diag_summary_printed = True
        t_inf = f"T+{self._diag_first_inference_s:.2f}s" if self._diag_first_inference_s is not None else "NEVER"
        t_raw = f"T+{self._diag_first_raw_detection_s:.2f}s" if self._diag_first_raw_detection_s is not None else "NEVER"
        t_geo = f"T+{self._diag_first_geo_detection_s:.2f}s" if self._diag_first_geo_detection_s is not None else "NEVER"
        t_conf = f"T+{self._diag_first_confirmed_alert_s:.2f}s" if self._diag_first_confirmed_alert_s is not None else "NEVER"
        t_peer = f"T+{self._diag_peers_discovered_s:.2f}s" if self._diag_peers_discovered_s is not None else "NONE"

        logger.info(
            "[%s][TIMELINE SUMMARY 60s] first inference at %s, first raw detection at %s, "
            "first geolocated detection at %s, first confirmed alert at %s, peers discovered at %s | "
            "rejection histogram: %s",
            self.drone_id,
            t_inf,
            t_raw,
            t_geo,
            t_conf,
            t_peer,
            dict(self._diag_rejection_histogram),
        )

    async def _capture_loop(self) -> None:
        """
        Dedicated background capture task. Continuously acquires frames from frame_source.
        Overwrites self._latest_frame and self._latest_telem with the most recently captured
        frame and its corresponding telemetry sampled at capture time, immediately dropping older
        frames without queueing.
        """
        while self._capture_running:
            try:
                # Do not capture frames if detector is degraded or failed to load
                if self.last_rejection_reason == "DETECTOR_NOT_LOADED":
                    await asyncio.sleep(0.05)
                    continue

                frame = await self.frame_source.get_frame()
                if frame is not None and getattr(frame, "image", None) is not None:
                    # Sample telemetry immediately at capture time to pair pose directly with the frame
                    telem = await self.fc.get_telemetry()
                    self._latest_frame = frame
                    self._latest_telem = telem
                    self._latest_frame_id += 1
                    self._new_frame_event.set()
                    # Cooperative yield so fast/mocked frame sources do not starve the event loop
                    await asyncio.sleep(0.002)
                else:
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[%s] Error in decoupled capture loop: %s", self.drone_id, e)
                await asyncio.sleep(0.02)

    def start_capture(self) -> None:
        """Start decoupled background capture task if not already running."""
        self._capture_running = True
        if self._capture_task is None or self._capture_task.done():
            self._capture_task = asyncio.create_task(self._capture_loop())

    async def stop_capture(self) -> None:
        """Stop decoupled background capture task."""
        self._capture_running = False
        if self._capture_task is not None and not self._capture_task.done():
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
        self._capture_task = None

    async def start(self) -> None:
        """Start perception loop as an isolated asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self.run_loop())

    async def stop(self, close_detector: bool = True, close_source: bool = True) -> None:
        """Stop perception pipeline and release resources."""
        self._running = False
        await self.stop_capture()
        self._latest_frame = None
        self._latest_telem = None
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if close_source:
            await self.frame_source.close()
        if close_detector:
            self.detector.close()
        self._print_timeline_summary()
        logger.info("Perception pipeline stopped.")

    async def run_loop(self) -> None:
        """Main periodic perception processing loop."""
        self._running = True
        logger.info("Perception pipeline loop started on %s (max_fps=%.1f, decoupled=%s).", self.drone_id, self.config.max_fps, self.decoupled_capture)

        # Stagger startup by drone index to avoid synchronized inference CPU bursts across drones
        import re
        drone_idx = 0
        m = re.search(r'\d+', str(self.drone_id))
        if m:
            drone_idx = max(0, int(m.group(0)) - 1)
        start_stagger_cfg = float(getattr(self.config, "start_stagger_s", 0.25))
        stagger_s = drone_idx * start_stagger_cfg
        if stagger_s > 0:
            try:
                await asyncio.sleep(stagger_s)
            except asyncio.CancelledError:
                return

        if self._pipeline_start_time is None:
            self._pipeline_start_time = time.monotonic()

        # Warm up detector in executor thread before entering periodic step loop
        warmup_frames = int(getattr(self.config, "warmup_frames", 3))
        loaded = True
        if hasattr(self.detector, "ensure_ready") and callable(self.detector.ensure_ready):
            res = self.detector.ensure_ready(warmup_frames=warmup_frames)
            if asyncio.iscoroutine(res):
                loaded = await res
            else:
                loaded = res
        elif hasattr(self.detector, "ensure_loaded") and callable(self.detector.ensure_loaded):
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
        elif self.decoupled_capture:
            self.start_capture()

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
        if self._pipeline_start_time is None:
            self._pipeline_start_time = time.monotonic()
        self._current_step_info = {
            "ready_state": "OK",
            "alt_gate": "N/A",
            "tilt_gate": "N/A",
            "frame_age_s": None,
            "telem_age_s": None,
            "sync_skew_s": None,
            "raw_detections": 0,
            "max_raw_conf": 0.0,
            "deferred": False,
            "defer_reason": "none",
        }
        try:
            await self._execute_step()
        finally:
            self._record_step_timeline(
                ready_state=self._current_step_info.get("ready_state", "OK"),
                alt_gate=self._current_step_info.get("alt_gate", "N/A"),
                tilt_gate=self._current_step_info.get("tilt_gate", "N/A"),
                frame_age_s=self._current_step_info.get("frame_age_s"),
                telem_age_s=self._current_step_info.get("telem_age_s"),
                sync_skew_s=self._current_step_info.get("sync_skew_s"),
                rejection_reason=self.last_rejection_reason,
                raw_detections=self._current_step_info.get("raw_detections", 0),
                max_raw_conf=self._current_step_info.get("max_raw_conf", 0.0),
                deferred=self._current_step_info.get("deferred", False),
                defer_reason=self._current_step_info.get("defer_reason", "none"),
            )

    async def _execute_step(self) -> None:
        t_step_start = time.perf_counter()
        self.last_rejection_reason = None
        self.last_step_stats = {
            "reached_camera": False,
            "reached_inference": False,
            "raw_detections": 0,
            "geo_detections": 0,
            "emitted_items": 0,
            "rejection_reason": None,
        }

        # 1. Warm-up / readiness check: do not acquire camera frames or request telemetry while warming up
        if hasattr(self.detector, "get_status") and callable(self.detector.get_status):
            try:
                res = self.detector.get_status()
                if asyncio.iscoroutine(res):
                    res = await res
                if isinstance(res, tuple) and len(res) >= 1 and isinstance(res[0], str):
                    self._current_step_info["ready_state"] = res[0]
                    if res[0] == "WARMING_UP":
                        self.last_rejection_reason = "DETECTOR_WARMING_UP"
                        self.last_step_stats["rejection_reason"] = self.last_rejection_reason
                        return
            except Exception:
                pass

        # 2. Detector load check (asynchronous to avoid freezing main asyncio loop)
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
        elif hasattr(self.detector, "_available"):
            loaded = bool(self.detector._available)

        if loaded is False:
            self.last_rejection_reason = "DETECTOR_NOT_LOADED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            self._current_step_info["ready_state"] = "DEGRADED"
            if not getattr(self, "_load_failed_logged", False):
                logger.error("Perception detector not loaded; retrying with backoff.")
                self._load_failed_logged = True
            return

        # Check readiness state
        if hasattr(self.detector, "get_status") and callable(self.detector.get_status):
            try:
                res = self.detector.get_status()
                if asyncio.iscoroutine(res):
                    res = await res
                if isinstance(res, tuple) and len(res) >= 1 and isinstance(res[0], str):
                    self._current_step_info["ready_state"] = res[0]
                    if res[0] != "OK" and not getattr(self.detector, "_available", False):
                        self.last_rejection_reason = "DETECTOR_NOT_LOADED"
                        self.last_step_stats["rejection_reason"] = self.last_rejection_reason
                        return
            except Exception:
                pass
        self._current_step_info["ready_state"] = "OK"

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
        self._current_step_info["alt_gate"] = "PASS" if alt >= self.config.min_altitude_m else f"FAIL(alt={alt:.1f}m<{self.config.min_altitude_m:.1f}m)"
        if alt < self.config.min_altitude_m:
            self.last_rejection_reason = f"ALTITUDE_BELOW_MINIMUM (alt={alt:.2f}m < {self.config.min_altitude_m:.2f}m)"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Altitude %.2fm below min %.2fm", alt, self.config.min_altitude_m)
            return

        # Tilt angle limits (|roll|, |pitch| <= max_tilt_deg)
        roll = float(getattr(pre_telem, "roll", 0.0) or 0.0)
        pitch = float(getattr(pre_telem, "pitch", 0.0) or 0.0)
        self._current_step_info["tilt_gate"] = "PASS" if (abs(roll) <= self.config.max_tilt_deg and abs(pitch) <= self.config.max_tilt_deg) else f"FAIL(roll={roll:.1f},pitch={pitch:.1f})"
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

        # 3. Acquire frame and capture-time telemetry (decoupled latest-frame-only if background capture running, else direct)
        if self._capture_task is not None and not self._capture_task.done():
            if self._latest_frame is None or self._last_processed_frame_id == self._latest_frame_id:
                try:
                    await asyncio.wait_for(self._new_frame_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    frame = self._latest_frame
                    telem = self._latest_telem
                else:
                    frame = self._latest_frame
                    telem = self._latest_telem
            else:
                frame = self._latest_frame
                telem = self._latest_telem
            self._last_processed_frame_id = self._latest_frame_id
            self._new_frame_event.clear()
        else:
            frame = await self.frame_source.get_frame()
            telem = await self.fc.get_telemetry()
        if frame is None or frame.image is None:
            self.last_rejection_reason = "FRAME_ACQUISITION_FAILED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.debug("Gate reject: Camera frame acquisition returned None")
            return

        self.last_step_stats["reached_camera"] = True
        self._frame_id += 1
        frame_meta = getattr(frame, "metadata", {})
        if not isinstance(frame_meta, dict):
            frame_meta = {}
        sim_get_images_ms = float(frame_meta.get("sim_get_images_ms", 0.0) or 0.0)
        capture_ms = float(frame_meta.get("capture_ms", 0.0) or 0.0)

        # 4. Telemetry snapshot validation (sampled at capture time for accurate geolocation)
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

        if telem_ts is not None and float(telem_ts) > 1_000_000_000:
            self._current_step_info["telem_age_s"] = now - float(telem_ts)
        if frame_ts is not None and float(frame_ts) > 1_000_000_000:
            self._current_step_info["frame_age_s"] = now - float(frame_ts)
        if telem_ts is not None and frame_ts is not None and float(telem_ts) > 0 and float(frame_ts) > 0:
            self._current_step_info["sync_skew_s"] = abs(float(frame_ts) - float(telem_ts))

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
        t_infer_start = time.perf_counter()
        try:
            detections = await self.detector.detect(frame.image)
        except Exception as e:
            self.last_rejection_reason = f"INFERENCE_FAILED: {e}"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            logger.error("Detector inference failed: %s", e)
            return

        timing = getattr(self.detector, "last_timing", {})
        if not isinstance(timing, dict):
            timing = {}
        preprocess_ms = float(timing.get("preprocess_ms", 0.0) or 0.0)
        inference_ms = float(timing.get("inference_ms", 0.0) or 0.0)
        postprocess_ms = float(timing.get("postprocess_filter_ms", 0.0) or 0.0)
        raw_conf = float(timing.get("max_raw_conf", 0.0) or 0.0)

        self._current_step_info["raw_detections"] = len(detections) if detections else 0
        self._current_step_info["max_raw_conf"] = raw_conf
        if self._diag_first_inference_s is None and self._pipeline_start_time is not None:
            self._diag_first_inference_s = round(time.monotonic() - self._pipeline_start_time, 2)
        if detections and self._diag_first_raw_detection_s is None and self._pipeline_start_time is not None:
            self._diag_first_raw_detection_s = round(time.monotonic() - self._pipeline_start_time, 2)

        infer_latency_ms = (time.perf_counter() - t_infer_start) * 1000.0
        self.last_step_stats["inference_latency_ms"] = round(infer_latency_ms, 2)
        logger.debug("[%s] Inference latency: %.2f ms (raw: %d)", self.drone_id, infer_latency_ms, len(detections) if detections else 0)

        self.last_step_stats["raw_detections"] = len(detections) if detections else 0
        now_curr = time.time()

        # Re-evaluate previously deferred detections buffered from recent frames
        recovered_from_buffer = self._process_deferred_buffer(now_curr)

        if not detections and not recovered_from_buffer:
            self.last_rejection_reason = "ZERO_DETECTIONS"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            # Fix ZERO_DETECTIONS path: aggregator still gets a prune/tick call with empty list
            t_agg_start = time.perf_counter()
            self.aggregator.update([], now=frame.timestamp, frame_id=self._frame_id)
            agg_ms = (time.perf_counter() - t_agg_start) * 1000.0
            e2e_ms = (time.perf_counter() - t_step_start) * 1000.0
            self._record_profile_sample(
                sim_get_images_ms=sim_get_images_ms,
                capture_ms=capture_ms,
                preprocess_ms=preprocess_ms,
                inference_ms=inference_ms,
                postprocess_ms=postprocess_ms,
                tracker_ms=agg_ms,
                e2e_ms=e2e_ms,
                raw_conf=raw_conf,
            )
            self._flush_confirmed_alerts()
            return

        h, w = frame.image.shape[:2]
        cam_cfg = self.config.camera

        # 5. Geolocation
        geo_dets: List[GeoreferencedDetection] = []
        if recovered_from_buffer:
            geo_dets.extend(recovered_from_buffer)

        cam_mask_regions = getattr(cam_cfg, "mask_regions", None) or []

        for det_idx, det in enumerate(detections or []):
            trace_id = f"{self.drone_id}-f{self._frame_id}-d{det_idx}-{int(now_curr*1000)}"
            x1, y1, x2, y2 = det.bbox_xyxy
            bbox_list = [round(float(c), 2) for c in (x1, y1, x2, y2)]
            u = (x1 + x2) / 2.0
            v = (y1 + y2) / 2.0
            box_w = abs(x2 - x1)
            box_h = abs(y2 - y1)

            # Camera mask regions check: ignore fixed vehicle parts on real hardware
            is_masked = False
            for mr in cam_mask_regions:
                if len(mr) == 4:
                    mx1, my1, mx2, my2 = mr
                    if max(mx1, my1, mx2, my2) <= 1.0:
                        mx1 *= w
                        mx2 *= w
                        my1 *= h
                        my2 *= h
                    if min(mx1, mx2) <= u <= max(mx1, mx2) and min(my1, my2) <= v <= max(my1, my2):
                        is_masked = True
                        break
            if is_masked:
                logger.debug("Dropping detection at pixel (%.1f, %.1f): inside camera mask region", u, v)
                continue

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

            obs_time = float(getattr(frame, "timestamp", now_curr) or now_curr)
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
                    obs_time=obs_time,
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
                self._current_step_info["deferred"] = True
                self._current_step_info["defer_reason"] = exclusion_reason
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
                    "w": box_w,
                    "h": box_h,
                    "img_w": w,
                    "img_h": h,
                    "roll": roll,
                    "pitch": pitch,
                    "yaw": yaw,
                    "drone_lat": drone_lat,
                    "drone_lon": drone_lon,
                    "alt": alt,
                    "obs_time": obs_time,
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
                    pixel_x=u,
                    pixel_y=v,
                    box_w=box_w,
                    box_h=box_h,
                    img_w=w,
                    img_h=h,
                    roll_deg=roll,
                    pitch_deg=pitch,
                )
            )

        self.last_step_stats["geo_detections"] = len(geo_dets)
        if geo_dets and self._diag_first_geo_detection_s is None and self._pipeline_start_time is not None:
            self._diag_first_geo_detection_s = round(time.monotonic() - self._pipeline_start_time, 2)
        if not geo_dets:
            if any(item.get("deadline") for item in self._deferred_buffer):
                self.last_rejection_reason = "DETECTIONS_DEFERRED"
            else:
                self.last_rejection_reason = "GEOLOCATION_ALL_FAILED"
            self.last_step_stats["rejection_reason"] = self.last_rejection_reason
            # Tick aggregator with empty list so unconfirmed/stale tracks are pruned
            t_agg_start = time.perf_counter()
            self.aggregator.update([], now=frame.timestamp, frame_id=self._frame_id)
            agg_ms = (time.perf_counter() - t_agg_start) * 1000.0
            e2e_ms = (time.perf_counter() - t_step_start) * 1000.0
            self._record_profile_sample(
                sim_get_images_ms=sim_get_images_ms,
                capture_ms=capture_ms,
                preprocess_ms=preprocess_ms,
                inference_ms=inference_ms,
                postprocess_ms=postprocess_ms,
                tracker_ms=agg_ms,
                e2e_ms=e2e_ms,
                raw_conf=raw_conf,
            )
            self._flush_confirmed_alerts()
            return

        # 6. Aggregation
        t_agg_start = time.perf_counter()
        emitted_items = self.aggregator.update(geo_dets, now=frame.timestamp, frame_id=self._frame_id)
        agg_ms = (time.perf_counter() - t_agg_start) * 1000.0
        e2e_ms = (time.perf_counter() - t_step_start) * 1000.0
        self._record_profile_sample(
            sim_get_images_ms=sim_get_images_ms,
            capture_ms=capture_ms,
            preprocess_ms=preprocess_ms,
            inference_ms=inference_ms,
            postprocess_ms=postprocess_ms,
            tracker_ms=agg_ms,
            e2e_ms=e2e_ms,
            raw_conf=raw_conf,
        )
        self._flush_confirmed_alerts()
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
        obs_time: Optional[float] = None,
        allow_unknown_deferral: bool = True,
    ) -> Tuple[bool, str, str, Optional[str], Optional[float], List[str], List[Dict[str, Any]]]:
        """
        Evaluate candidate ground detection against active peer drones.
        Enforces configurable 3D exclusion volume, ignores inactive/dead peers,
        dead-reckons moving peers to observation timestamp obs_time, and treats
        unpositioned peers as unknown with bounded deferral.
        """
        if obs_time is None:
            obs_time = now_curr

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

        unpositioned_peers: List[str] = []
        stale_peers: List[str] = []

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

                # Dead-reckon moving peer back/forward to the observation timestamp.
                # Compute the dead-reckoning interval as dt = obs_time - peer.last_position_time (both local clock).
                # Do not use position_msg_time in any subtraction with local time.
                # Keep position_msg_time only for logging.
                if has_pos and last_pos_time is not None and isinstance(last_pos_time, (int, float)):
                    dt_obs = obs_time - float(last_pos_time)
                    vx = getattr(peer, "velocity_x", None)
                    if vx is None:
                        vx = getattr(peer, "vx", None)
                    if vx is None and hasattr(peer, "telemetry") and peer.telemetry:
                        vx = getattr(peer.telemetry, "velocity_x", getattr(peer.telemetry, "vx", None))
                    vx = float(vx or getattr(peer, "vel_n", 0.0) or 0.0)

                    vy = getattr(peer, "velocity_y", None)
                    if vy is None:
                        vy = getattr(peer, "vy", None)
                    if vy is None and hasattr(peer, "telemetry") and peer.telemetry:
                        vy = getattr(peer.telemetry, "velocity_y", getattr(peer.telemetry, "vy", None))
                    vy = float(vy or getattr(peer, "vel_e", 0.0) or 0.0)

                    vz = getattr(peer, "velocity_z", None)
                    if vz is None:
                        vz = getattr(peer, "vz", None)
                    if vz is None and hasattr(peer, "telemetry") and peer.telemetry:
                        vz = getattr(peer.telemetry, "velocity_z", getattr(peer.telemetry, "vz", None))
                    vz = float(vz or getattr(peer, "vel_d", 0.0) or 0.0)

                    if abs(dt_obs) <= max_age and (vx != 0.0 or vy != 0.0 or vz != 0.0):
                        dn = vx * dt_obs
                        de = vy * dt_obs
                        p_lat, p_lon = meters_to_lat_lon(dn, de, p_lat, p_lon)
                        p_alt = p_alt - (vz * dt_obs)

                # Distinguish active unpositioned peers from stale-telemetry peers
                if p_active:
                    if not has_pos:
                        if allow_unknown_deferral:
                            unpositioned_peers.append(p_id)
                    elif is_stale:
                        stale_peers.append(p_id)

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
                    "position_msg_time": getattr(peer, "position_msg_time", None),
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
                if stale_peers or unpositioned_peers:
                    decision = "DEFERRED"
                    pending = stale_peers + unpositioned_peers
                    exclusion_reason = f"DEFERRED_UNCERTAIN_PEER_TELEMETRY (stale_or_missing={pending})"
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
        Re-evaluates buffered deferred detections against newly arrived peer positions
        dead-reckoned to the detection's observation timestamp.
        On deadline expiry, accepts only detections deferred because an active peer never
        reported a position. If a peer was stale (has a position but old telemetry),
        drops the detection to prevent false alerts (never confirmed).
        """
        accepted: List[GeoreferencedDetection] = []
        still_buffered: List[Dict[str, Any]] = []

        for item in self._deferred_buffer:
            obs_time = float(item.get("obs_time", now_curr))
            is_deadline_expired = now_curr > item["deadline"]

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
                obs_time=obs_time,
                allow_unknown_deferral=(not is_deadline_expired),
            )

            if is_peer:
                logger.info("Deferred detection matches peer %s upon telemetry update; excluding.", reason)
            elif decision == "ACCEPTED":
                logger.info(
                    "Deferred detection at (%.6f, %.6f) accepted (%s).",
                    item["glat"],
                    item["glon"],
                    "after bounded deferral window for unpositioned peer" if is_deadline_expired else "after peer telemetry update",
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
                        pixel_x=item.get("u"),
                        pixel_y=item.get("v"),
                        box_w=item.get("w"),
                        box_h=item.get("h"),
                        img_w=item.get("img_w"),
                        img_h=item.get("img_h"),
                        roll_deg=item.get("roll"),
                        pitch_deg=item.get("pitch"),
                    )
                )
            elif is_deadline_expired:
                # Deadline expired but peer is still stale (or uncertain) -> drop it, never confirm!
                logger.warning(
                    "Dropping deferred detection at (%.6f, %.6f): deferral deadline expired but peer telemetry remains stale (%s).",
                    item["glat"],
                    item["glon"],
                    reason,
                )
            else:
                still_buffered.append(item)

        self._deferred_buffer = still_buffered
        return accepted

