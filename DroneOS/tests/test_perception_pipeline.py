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

