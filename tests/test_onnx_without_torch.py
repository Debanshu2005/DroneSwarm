"""
Test suite for pure ONNX Runtime perception without PyTorch / Ultralytics dependencies.
Covers:
1. Preflight validation requiring onnxruntime only (not ultralytics) for .onnx models.
2. Letterbox preprocessing with aspect-ratio preservation and unletterboxing coordinate transform.
3. ONNX model metadata parsing for class labels.
4. Inference execution on synthetic ONNX models (both anchor [1, 5, N] and end-to-end [1, N, 6] formats).
5. Defensively shared candidate bounding-box filtering without integer truncation.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
import pytest

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.preflight import run_perception_preflight
from DroneOS.shared.config.models import PerceptionConfig


def create_synthetic_anchor_onnx(save_path: Path):
    """Generate a tiny valid ONNX model emitting anchor format [1, 5, 1]."""
    import onnx
    from onnx import helper, TensorProto

    input_info = helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])
    output_info = helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 5, 1])

    # [cx, cy, w, h, score] = [320.5, 320.5, 60.25, 40.75, 0.88]
    val = np.array([[[320.5], [320.5], [60.25], [40.75], [0.88]]], dtype=np.float32)
    const_tensor = helper.make_tensor("const_anchor", TensorProto.FLOAT, [1, 5, 1], val.tobytes(), raw=True)
    node = helper.make_node("Constant", [], ["output0"], value=const_tensor)

    graph = helper.make_graph([node], "synthetic_anchor_net", [input_info], [output_info])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=10)
    meta = model.metadata_props.add()
    meta.key = "names"
    meta.value = "{0: 'rubbish'}"
    onnx.checker.check_model(model)
    onnx.save(model, str(save_path))


def create_synthetic_e2e_onnx(save_path: Path):
    """Generate a tiny valid ONNX model emitting end-to-end NMS format [1, 1, 6]."""
    import onnx
    from onnx import helper, TensorProto

    input_info = helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])
    output_info = helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 1, 6])

    # [x1, y1, x2, y2, score, class_id] = [120.25, 150.5, 240.75, 270.25, 0.92, 0.0]
    val = np.array([[[120.25, 150.5, 240.75, 270.25, 0.92, 0.0]]], dtype=np.float32)
    const_tensor = helper.make_tensor("const_e2e", TensorProto.FLOAT, [1, 1, 6], val.tobytes(), raw=True)
    node = helper.make_node("Constant", [], ["output0"], value=const_tensor)

    graph = helper.make_graph([node], "synthetic_e2e_net", [input_info], [output_info])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=10)
    meta = model.metadata_props.add()
    meta.key = "names"
    meta.value = "{0: 'rubbish'}"
    onnx.checker.check_model(model)
    onnx.save(model, str(save_path))


def test_preflight_onnx_does_not_require_ultralytics(tmp_path):
    """When using .onnx weights, preflight must succeed even if ultralytics is absent."""
    weights_path = tmp_path / "model.onnx"
    weights_path.write_bytes(b"dummy")

    cfg = PerceptionConfig(enabled=True, weights=str(weights_path))

    # Simulate environment where ultralytics is missing
    with patch.dict("sys.modules", {"ultralytics": None}):
        status, reason = run_perception_preflight(cfg, repo_root=tmp_path)
        assert status == "OK", f"Expected OK without ultralytics for ONNX, got {status}: {reason}"


def test_preflight_pt_requires_ultralytics(tmp_path):
    """When using .pt weights, preflight must fail cleanly with DEGRADED if ultralytics is missing."""
    weights_path = tmp_path / "model.pt"
    weights_path.write_bytes(b"dummy")

    cfg = PerceptionConfig(enabled=True, weights=str(weights_path))

    with patch.dict("sys.modules", {"ultralytics": None}):
        status, reason = run_perception_preflight(cfg, repo_root=tmp_path)
        assert status == "DEGRADED"
        assert "ultralytics" in reason


def test_letterbox_preprocessing_aspect_ratio_preservation():
    """Verify letterbox resizes maintaining aspect ratio with centered padding."""
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    padded, r, (pad_left, pad_top) = GarbageDetector._letterbox_image(img, (640, 640))

    assert padded.shape == (640, 640, 3)
    # Ratio should be 640 / 1280 = 0.5
    assert abs(r - 0.5) < 1e-4
    # Height unpadded: 720 * 0.5 = 360; top padding: (640 - 360)/2 = 140
    assert abs(pad_top - 140.0) < 1.0
    assert abs(pad_left - 0.0) < 1.0


def test_pure_onnxruntime_anchor_inference(tmp_path):
    """Verify pure ONNX Runtime inference parses anchor format, metadata, and keeps float bounds."""
    onnx_file = tmp_path / "test_anchor.onnx"
    create_synthetic_anchor_onnx(onnx_file)

    detector = GarbageDetector(weights_path=str(onnx_file), imgsz=640, confidence=0.5)

    # Force pure ONNX Runtime fallback by simulating missing ultralytics
    with patch.dict("sys.modules", {"ultralytics": None}):
        assert detector.load() is True
        assert detector._ort_session is not None
        assert detector._model is None
        assert detector._class_names == {0: "rubbish"}

        dummy_img = np.zeros((640, 640, 3), dtype=np.uint8)
        detections = asyncio.run(detector.detect(dummy_img))

        assert len(detections) == 1
        det = detections[0]
        assert det.label == "rubbish"
        assert abs(det.confidence - 0.88) < 1e-3

        # Candidate was cx=320.5, cy=320.5, w=60.25, h=40.75
        # x1 = 320.5 - 30.125 = 290.375, x2 = 350.625
        # y1 = 320.5 - 20.375 = 300.125, y2 = 340.875
        x1, y1, x2, y2 = det.bbox_xyxy
        assert isinstance(x1, float)
        assert abs(x1 - 290.375) < 1e-2
        assert abs(y1 - 300.125) < 1e-2
        assert abs(x2 - 350.625) < 1e-2
        assert abs(y2 - 340.875) < 1e-2

    detector.close()


def test_pure_onnxruntime_end_to_end_inference(tmp_path):
    """Verify pure ONNX Runtime handles end-to-end NMS output format [1, N, 6]."""
    onnx_file = tmp_path / "test_e2e.onnx"
    create_synthetic_e2e_onnx(onnx_file)

    detector = GarbageDetector(weights_path=str(onnx_file), imgsz=640, confidence=0.5)

    with patch.dict("sys.modules", {"ultralytics": None}):
        assert detector.load() is True

        dummy_img = np.zeros((640, 640, 3), dtype=np.uint8)
        detections = asyncio.run(detector.detect(dummy_img))

        assert len(detections) == 1
        det = detections[0]
        assert det.label == "rubbish"
        assert abs(det.confidence - 0.92) < 1e-3
        x1, y1, x2, y2 = det.bbox_xyxy
        assert abs(x1 - 120.25) < 1e-2
        assert abs(y1 - 150.5) < 1e-2
        assert abs(x2 - 240.75) < 1e-2
        assert abs(y2 - 270.25) < 1e-2

    detector.close()


def test_shared_candidate_box_filtering():
    """Verify shared candidate box filter rejects non-finite, degenerate, and out-of-spec boxes."""
    detector = GarbageDetector(
        weights_path="dummy.onnx",
        min_area_ratio=0.005,
        max_area_ratio=0.5,
        max_aspect_ratio=4.0,
    )

    # Valid candidate
    d = detector.filter_candidate_box(10.5, 10.5, 100.5, 100.5, 0.85, "rubbish", img_w=1000, img_h=1000)
    assert d is not None
    assert d.bbox_xyxy == (10.5, 10.5, 100.5, 100.5)

    # Non-finite values
    assert detector.filter_candidate_box(float("nan"), 10.0, 50.0, 50.0, 0.8, "rubbish") is None
    assert detector.filter_candidate_box(10.0, 10.0, float("inf"), 50.0, 0.8, "rubbish") is None

    # Degenerate zero-area
    assert detector.filter_candidate_box(10.0, 10.0, 10.0, 50.0, 0.8, "rubbish") is None

    # Aspect ratio exceeded (e.g. 10x100 -> aspect ratio 10 > 4.0)
    assert detector.filter_candidate_box(0.0, 0.0, 10.0, 100.0, 0.8, "rubbish", img_w=1000, img_h=1000) is None
