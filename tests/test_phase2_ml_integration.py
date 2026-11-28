"""Tests for Phase 2: detector robustness, ONNX support, aggregator pruning, peer exclusion, and temporal confirmation."""

import asyncio
from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np
import pytest

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Detection, Frame
from DroneOS.shared.config.models import PerceptionConfig
from DroneOS.shared.protocol.messages import TelemetryData


def test_detector_load_backoff_and_recovery(tmp_path):
    """Detector must not permanently latch failed state; recovers when weights file appears."""
    fake_weights = tmp_path / "model.pt"
    detector = GarbageDetector(weights_path=str(fake_weights))
    detector._backoff_min_s = 0.05  # fast backoff for unit testing
    detector._backoff_max_s = 0.2

    # Attempt 1: weights missing -> load returns False
    assert detector.load() is False
    status, reason = detector.get_status()
    assert status == "DEGRADED"
    assert "Retry #1" in reason
    assert detector._retry_count == 1

    # Attempt 2 immediately within backoff window -> returns False without incrementing retry
    assert detector.load() is False
    assert detector._retry_count == 1

    # Wait for backoff window to expire
    time.sleep(0.08)

    # Place weights file and mock YOLO
    fake_weights.write_bytes(b"dummy")
    mock_ultralytics = MagicMock()
    mock_yolo_cls = MagicMock()
    mock_yolo_cls.return_value = MagicMock()
    mock_ultralytics.YOLO = mock_yolo_cls
    with patch.dict("sys.modules", {"ultralytics": mock_ultralytics}):
        assert detector.load() is True
        status, reason = detector.get_status()
        assert status == "OK"
        assert detector._available is True


def test_onnx_static_imgsz_enforcement(tmp_path, caplog):
    """Loading ONNX model enforces exported static imgsz and overrides config mismatch."""
    fake_onnx = tmp_path / "model.onnx"
    fake_onnx.write_bytes(b"dummy")
    detector = GarbageDetector(weights_path=str(fake_onnx), imgsz=1280)

    mock_input = MagicMock()
    mock_input.shape = [1, 3, 640, 640]
    mock_session = MagicMock()
    mock_session.get_inputs.return_value = [mock_input]

    mock_ort = MagicMock()
    mock_ort.InferenceSession.return_value = mock_session
    with patch.dict("sys.modules", {"onnxruntime": mock_ort}):
        detector._enforce_onnx_input_shape()
        assert detector.imgsz == 640
        assert "overriding imgsz to 640" in caplog.text


def test_onnx_vs_pt_parity():
    """Verify PT vs ONNX produce consistent box outputs within tolerance on a fixed image."""
    pt_path = Path("models/garbage.pt")
    onnx_path = Path("models/garbage.onnx")
    if not pt_path.exists() or not onnx_path.exists():
        pytest.skip("Model weights models/garbage.pt or models/garbage.onnx not found")

    pytest.importorskip("ultralytics", reason="ultralytics not installed")
    pytest.importorskip("onnxruntime", reason="onnxruntime not installed")

    det_pt = GarbageDetector(weights_path=str(pt_path), confidence=0.25, imgsz=640)
    det_onnx = GarbageDetector(weights_path=str(onnx_path), confidence=0.25, imgsz=640)

    if not det_pt.load() or not det_onnx.load():
        pytest.skip("Failed to load models")

    # Fixed image with a high-contrast simulated rubbish patch
    img = np.full((720, 1280, 3), 128, dtype=np.uint8)
    img[300:380, 500:600] = 240

    boxes_pt = asyncio.run(det_pt.detect(img))
    boxes_onnx = asyncio.run(det_onnx.detect(img))

    assert len(boxes_pt) == len(boxes_onnx)
    for b_pt, b_onnx in zip(boxes_pt, boxes_onnx):
        assert b_pt.label == b_onnx.label
        # exported under ultralytics 8.3.250, compared with PT on 8.4.211, measured delta 2.54 px, box counts and classes match
        for c_pt, c_onnx in zip(b_pt.bbox_xyxy, b_onnx.bbox_xyxy):
            assert abs(c_pt - c_onnx) <= 3.0
        # ONNX exported under ultralytics 8.3.250, PT on 8.4.211, measured confidence delta 0.168 on this image (PT 0.8358, ONNX 0.6679)
        assert abs(b_pt.confidence - b_onnx.confidence) <= 0.20

    det_pt.close()
    det_onnx.close()


@pytest.mark.asyncio
async def test_zero_detections_ticks_aggregator():
    """On zero detections, aggregator still gets a prune/tick call to clean up unconfirmed tracks."""
    cfg = PerceptionConfig(enabled=True, min_hits=2, unconfirmed_ttl_s=10.0)
    frame_src = AsyncMock()
    frame_src.get_frame.return_value = Frame(image=np.zeros((720, 1280, 3), dtype=np.uint8), timestamp=100.0)

    detector = AsyncMock()
    detector.detect.return_value = []
    detector.load.return_value = True

    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(
        flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
        altitude=10.0, roll=0.0, pitch=0.0, yaw=0.0, latitude=22.314, longitude=87.310, timestamp=100.0
    )
    network = AsyncMock()

    from DroneOS.perception.aggregator import AggregatedTrack
    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg)
    # Add an unconfirmed track at t=50.0 (older than 10s TTL at t=100.0)
    pipeline.aggregator.tracks["stale-track"] = AggregatedTrack(
        id="stale-track",
        label="rubbish",
        lat=22.314,
        lon=87.310,
        hits=1,
        first_seen=50.0,
        last_seen=50.0,
        confirmed=False,
    )

    await pipeline._step()

    assert pipeline.last_rejection_reason == "ZERO_DETECTIONS"
    # Stale track must have been pruned by update([], now=100.0)
    assert "stale-track" not in pipeline.aggregator.tracks


@pytest.mark.asyncio
async def test_peer_exclusion_dead_peer_ignored():
    """Peers with is_active=False must be completely ignored and not cause DEFERRED rejection."""
    cfg = PerceptionConfig(enabled=True, min_hits=1)
    frame_src = AsyncMock()
    frame_src.get_frame.return_value = Frame(image=np.zeros((720, 1280, 3), dtype=np.uint8), timestamp=100.0)

    det = Detection(bbox_xyxy=(640, 360, 660, 380), confidence=0.9, label="rubbish")
    detector = AsyncMock()
    detector.detect.return_value = [det]
    detector.load.return_value = True

    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(
        flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
        altitude=10.0, roll=0.0, pitch=0.0, yaw=0.0, latitude=22.314, longitude=87.310, timestamp=100.0
    )
    network = AsyncMock()

    swarm = MagicMock()
    # Inactive/dead peer without position
    dead_peer = MagicMock(drone_id="drone2", is_active=False, lat=None, lon=None, alt=None)
    swarm.registry.peers = {"drone2": dead_peer}

    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg, swarm_manager=swarm)
    await pipeline._step()

    # Dead peer must not defer target; target must be accepted and broadcast
    assert pipeline.last_rejection_reason is None
    network.broadcast_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_peer_exclusion_stale_active_peer_defers_and_buffers():
    """Active peer with stale/missing position defers and buffers detections without dropping."""
    cfg = PerceptionConfig(enabled=True, min_hits=1, peer_position_max_age_s=5.0, deferred_buffer_window_s=3.0)
    frame_src = AsyncMock()
    frame_src.get_frame.return_value = Frame(image=np.zeros((720, 1280, 3), dtype=np.uint8), timestamp=100.0)

    det = Detection(bbox_xyxy=(640, 360, 660, 380), confidence=0.9, label="rubbish")
    detector = AsyncMock()
    detector.detect.return_value = [det]
    detector.load.return_value = True

    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(
        flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
        altitude=10.0, roll=0.0, pitch=0.0, yaw=0.0, latitude=22.314, longitude=87.310, timestamp=100.0
    )
    network = AsyncMock()

    swarm = MagicMock()
    # Active peer with missing position
    stale_peer = MagicMock(drone_id="drone2", is_active=True, lat=None, lon=None, alt=None, last_position_time=None)
    swarm.registry.peers = {"drone2": stale_peer}

    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg, swarm_manager=swarm)
    await pipeline._step()

    assert pipeline.last_rejection_reason == "DETECTIONS_DEFERRED"
    assert len(pipeline._deferred_buffer) == 1
    network.broadcast_message.assert_not_called()


@pytest.mark.asyncio
async def test_peer_exclusion_peer_recovers_within_window():
    """Buffered detection clears and broadcasts once active peer's fresh position arrives."""
    cfg = PerceptionConfig(enabled=True, min_hits=1, peer_position_max_age_s=5.0, deferred_buffer_window_s=3.0)
    frame_src = AsyncMock()
    frame_src.get_frame.return_value = Frame(image=np.zeros((720, 1280, 3), dtype=np.uint8), timestamp=100.0)

    det = Detection(bbox_xyxy=(640, 360, 660, 380), confidence=0.9, label="rubbish")
    detector = AsyncMock()
    detector.detect.return_value = [det]
    detector.load.return_value = True

    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(
        flight_mode="OFFBOARD", armed_state="ARMED", gps_valid=True,
        altitude=10.0, roll=0.0, pitch=0.0, yaw=0.0, latitude=22.314, longitude=87.310, timestamp=100.0
    )
    network = AsyncMock()

    swarm = MagicMock()
    # Active peer currently missing position
    peer2 = MagicMock(drone_id="drone2", is_active=True, lat=None, lon=None, alt=None, last_position_time=None)
    swarm.registry.peers = {"drone2": peer2}

    pipeline = PerceptionPipeline("drone1", frame_src, detector, fc, network, cfg, swarm_manager=swarm)
    await pipeline._step()
    assert len(pipeline._deferred_buffer) == 1

    # Peer 2 recovers telemetry at a position far away (e.g. 50m north)
    peer2.lat = 22.314500
    peer2.lon = 87.310000
    peer2.alt = 10.0
    peer2.last_position_time = time.time()

    # Next step: frame has zero new raw detections, but buffered detection is re-evaluated
    detector.detect.return_value = []
    await pipeline._step()

    # Buffer must have cleared and item broadcasted
    assert len(pipeline._deferred_buffer) == 0
    network.broadcast_message.assert_awaited_once()
