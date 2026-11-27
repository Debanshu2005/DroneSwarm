"""Tests for device and execution provider selection (auto/cpu/cuda)."""

import importlib
from unittest.mock import MagicMock, patch
import pytest

from DroneOS.perception.detector import GarbageDetector, resolve_device_and_provider


@pytest.fixture(scope="module", autouse=True)
def _ensure_torch_loaded_before_sys_modules_patch():
    """Pre-import torch if installed so patch.dict('sys.modules', {'torch': ...})
    restores the original module instead of deleting 'torch' and triggering a
    re-import RuntimeError ('_has_torch_function already has a docstring')."""
    try:
        importlib.import_module("torch")
    except ImportError:
        pass


def test_resolve_device_auto_cuda_available(monkeypatch):
    """When torch has CUDA available and device is auto, resolve cuda and torch.cuda."""
    monkeypatch.delenv("DRONEOS_PERCEPTION_DEVICE", raising=False)
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    with patch.dict("sys.modules", {"torch": mock_torch}):
        dev, prov, src = resolve_device_and_provider(requested_device="auto", is_onnx=False)
        assert dev == "cuda"
        assert prov == "torch.cuda"
        assert src == "auto"


def test_resolve_device_auto_cuda_unavailable(monkeypatch):
    """When torch lacks CUDA and device is auto, resolve cpu and torch.cpu."""
    monkeypatch.delenv("DRONEOS_PERCEPTION_DEVICE", raising=False)
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False

    with patch.dict("sys.modules", {"torch": mock_torch}):
        dev, prov, src = resolve_device_and_provider(requested_device="auto", is_onnx=False)
        assert dev == "cpu"
        assert prov == "torch.cpu"
        assert src == "auto"


def test_resolve_device_cuda_requested_available(monkeypatch):
    """When cuda is explicitly configured and available, source is yaml and dev is cuda."""
    monkeypatch.delenv("DRONEOS_PERCEPTION_DEVICE", raising=False)
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    with patch.dict("sys.modules", {"torch": mock_torch}):
        dev, prov, src = resolve_device_and_provider(requested_device="cuda", is_onnx=False)
        assert dev == "cuda"
        assert prov == "torch.cuda"
        assert src == "yaml"


def test_resolve_device_cuda_missing_fallback(monkeypatch, caplog):
    """When cuda is requested but missing, log warning and fallback to cpu."""
    monkeypatch.delenv("DRONEOS_PERCEPTION_DEVICE", raising=False)
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False

    with patch.dict("sys.modules", {"torch": mock_torch}):
        with caplog.at_level("WARNING"):
            dev, prov, src = resolve_device_and_provider(requested_device="cuda", is_onnx=False)
        assert dev == "cpu"
        assert prov == "torch.cpu"
        assert src == "yaml"
        assert any("CUDA" in record.message and "falling back to CPU" in record.message for record in caplog.records)


def test_resolve_device_env_override_takes_precedence(monkeypatch):
    """DRONEOS_PERCEPTION_DEVICE env override takes precedence over yaml config."""
    monkeypatch.setenv("DRONEOS_PERCEPTION_DEVICE", "cpu")
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    with patch.dict("sys.modules", {"torch": mock_torch}):
        dev, prov, src = resolve_device_and_provider(requested_device="cuda", is_onnx=False)
        assert dev == "cpu"
        assert prov == "torch.cpu"
        assert src == "env"


def test_resolve_device_env_override_cuda(monkeypatch):
    """DRONEOS_PERCEPTION_DEVICE=cuda selects cuda with source=env."""
    monkeypatch.setenv("DRONEOS_PERCEPTION_DEVICE", "cuda")
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    with patch.dict("sys.modules", {"torch": mock_torch}):
        dev, prov, src = resolve_device_and_provider(requested_device="cpu", is_onnx=False)
        assert dev == "cuda"
        assert prov == "torch.cuda"
        assert src == "env"


def test_resolve_device_onnx_provider_hierarchy(monkeypatch):
    """ONNX selects TensorRT > CUDA > CPU execution provider."""
    monkeypatch.delenv("DRONEOS_PERCEPTION_DEVICE", raising=False)
    mock_ort = MagicMock()
    mock_ort.get_available_providers.return_value = [
        "TensorrtExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]

    with patch.dict("sys.modules", {"onnxruntime": mock_ort}):
        dev, prov, src = resolve_device_and_provider(requested_device="auto", is_onnx=True)
        assert dev == "cuda"
        assert prov == "TensorrtExecutionProvider"
        assert src == "auto"

    mock_ort.get_available_providers.return_value = [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    with patch.dict("sys.modules", {"onnxruntime": mock_ort}):
        dev, prov, src = resolve_device_and_provider(requested_device="auto", is_onnx=True)
        assert dev == "cuda"
        assert prov == "CUDAExecutionProvider"
        assert src == "auto"

    mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
    with patch.dict("sys.modules", {"onnxruntime": mock_ort}):
        dev, prov, src = resolve_device_and_provider(requested_device="auto", is_onnx=True)
        assert dev == "cpu"
        assert prov == "CPUExecutionProvider"
        assert src == "auto"


def test_detector_fp16_flag_only_on_cuda(monkeypatch, tmp_path):
    """GarbageDetector sets fp16=True only when resolved_device is cuda."""
    dummy_weights = tmp_path / "model.pt"
    dummy_weights.touch()

    monkeypatch.delenv("DRONEOS_PERCEPTION_DEVICE", raising=False)
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True

    with patch.dict("sys.modules", {"torch": mock_torch, "ultralytics": MagicMock()}):
        det_cuda = GarbageDetector(weights_path=str(dummy_weights), device="cuda")
        det_cuda.load()
        assert det_cuda.resolved_device == "cuda"
        assert det_cuda.fp16 is True

    mock_torch.cuda.is_available.return_value = False
    with patch.dict("sys.modules", {"torch": mock_torch, "ultralytics": MagicMock()}):
        det_cpu = GarbageDetector(weights_path=str(dummy_weights), device="cpu")
        det_cpu.load()
        assert det_cpu.resolved_device == "cpu"
        assert det_cpu.fp16 is False
