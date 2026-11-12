"""Unit tests for the perception pipeline execution, gating, chunking, and fault-tolerance."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import numpy as np
import pytest

from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Detection, Frame
from DroneOS.shared.config.models import CameraConfig, PerceptionConfig
from DroneOS.shared.protocol.messages import TelemetryData


class FakeFrameSource:
    def __init__(self, frame=None):
        self.frame = frame
        self.is_synthetic = True

    async def get_frame(self):
        return self.frame

    async def close(self):
        pass


class FakeDetector:
    def __init__(self, detections=None, error_on_detect=False):
        self.detections = detections if detections is not None else []
        self.error_on_detect = error_on_detect

    def load(self):
        return True

    async def ensure_loaded(self):
        return self.load()

    async def detect(self, image):
        if self.error_on_detect:
            raise RuntimeError("Simulated inference engine crash")
        return self.detections

    def close(self):
        pass


def make_valid_telemetry(
    armed_state="ARMED",
    gps_valid=True,
    altitude=10.0,
    roll=0.0,
    pitch=0.0,
    yaw=0.0,
    lat=22.314,
    lon=87.310,
):
    return TelemetryData(
        flight_mode="OFFBOARD",
        armed_state=armed_state,
        gps_valid=gps_valid,
        altitude=altitude,
        roll=roll,
        pitch=pitch,
        yaw=yaw,
        latitude=lat,
        longitude=lon,
    )


@pytest.mark.asyncio
async def test_pipeline_gating_disarmed():
    """Disarmed drone must not broadcast detections."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector([Detection(bbox_xyxy=(600, 350, 640, 370), confidence=0.9, label="rubbish")])
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry(armed_state="DISARMED")
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline._step()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_gating_gps_invalid():
    """GPS invalid must gate detection broadcasting."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector([Detection(bbox_xyxy=(600, 350, 640, 370), confidence=0.9, label="rubbish")])
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry(gps_valid=False)
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline._step()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_gating_low_altitude():
    """Altitude below min_altitude_m (2.0) must gate perception."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector([Detection(bbox_xyxy=(600, 350, 640, 370), confidence=0.9, label="rubbish")])
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry(altitude=1.5)
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, min_altitude_m=2.0, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline._step()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_gating_excessive_tilt():
    """Roll or pitch exceeding max_tilt_deg (20 deg) must gate perception."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector([Detection(bbox_xyxy=(600, 350, 640, 370), confidence=0.9, label="rubbish")])
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry(roll=25.0)  # > 20
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, max_tilt_deg=20.0, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline._step()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_chunking():
    """Verify items are chunked to <= 10 items per DetectionMessage."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))

    # Generate 25 distinct detections across the image
    dets = []
    for i in range(25):
        # Spaced out pixels
        u = 200 + (i % 5) * 150
        v = 150 + (i // 5) * 90
        dets.append(Detection(bbox_xyxy=(u - 10, v - 10, u + 10, v + 10), confidence=0.8, label="rubbish"))

    detector = FakeDetector(dets)
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry()
    network = AsyncMock()

    # With min_hits=1, all 25 detections are confirmed and emitted
    cfg = PerceptionConfig(enabled=True, min_hits=1, merge_radius_m=0.1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline._step()
    assert network.broadcast_message.call_count == 3  # 10 + 10 + 5
    calls = network.broadcast_message.call_args_list
    assert len(calls[0].args[0].detections) == 10
    assert len(calls[1].args[0].detections) == 10
    assert len(calls[2].args[0].detections) == 5
    assert calls[0].args[0].synthetic is True


@pytest.mark.asyncio
async def test_pipeline_detector_exception_resilience():
    """An exception in detector must not kill the pipeline loop."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector(error_on_detect=True)
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry()
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, max_fps=10.0)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    # Calling _step directly handles detector exception cleanly
    await pipeline._step()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_start_and_stop_lifecycle():
    """Verify that start() launches run_loop() in background, runs _step, and stop() cleanly cancels."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector([Detection(bbox_xyxy=(600, 350, 640, 370), confidence=0.9, label="rubbish")])
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry()
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, max_fps=20.0, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline.start()
    assert pipeline._running is True
    assert pipeline._task is not None

    await asyncio.sleep(0.3)

    assert fc.get_telemetry.call_count >= 1
    assert network.broadcast_message.call_count >= 1

    await pipeline.stop()
    assert pipeline._running is False
    assert pipeline._task.done()
    assert pipeline._task.cancelled() or pipeline._task.exception() is None


@pytest.mark.asyncio
async def test_pipeline_direct_run_loop_task():
    """Verify that awaiting run_loop() directly in a task sets _running and processes frames."""
    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = FakeDetector([Detection(bbox_xyxy=(600, 350, 640, 370), confidence=0.9, label="rubbish")])
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry()
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, max_fps=20.0, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    task = asyncio.create_task(pipeline.run_loop())
    await asyncio.sleep(0.3)

    assert pipeline._running is True
    assert fc.get_telemetry.call_count >= 1
    assert network.broadcast_message.call_count >= 1

    await pipeline.stop()
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert task.done()
    assert task.cancelled() or task.exception() is None


@pytest.mark.asyncio
async def test_pipeline_detector_load_failure_stops_loop():
    """If detector.load() returns False, pipeline logs error and stops loop without capturing frames."""
    frame_src = AsyncMock()
    detector = FakeDetector()
    detector.load = MagicMock(return_value=False)
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry()
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, max_fps=20.0)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    task = asyncio.create_task(pipeline.run_loop())
    await asyncio.sleep(0.1)

    assert pipeline._running is False
    assert task.done()
    assert task.exception() is None
    frame_src.get_frame.assert_not_called()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_gating_before_frame_capture():
    """Telemetry gating must prevent frame capture when drone is disarmed or invalid."""
    frame_src = AsyncMock()
    detector = FakeDetector()
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry(armed_state="DISARMED")
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    await pipeline._step()
    frame_src.get_frame.assert_not_called()
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_async_ensure_loaded_does_not_block_event_loop():
    """Verify that detector loading in a background thread does not freeze the asyncio event loop."""
    import time
    from DroneOS.perception.detector import GarbageDetector

    class SlowFakeDetector(GarbageDetector):
        def __init__(self):
            super().__init__(weights_path="models/garbage.pt")

        def load(self):
            time.sleep(0.5)
            return True

        async def detect(self, image):
            return []

    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame_src = FakeFrameSource(Frame(image=dummy_img, timestamp=100.0))
    detector = SlowFakeDetector()
    fc = AsyncMock()
    fc.get_telemetry.return_value = make_valid_telemetry()
    network = AsyncMock()

    cfg = PerceptionConfig(enabled=True, max_fps=20.0, min_hits=1)
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)

    # 20 ms ticker task to measure event loop freeze
    tick_gaps = []
    ticker_running = True

    async def ticker():
        last_time = time.monotonic()
        while ticker_running:
            await asyncio.sleep(0.02)
            now = time.monotonic()
            tick_gaps.append(now - last_time)
            last_time = now

    ticker_task = asyncio.create_task(ticker())

    try:
        await pipeline.start()
        # Wait 0.7s so load() (0.5s) completes in the executor while the ticker runs
        await asyncio.sleep(0.7)
    finally:
        ticker_running = False
        await pipeline.stop()
        if not ticker_task.done():
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

    assert len(tick_gaps) >= 10, f"Expected multiple ticks, got {len(tick_gaps)}"
    max_gap = max(tick_gaps)
    # If load() ran synchronously on the event loop, max_gap would be >= 0.5 s.
    # Running via ensure_loaded in self._executor keeps tick gaps well under 0.5 s (< 0.20 s).
    assert max_gap < 0.20, f"Event loop frozen: max tick gap was {max_gap:.3f} s (expected < 0.20 s)"



