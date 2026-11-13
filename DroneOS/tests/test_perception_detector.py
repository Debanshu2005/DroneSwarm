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

