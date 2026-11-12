"""Perception processing pipeline: captures, detects, geolocates, aggregates, and broadcasts."""

import asyncio
import math
import time
from typing import List, Optional

from DroneOS1.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS1.perception.detector import GarbageDetector
from DroneOS1.perception.frame_sources import FrameSource
from DroneOS1.perception.geolocate import pixel_to_lat_lon
from DroneOS1.shared.protocol.messages import DetectionItem, DetectionMessage
from DroneOS1.shared.utils.logger import setup_logger

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
        # 1. Detector load check: if load returns False, log error and terminate loop
        if hasattr(self.detector, "load"):
            load_fn = getattr(self.detector, "load")
            if callable(load_fn):
                load_res = load_fn()
                if asyncio.iscoroutine(load_res):
                    load_res = await load_res
                if load_res is False:
                    if not getattr(self, "_load_failed_logged", False):
                        logger.error("Perception detector failed to load (missing weights or ultralytics); stopping perception loop.")
                        self._load_failed_logged = True
                    self._running = False
                    return

        # 2. Early telemetry gating BEFORE acquiring a frame
        pre_telem = await self.fc.get_telemetry()
        if pre_telem is None:
            return

        # Must be armed
        is_armed = getattr(pre_telem, "armed_state", None) == "ARMED"
        if not is_armed:
            return

        # GPS fix valid
        if not getattr(pre_telem, "gps_valid", False):
            return

        # Altitude above minimum
        alt = float(getattr(pre_telem, "altitude", 0.0) or 0.0)
        if alt < self.config.min_altitude_m:
            return

        # Tilt angle limits (|roll|, |pitch| <= max_tilt_deg)
        roll = float(getattr(pre_telem, "roll", 0.0) or 0.0)
        pitch = float(getattr(pre_telem, "pitch", 0.0) or 0.0)
        if abs(roll) > self.config.max_tilt_deg or abs(pitch) > self.config.max_tilt_deg:
            return

        pre_lat = getattr(pre_telem, "latitude", None)
        pre_lon = getattr(pre_telem, "longitude", None)
        if pre_lat is None or pre_lon is None or (pre_lat == 0.0 and pre_lon == 0.0):
            return

        # 3. Acquire frame
        frame = await self.frame_source.get_frame()
        if frame is None or frame.image is None:
            return

        # 4. Fresh telemetry snapshot immediately after capture for geolocation
        telem = await self.fc.get_telemetry()
        if telem is None:
            return

        drone_lat = getattr(telem, "latitude", None)
        drone_lon = getattr(telem, "longitude", None)
        if drone_lat is None or drone_lon is None or (drone_lat == 0.0 and drone_lon == 0.0):
            return

        alt = float(getattr(telem, "altitude", 0.0) or 0.0)
        roll = float(getattr(telem, "roll", 0.0) or 0.0)
        pitch = float(getattr(telem, "pitch", 0.0) or 0.0)
        if abs(roll) > self.config.max_tilt_deg or abs(pitch) > self.config.max_tilt_deg:
            return

        yaw = float(getattr(telem, "yaw", getattr(telem, "heading", 0.0)) or 0.0)

        # 4. Inference
        try:
            detections = await self.detector.detect(frame.image)
        except Exception as e:
            logger.error("Detector inference failed: %s", e)
            return

        if not detections:
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

        if not geo_dets:
            return

        # 6. Aggregation
        emitted_items = self.aggregator.update(geo_dets, now=frame.timestamp)
        if not emitted_items:
            return

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
            await self.network.broadcast_message(msg)

