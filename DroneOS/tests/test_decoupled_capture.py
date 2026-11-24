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

