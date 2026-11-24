"""Unit tests for perception warm-up, readiness states, fast load retries, and timeline diagnostics."""

import time
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np
import pytest

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Detection, Frame
from DroneOS.shared.config.models import PerceptionConfig


@pytest.mark.asyncio
async def test_warmup_disabled_when_zero_frames():
    """warmup_frames=0 must disable warm-up and transition immediately to OK."""
    detector = GarbageDetector(weights_path="models/garbage.pt")
    detector._available = True

    with patch.object(detector, "_sync_predict") as mock_predict:
        res = detector.warmup(warmup_frames=0)
        assert res is True
        assert detector._warmup_done is True
        assert detector.get_status()[0] == "OK"
        mock_predict.assert_not_called()
    detector.close()


@pytest.mark.asyncio
async def test_warmup_runs_dummy_inferences_and_discards_detections():
    """Warmup executes N dummy frames; detections never reach the pipeline or tracker."""
    detector = GarbageDetector(weights_path="models/garbage.pt")
    detector._available = True

    dummy_detection = Detection(
        bbox_xyxy=(10.0, 10.0, 50.0, 50.0),
        confidence=0.99,
        label="rubbish",
    )

    with patch.object(detector, "load", return_value=True):
        with patch.object(detector, "_sync_predict", return_value=[dummy_detection]) as mock_predict:
            res = detector.warmup(warmup_frames=3)
            assert res is True
            assert mock_predict.call_count == 3
            assert detector._warmup_done is True
            st, reason = detector.get_status()
            assert st == "OK"
    detector.close()


@pytest.mark.asyncio
async def test_pipeline_skips_frame_acquisition_while_warming_up():
    """Pipeline loop must not acquire camera frames or read telemetry while detector is warming up."""
    mock_frame_source = AsyncMock()
    mock_fc = AsyncMock()
    mock_net = AsyncMock()

    detector = GarbageDetector(weights_path="models/garbage.pt")
    detector._status = "WARMING_UP"
    detector._available = False
    detector._warmup_done = False

    cfg = PerceptionConfig(enabled=True, warmup_frames=3, max_fps=1.0)
    pipeline = PerceptionPipeline(
        drone_id="drone1",
        frame_source=mock_frame_source,
        detector=detector,
        flight_controller=mock_fc,
        network_adapter=mock_net,
        config=cfg,
    )

    await pipeline._step()

    # Verify frame was never acquired and telemetry was never requested
    mock_frame_source.get_frame.assert_not_called()
    mock_fc.get_telemetry.assert_not_called()
    assert pipeline.last_rejection_reason == "DETECTOR_WARMING_UP"
    assert len(pipeline.aggregator.tracks) == 0
    detector.close()


@pytest.mark.asyncio
async def test_cuda_failure_during_warmup_falls_back_to_cpu_once_and_stays():
    """If CUDA fails during warm-up (OOM or runtime error), falls back to CPU once and stays on CPU."""
    detector = GarbageDetector(weights_path="models/garbage.pt", device="cuda")
    detector._available = True
    detector.resolved_device = "cuda"
    detector.fp16 = True
    detector.resolved_provider = "torch.cuda"

    call_count = 0

    def mock_predict_side_effect(image):
        nonlocal call_count
        call_count += 1
        if detector.resolved_device == "cuda":
            raise RuntimeError("CUDA out of memory during warmup")
        return []

    with patch.object(detector, "load", return_value=True):
        with patch.object(detector, "_sync_predict", side_effect=mock_predict_side_effect):
            res = detector.warmup(warmup_frames=2)
            assert res is True
            assert detector.resolved_device == "cpu"
            assert detector.fp16 is False
            assert detector.resolved_provider == "torch.cpu"
            assert detector.get_status()[0] == "OK"
            # 1 failure on CUDA + 2 retries on CPU = 3 calls
            assert call_count == 3

    # Subsequent warm-up or predict stays on CPU
    assert detector.resolved_device == "cpu"
    detector.close()


def test_load_failure_fast_retries_then_backoff(tmp_path):
    """Load failure must use 2 quick retries (1s, 2s) before exponential backoff (5s..60s)."""
    non_existent = tmp_path / "non_existent.pt"
    detector = GarbageDetector(weights_path=str(non_existent))

    # Attempt 1
    t0 = 100.0
    with patch("time.monotonic", return_value=t0):
        ok1 = detector.load()
        assert ok1 is False
        assert detector._retry_count == 1
        assert detector._current_backoff_s == 1.0

    # Immediate retry before 1.0s fails due to backoff window
    with patch("time.monotonic", return_value=t0 + 0.5):
        ok_too_soon = detector.load()
        assert ok_too_soon is False
        assert detector._retry_count == 1  # Not incremented

    # Attempt 2 (after 1.0s backoff)
    with patch("time.monotonic", return_value=t0 + 1.1):
        ok2 = detector.load()
        assert ok2 is False
        assert detector._retry_count == 2
        assert detector._current_backoff_s == 2.0

    # Attempt 3 (after 2.0s backoff)
    with patch("time.monotonic", return_value=t0 + 3.2):
        ok3 = detector.load()
        assert ok3 is False
        assert detector._retry_count == 3
        assert detector._current_backoff_s == 5.0

    # Attempt 4 (exponential: 5.0 * 2^1 = 10.0s)
    with patch("time.monotonic", return_value=t0 + 8.3):
        ok4 = detector.load()
        assert ok4 is False
        assert detector._retry_count == 4
        assert detector._current_backoff_s == 10.0

    detector.close()


@pytest.mark.asyncio
async def test_timeline_diagnostics_recording_and_summary():
    """Timeline diagnostics logs per step for first 60s and prints summary."""
    mock_frame_source = AsyncMock()
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_frame_source.get_frame.side_effect = lambda: Frame(image=dummy_img, timestamp=time.time())

    mock_fc = AsyncMock()

    def make_fresh_telem():
        mock_telem = MagicMock()
        mock_telem.armed_state = "ARMED"
        mock_telem.gps_valid = True
        mock_telem.altitude = 10.0
        mock_telem.roll = 2.0
        mock_telem.pitch = -1.0
        mock_telem.yaw = 45.0
        mock_telem.latitude = 37.7749
        mock_telem.longitude = -122.4194
        mock_telem.timestamp = time.time()
        return mock_telem

    mock_fc.get_telemetry.side_effect = make_fresh_telem

    mock_net = AsyncMock()

    detector = GarbageDetector(weights_path="models/garbage.pt")
    detector._available = True
    detector._status = "OK"
    detector._warmup_done = True

    cfg = PerceptionConfig(enabled=True, warmup_frames=0, max_fps=1.0)
    pipeline = PerceptionPipeline(
        drone_id="drone1",
        frame_source=mock_frame_source,
        detector=detector,
        flight_controller=mock_fc,
        network_adapter=mock_net,
        config=cfg,
    )

    with patch.object(detector, "detect", return_value=[]):
        await pipeline._step()

    assert pipeline._pipeline_start_time is not None
    assert "ZERO_DETECTIONS" in pipeline._diag_rejection_histogram
    assert pipeline._diag_first_inference_s is not None

    # Calling stop should trigger summary print
    await pipeline.stop()
    assert pipeline._diag_summary_printed is True
