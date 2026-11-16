"""Unit tests for ONNX model export, graph integrity, and checkpoint verification."""

import hashlib
from pathlib import Path
import numpy as np
import pytest


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def test_pytorch_weights_hash_integrity():
    """Verify models/garbage.pt hash matches the pristine reference hash."""
    weights_path = Path("models/garbage.pt")
    assert weights_path.exists(), "models/garbage.pt does not exist!"
    current_hash = compute_sha256(weights_path)
    expected_hash = "5e6fe97ead40c8d24edcdb5517b210bf8415ec23719beb8a575fdab3a4028d20"
    assert current_hash == expected_hash, (
        f"Model integrity violation! Expected {expected_hash}, got {current_hash}"
    )


def test_onnx_model_exists_and_valid():
    """Verify models/garbage.onnx exists and has valid graph structure."""
    onnx = pytest.importorskip("onnx")
    ort = pytest.importorskip("onnxruntime")

    onnx_path = Path("models/garbage.onnx")
    assert onnx_path.exists(), "models/garbage.onnx does not exist!"
    assert onnx_path.stat().st_size > 1_000_000, "ONNX file unexpectedly small"

    onnx_model = onnx.load(str(onnx_path))
    onnx.checker.check_model(onnx_model)

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    inputs = sess.get_inputs()
    outputs = sess.get_outputs()

    assert len(inputs) == 1
    assert inputs[0].name == "images"
    assert inputs[0].shape == [1, 3, 640, 640]

    assert len(outputs) == 1
    assert outputs[0].shape == [1, 5, 8400]


def test_onnx_runtime_cpu_inference():
    """Verify pure ONNX Runtime CPU inference executes without errors."""
    ort = pytest.importorskip("onnxruntime")

    onnx_path = Path("models/garbage.onnx")
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    dummy_input = np.zeros((1, 3, 640, 640), dtype=np.float32)
    outputs = sess.run(None, {"images": dummy_input})

    assert len(outputs) == 1
    assert outputs[0].shape == (1, 5, 8400)
    assert np.all(np.isfinite(outputs[0]))

