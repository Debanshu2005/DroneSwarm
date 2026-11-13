"""Unit tests for GarbageDetector loading, inference, filtering, and error handling."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.types import Detection


@pytest.mark.asyncio
async def test_detector_missing_checkpoint():
    """Missing weights file must fail gracefully and return empty detections."""
    detector = GarbageDetector(weights_path="models/non_existent_weights_xyz.pt")
    assert not detector.load()
    assert not await detector.ensure_loaded()

    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    dets = await detector.detect(dummy_img)
    assert dets == []
    detector.close()


@pytest.mark.asyncio
async def test_detector_invalid_checkpoint(tmp_path):
    """Corrupt weights file must not crash; returns empty list and logs error."""
    corrupt_file = tmp_path / "corrupt.pt"
    corrupt_file.write_text("not a real pytorch model weights file")

    detector = GarbageDetector(weights_path=str(corrupt_file))
    assert not detector.load()
    assert not await detector.ensure_loaded()

    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    dets = await detector.detect(dummy_img)
    assert dets == []
    detector.close()


@pytest.mark.asyncio
async def test_detector_mocked_prediction_and_bbox_conversion():
    """Verify bounding box extraction, confidence, and class label mapping."""
    detector = GarbageDetector(weights_path="models/garbage.pt", confidence=0.35, iou=0.5)

    mock_box1 = MagicMock()
    mock_box1.conf = [0.85]
    mock_box1.cls = [0]
    mock_box1.xyxy = [[10.0, 20.0, 100.0, 150.0]]

    mock_box2 = MagicMock()
    mock_box2.conf = [0.42]
    mock_box2.cls = [0]
    mock_box2.xyxy = [[200.0, 250.0, 300.0, 350.0]]

    mock_result = MagicMock()
    mock_result.boxes = [mock_box1, mock_box2]
    mock_result.names = {0: "rubbish"}

    mock_model = MagicMock()
    mock_model.predict.return_value = [mock_result]

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)

        assert len(dets) == 2
        d1 = dets[0]
        assert d1.label == "rubbish"
        assert pytest.approx(d1.confidence, abs=1e-3) == 0.85
        assert d1.bbox_xyxy == (10.0, 20.0, 100.0, 150.0)

        d2 = dets[1]
        assert d2.label == "rubbish"
        assert pytest.approx(d2.confidence, abs=1e-3) == 0.42

    detector.close()


@pytest.mark.asyncio
async def test_detector_class_filtering():
    """Only classes listed in `classes` configuration should be returned."""
    detector = GarbageDetector(
        weights_path="models/garbage.pt",
        classes=["target_waste"],
    )

    mock_box1 = MagicMock()
    mock_box1.conf = [0.9]
    mock_box1.cls = [0]
    mock_box1.xyxy = [[10.0, 10.0, 50.0, 50.0]]

    mock_box2 = MagicMock()
    mock_box2.conf = [0.8]
    mock_box2.cls = [1]
    mock_box2.xyxy = [[60.0, 60.0, 90.0, 90.0]]

    mock_result = MagicMock()
    mock_result.boxes = [mock_box1, mock_box2]
    mock_result.names = {0: "target_waste", 1: "other_class"}

    mock_model = MagicMock()
    mock_model.predict.return_value = [mock_result]

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)

        assert len(dets) == 1
        assert dets[0].label == "target_waste"

    detector.close()


@pytest.mark.asyncio
async def test_detector_empty_prediction():
    """Empty predictions must return an empty list gracefully."""
    detector = GarbageDetector(weights_path="models/garbage.pt")

    mock_result = MagicMock()
    mock_result.boxes = []
    mock_result.names = {0: "rubbish"}

    mock_model = MagicMock()
    mock_model.predict.return_value = [mock_result]

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)
        assert dets == []

    detector.close()


@pytest.mark.asyncio
async def test_detector_cuda_to_cpu_fallback():
    """If inference on CUDA fails, detector must attempt fallback to CPU."""
    detector = GarbageDetector(
        weights_path="models/garbage.pt",
        device="cuda:0",
    )

    mock_model = MagicMock()
    # First call with cuda fails, second call with cpu succeeds
    def mock_predict(**kwargs):
        if kwargs.get("device") == "cuda:0":
            raise RuntimeError("CUDA out of memory / device error")
        res = MagicMock()
        b = MagicMock()
        b.conf = [0.8]
        b.cls = [0]
        b.xyxy = [[1.0, 2.0, 3.0, 4.0]]
        res.boxes = [b]
        res.names = {0: "rubbish"}
        return [res]

    mock_model.predict.side_effect = mock_predict

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)

        assert len(dets) == 1
        assert dets[0].label == "rubbish"

    detector.close()


@pytest.mark.asyncio
async def test_detector_coordinate_clipping_and_normalization():
    """Verify coordinate normalization (x1 > x2) and boundary clipping."""
    detector = GarbageDetector(weights_path="models/garbage.pt")

    # Box 1: inverted coordinates [100, 150, 50, 80] -> [50, 80, 100, 150]
    mock_box1 = MagicMock()
    mock_box1.conf = [0.9]
    mock_box1.cls = [0]
    mock_box1.xyxy = [[100.0, 150.0, 50.0, 80.0]]

    # Box 2: out-of-bounds coordinates [-20.0, -10.0, 700.0, 500.0] on 640x480 image
    mock_box2 = MagicMock()
    mock_box2.conf = [0.85]
    mock_box2.cls = [0]
    mock_box2.xyxy = [[-20.0, -10.0, 700.0, 500.0]]

    mock_result = MagicMock()
    mock_result.boxes = [mock_box1, mock_box2]
    mock_result.names = {0: "rubbish"}

    mock_model = MagicMock()
    mock_model.predict.return_value = [mock_result]

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)

        assert len(dets) == 2
        # Inverted box normalized:
        assert dets[0].bbox_xyxy == (50.0, 80.0, 100.0, 150.0)
        # Out-of-bounds box clipped to [0, 0, 640, 480]:
        assert dets[1].bbox_xyxy == (0.0, 0.0, 640.0, 480.0)

    detector.close()


@pytest.mark.asyncio
async def test_detector_degenerate_and_nan_box_rejection():
    """Verify rejection of zero-area boxes, fully out-of-bounds boxes, and non-finite coords."""
    detector = GarbageDetector(weights_path="models/garbage.pt")

    def make_box(conf, cls_val, xyxy):
        b = MagicMock()
        b.conf = [conf]
        b.cls = [cls_val]
        b.xyxy = [xyxy]
        return b

    # Degenerate: zero width
    b_zero_w = make_box(0.9, 0, [50.0, 50.0, 50.0, 100.0])
    # Degenerate: zero height
    b_zero_h = make_box(0.9, 0, [50.0, 50.0, 100.0, 50.0])
    # Fully outside image (e.g. x1=700, x2=800 for 640-wide image clips to 640, 640 -> 0 width)
    b_outside = make_box(0.9, 0, [700.0, 100.0, 800.0, 200.0])
    # Non-finite: NaN
    b_nan = make_box(0.9, 0, [float("nan"), 50.0, 100.0, 100.0])
    # Non-finite: Inf
    b_inf = make_box(0.9, 0, [10.0, float("inf"), 100.0, 100.0])
    # Valid box
    b_valid = make_box(0.95, 0, [20.0, 30.0, 80.0, 90.0])

    mock_result = MagicMock()
    mock_result.boxes = [b_zero_w, b_zero_h, b_outside, b_nan, b_inf, b_valid]
    mock_result.names = {0: "rubbish"}

    mock_model = MagicMock()
    mock_model.predict.return_value = [mock_result]

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)

        # Only the valid box should survive
        assert len(dets) == 1
        assert dets[0].bbox_xyxy == (20.0, 30.0, 80.0, 90.0)

    detector.close()


@pytest.mark.asyncio
async def test_detector_non_square_aspect_ratio():
    """Verify proper handling and clipping on non-square resolution (1920x1080)."""
    detector = GarbageDetector(weights_path="models/garbage.pt")

    b1 = MagicMock()
    b1.conf = [0.88]
    b1.cls = [0]
    b1.xyxy = [[100.0, 200.0, 1850.0, 1050.0]]

    # Box extending past width 1920 and height 1080
    b2 = MagicMock()
    b2.conf = [0.75]
    b2.cls = [0]
    b2.xyxy = [[1800.0, 1000.0, 2000.0, 1200.0]]

    mock_result = MagicMock()
    mock_result.boxes = [b1, b2]
    mock_result.names = {0: "rubbish"}

    mock_model = MagicMock()
    mock_model.predict.return_value = [mock_result]

    with patch.object(detector, "load", return_value=True):
        detector._model = mock_model
        # 1080 rows, 1920 cols
        dummy_img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        dets = await detector.detect(dummy_img)

        assert len(dets) == 2
        assert dets[0].bbox_xyxy == (100.0, 200.0, 1850.0, 1050.0)
        assert dets[1].bbox_xyxy == (1800.0, 1000.0, 1920.0, 1080.0)

    detector.close()


