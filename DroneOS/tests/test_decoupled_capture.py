"""Tests for decoupled frame capture and inference pipeline."""

import asyncio
import time
from typing import Optional
from unittest.mock import AsyncMock
import numpy as np
import pytest

from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Frame
from DroneOS.shared.config.models import PerceptionConfig
from DroneOS.shared.protocol.messages import TelemetryData


class FakeSlowFrameSource:
    def __init__(self, delay_s: float = 0.04):
        self.delay_s = delay_s
        self.seq = 0
        self.closed = False

    async def get_frame(self) -> Optional[Frame]:
        if self.closed:
            return None
        await asyncio.sleep(self.delay_s)
        self.seq += 1
        img = np.zeros((240, 320, 3), dtype=np.uint8)
        return Frame(
            image=img,
            timestamp=time.time(),
            metadata={"seq": self.seq, "capture_ms": self.delay_s * 1000.0},
        )

    async def close(self) -> None:
        self.closed = True


class FakeSlowDetector:
    def __init__(self, delay_s: float = 0.04):
        self.delay_s = delay_s
        self.processed_seqs = []
        self._available = True
        self.device = "cpu"

    def load(self) -> bool:
        return True

    def get_status(self):
        return ("OK", "")

    async def detect(self, image, **kwargs):
        await asyncio.sleep(self.delay_s)
        return []

    def _sync_predict(self, image):
        time.sleep(self.delay_s)
        return []

    def close(self):
        pass


def make_fresh_telemetry():
    return TelemetryData(
        flight_mode="OFFBOARD",
        armed_state="ARMED",
        gps_valid=True,
        altitude=10.0,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        latitude=47.6415,
        longitude=-122.1402,
        timestamp=time.time(),
    )


@pytest.mark.asyncio
async def test_decoupled_throughput_approaches_max_capture_inference():
    """Decoupled execution period approaches max(capture, inference) instead of (capture + inference)."""
    cap_delay = 0.04
    inf_delay = 0.04
    steps = 6

    # 1. Measure sequential execution (capture + inference in series)
    src_seq = FakeSlowFrameSource(delay_s=cap_delay)
    det_seq = FakeSlowDetector(delay_s=inf_delay)
    fc_seq = AsyncMock()
    fc_seq.get_telemetry.side_effect = make_fresh_telemetry
    net_seq = AsyncMock()
    cfg = PerceptionConfig(enabled=True, max_fps=100.0, decoupled_capture=False)

    pipe_seq = PerceptionPipeline("drone1", src_seq, det_seq, fc_seq, net_seq, cfg)
    t0_seq = time.perf_counter()
    for _ in range(steps):
        await pipe_seq._step()
    dur_seq = time.perf_counter() - t0_seq
    await pipe_seq.stop()

    # 2. Measure decoupled execution (capture running in parallel background task)
    src_dec = FakeSlowFrameSource(delay_s=cap_delay)
    det_dec = FakeSlowDetector(delay_s=inf_delay)
    fc_dec = AsyncMock()
    fc_dec.get_telemetry.side_effect = make_fresh_telemetry
    net_dec = AsyncMock()
    cfg_dec = PerceptionConfig(enabled=True, max_fps=100.0, decoupled_capture=True)

    pipe_dec = PerceptionPipeline("drone1", src_dec, det_dec, fc_dec, net_dec, cfg_dec)
    pipe_dec._running = True
    pipe_dec.start_capture()

    t0_dec = time.perf_counter()
    for _ in range(steps):
        await pipe_dec._step()
    dur_dec = time.perf_counter() - t0_dec
    await pipe_dec.stop()

    # Sequential duration should be ~steps * (0.04 + 0.04) = 0.48s
    # Decoupled duration should be ~first_cap + steps * max(0.04, 0.04) ~ 0.28s
    # Decoupled must be at least 25% faster than sequential
    assert dur_dec < (dur_seq * 0.75), f"Decoupled ({dur_dec:.3f}s) was not faster than sequential ({dur_seq:.3f}s)"


@pytest.mark.asyncio
async def test_decoupled_skips_stale_frames():
    """When inference is slower than capture, inference always takes the latest frame and drops stale ones."""
    src = FakeSlowFrameSource(delay_s=0.01)  # Fast capture: 100 FPS
    det = FakeSlowDetector(delay_s=0.06)    # Slow detector: ~16 FPS
    fc = AsyncMock()
    fc.get_telemetry.side_effect = make_fresh_telemetry
    net = AsyncMock()
    cfg = PerceptionConfig(enabled=True, max_fps=100.0, decoupled_capture=True)

    pipe = PerceptionPipeline("drone1", src, det, fc, net, cfg)
    pipe._running = True
    pipe.start_capture()

    processed_seqs = []
    orig_execute = pipe._execute_step

    async def hook_step():
        res = await orig_execute()
        if pipe._latest_frame and hasattr(pipe._latest_frame, "metadata"):
            processed_seqs.append(pipe._latest_frame.metadata.get("seq"))
        return res

    pipe._execute_step = hook_step

    for _ in range(4):
        await pipe._step()

    await pipe.stop()

    assert len(processed_seqs) == 4
    # Because detector takes 60ms and capture takes 10ms, at least 4-6 frames were captured per step
    # The jump between subsequent processed seq numbers must be > 1 (stale frames dropped)
    seq_diffs = [processed_seqs[i] - processed_seqs[i - 1] for i in range(1, len(processed_seqs))]
    assert any(d > 1 for d in seq_diffs), f"Expected frame skipping but saw sequence: {processed_seqs}"


@pytest.mark.asyncio
async def test_decoupled_respects_max_fps():
    """Decoupled inference loop respects configured max_fps interval."""
    src = FakeSlowFrameSource(delay_s=0.005)
    det = FakeSlowDetector(delay_s=0.005)
    fc = AsyncMock()
    fc.get_telemetry.side_effect = make_fresh_telemetry
    net = AsyncMock()
    cfg = PerceptionConfig(enabled=True, max_fps=10.0, decoupled_capture=True)  # interval = 0.1s

    pipe = PerceptionPipeline("drone1", src, det, fc, net, cfg)
    await pipe.start()

    await asyncio.sleep(0.35)  # in 0.35s at 10 FPS, expect ~3-4 frames
    await pipe.stop()

    assert pipe._frame_id >= 2
    assert pipe._frame_id <= 5, f"Expected 3-4 frames at max_fps=10, got {pipe._frame_id}"


@pytest.mark.asyncio
async def test_decoupled_frame_too_stale_gate_active():
    """Stale frame gate rejects frames older than max_frame_age_s."""
    mock_src = AsyncMock()
    stale_ts = time.time() - 5.0  # 5s old frame
    mock_src.get_frame.return_value = Frame(
        image=np.zeros((100, 100, 3), dtype=np.uint8),
        timestamp=stale_ts,
    )
    det = FakeSlowDetector(delay_s=0.001)
    fc = AsyncMock()
    fc.get_telemetry.side_effect = make_fresh_telemetry
    net = AsyncMock()
    cfg = PerceptionConfig(enabled=True, max_frame_age_s=2.0)

    pipe = PerceptionPipeline("drone1", mock_src, det, fc, net, cfg)
    await pipe._step()
    await pipe.stop()

    assert pipe.last_rejection_reason is not None
    assert "STALE_FRAME" in pipe.last_rejection_reason


class FakeDetectingDetector:
    def __init__(self, delay_s: float = 0.0):
        self.delay_s = delay_s
        self._available = True
        self.device = "cpu"

    def load(self) -> bool:
        return True

    def get_status(self):
        return ("OK", "")

    async def detect(self, image, **kwargs):
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        from DroneOS.perception.types import Detection
        return [Detection(bbox_xyxy=(150.0, 110.0, 170.0, 130.0), confidence=0.85, label="garbage")]

    def close(self):
        pass


@pytest.mark.asyncio
async def test_decoupled_moving_drone_uses_capture_time_pose_not_newer():
    """
    A moving drone with slow capture or delayed processing must pair each frame with
    the telemetry/pose sampled at its capture time, NOT a newer pose sampled during inference.
    """
    capture_lat = 47.641500
    capture_lon = -122.140200
    moved_lat = 47.650000  # ~945m away north
    moved_lon = -122.150000

    current_lat = capture_lat
    current_lon = capture_lon

    async def moving_telemetry():
        return TelemetryData(
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
            altitude=10.0,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            latitude=current_lat,
            longitude=current_lon,
            timestamp=time.time(),
        )

    class MovingDroneDetector(FakeDetectingDetector):
        async def detect(self, image, **kwargs):
            nonlocal current_lat, current_lon
            # The drone moves during the detector inference window!
            current_lat = moved_lat
            current_lon = moved_lon
            return await super().detect(image, **kwargs)

    src = FakeSlowFrameSource(delay_s=0.01)
    det = MovingDroneDetector(delay_s=0.02)
    fc = AsyncMock()
    fc.get_telemetry.side_effect = moving_telemetry
    net = AsyncMock()
    cfg = PerceptionConfig(enabled=True, max_fps=20.0, decoupled_capture=True)

    pipe = PerceptionPipeline("drone1", src, det, fc, net, cfg)
    pipe._running = True
    pipe.start_capture()

    # Wait for frame capture in background task while drone is at capture_lat
    await asyncio.sleep(0.03)

    # Process step - during detector inference, drone moves to moved_lat
    await pipe._step()

    # Check geolocated detections recorded in aggregator
    tracks = list(pipe.aggregator.tracks.values())
    assert len(tracks) >= 1, "Expected track to be formed from detection"
    track = tracks[0]

    # Must match the capture-time pose (~47.6415), NOT the moved pose (~47.6500)
    assert abs(track.lat - capture_lat) < 0.001, (
        f"Track lat {track.lat:.6f} drifted from capture lat {capture_lat:.6f} towards moved lat {moved_lat:.6f}"
    )
    assert abs(track.lat - moved_lat) > 0.005, (
        f"Track lat {track.lat:.6f} erroneously used newer pose {moved_lat:.6f} instead of capture pose"
    )

    await pipe.stop()


@pytest.mark.asyncio
async def test_decoupled_stale_frame_dropped_in_decoupled_mode():
    """In decoupled mode, if the latest frame becomes older than max_frame_age_s, it is dropped."""
    now_ts = time.time()

    class StaleFrameSource:
        def __init__(self):
            self.closed = False

        async def get_frame(self):
            await asyncio.sleep(0.01)
            # Frame was captured 3.5 seconds ago (stale)
            return Frame(
                image=np.zeros((100, 100, 3), dtype=np.uint8),
                timestamp=time.time() - 3.5,
            )

        async def close(self):
            self.closed = True

    mock_src = StaleFrameSource()
    det = FakeDetectingDetector()
    fc = AsyncMock()
    fc.get_telemetry.side_effect = make_fresh_telemetry
    net = AsyncMock()
    cfg = PerceptionConfig(enabled=True, max_frame_age_s=2.0, decoupled_capture=True)

    pipe = PerceptionPipeline("drone1", mock_src, det, fc, net, cfg)
    pipe._running = True
    pipe.start_capture()
    await asyncio.sleep(0.03)  # Allow capture loop to acquire the stale frame

    await pipe._step()
    await pipe.stop()

    assert pipe.last_rejection_reason is not None
    assert "STALE_FRAME" in pipe.last_rejection_reason


