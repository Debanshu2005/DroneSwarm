import json
import logging
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from DroneOS.perception import preflight
from scripts import verify_detector_stack


def _make_manifest(tmp_path: Path, expected_pt: float = 0.70, tol: float = 0.05) -> Path:
    golden_dir = tmp_path / "assets" / "perception" / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)
    img = np.zeros((32, 32, 3), dtype=np.uint8)
    cv2.imwrite(str(golden_dir / "sample.jpg"), img)
    manifest = {
        "imgsz": 640,
        "confidence": 0.55,
        "iou": 0.5,
        "default_tolerance": tol,
        "frames": [
            {
                "file": "sample.jpg",
                "expected_pt": expected_pt,
                "expected_onnx": 0.65,
                "tolerance": tol,
            }
        ],
    }
    manifest_path = golden_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    weights_path = tmp_path / "models" / "garbage.pt"
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.write_bytes(b"dummy")
    return manifest_path


def _mock_detector_cls(score: float, load_ok: bool = True):
    class FakeDetector:
        def __init__(self, **kwargs):
            self.last_timing = {}
            self.closed = False

        def load(self) -> bool:
            return load_ok

        def _sync_predict(self, image):
            self.last_timing = {"max_raw_conf": score}
            return []

        def close(self):
            self.closed = True

    return FakeDetector


def test_verify_golden_frames_passes_within_tolerance(tmp_path, monkeypatch):
    manifest_path = _make_manifest(tmp_path, expected_pt=0.70, tol=0.05)
    monkeypatch.setattr("DroneOS.perception.detector.GarbageDetector", _mock_detector_cls(0.72))
    status, reason, results = preflight.verify_golden_frames(
        weights_path=tmp_path / "models" / "garbage.pt",
        manifest_path=manifest_path,
    )
    assert status == "OK"
    assert reason == ""
    assert len(results) == 1
    assert results[0]["ok"] is True


def test_verify_golden_frames_detects_drift(tmp_path, monkeypatch):
    manifest_path = _make_manifest(tmp_path, expected_pt=0.70, tol=0.05)
    monkeypatch.setattr("DroneOS.perception.detector.GarbageDetector", _mock_detector_cls(0.40))
    status, reason, results = preflight.verify_golden_frames(
        weights_path=tmp_path / "models" / "garbage.pt",
        manifest_path=manifest_path,
    )
    assert status == "DEGRADED"
    assert "golden-frame drift" in reason
    assert results[0]["ok"] is False


def test_verify_golden_frames_handles_load_failure(tmp_path, monkeypatch):
    manifest_path = _make_manifest(tmp_path)
    monkeypatch.setattr("DroneOS.perception.detector.GarbageDetector", _mock_detector_cls(0.0, load_ok=False))
    status, reason, _ = preflight.verify_golden_frames(
        weights_path=tmp_path / "models" / "garbage.pt",
        manifest_path=manifest_path,
    )
    assert status == "DEGRADED"
    assert "could not load" in reason


def test_preflight_logs_warning_and_returns_degraded_on_drift(tmp_path, monkeypatch, caplog):
    _make_manifest(tmp_path, expected_pt=0.70, tol=0.05)
    monkeypatch.setattr("DroneOS.perception.detector.GarbageDetector", _mock_detector_cls(0.20))
    monkeypatch.setattr(preflight, "_is_module_available", lambda name: True)
    cfg = SimpleNamespace(enabled=True, weights="models/garbage.pt", imgsz=640, confidence=0.55, iou=0.5, device="cpu")

    with caplog.at_level(logging.WARNING):
        status, reason = preflight.run_perception_preflight(cfg, repo_root=tmp_path)

    assert status == "DEGRADED"
    assert "golden-frame drift" in reason
    assert any("golden-frame check degraded" in rec.message for rec in caplog.records)


def test_cli_verify_detector_stack_exit_codes(tmp_path, monkeypatch):
    manifest_path = _make_manifest(tmp_path, expected_pt=0.70, tol=0.05)
    weights_path = tmp_path / "models" / "garbage.pt"

    monkeypatch.setattr("DroneOS.perception.detector.GarbageDetector", _mock_detector_cls(0.71))
    assert verify_detector_stack.main(["--weights", str(weights_path), "--manifest", str(manifest_path)]) == 0

    monkeypatch.setattr("DroneOS.perception.detector.GarbageDetector", _mock_detector_cls(0.10))
    assert verify_detector_stack.main(["--weights", str(weights_path), "--manifest", str(manifest_path)]) == 1


def test_preflight_logs_versions_and_warns_on_ultralytics_mismatch(tmp_path, monkeypatch, caplog):
    weights_path = tmp_path / "models" / "garbage.pt"
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.write_bytes(b"dummy")

    monkeypatch.setattr(preflight, "_is_module_available", lambda name: True)
    monkeypatch.setattr(
        preflight,
        "_get_runtime_versions",
        lambda: {
            "ultralytics": "8.3.250",
            "onnxruntime": "1.23.2",
            "opencv": "4.11.0.86",
            "numpy": "2.2.2",
            "torch": "2.9.1",
        },
    )
    monkeypatch.setattr(
        preflight,
        "_read_checkpoint_metadata",
        lambda p: {"format": "pt", "training_ultralytics": "8.4.211", "date": "2026-11-13"},
    )
    cfg = SimpleNamespace(enabled=True, weights="models/garbage.pt", imgsz=640, confidence=0.55, iou=0.5, device="cpu")

    with caplog.at_level(logging.INFO):
        status, reason = preflight.run_perception_preflight(cfg, repo_root=tmp_path)

    assert status == "DEGRADED"
    assert "8.3.250" in reason and "8.4.211" in reason
    assert any("Perception runtime versions:" in rec.message for rec in caplog.records)
    assert any(
        rec.levelno == logging.WARNING and "version mismatch" in rec.message
        for rec in caplog.records
    )


def test_preflight_ok_when_ultralytics_matches_and_logs_onnx_metadata(tmp_path, monkeypatch, caplog):
    pt_path = tmp_path / "models" / "garbage.pt"
    pt_path.parent.mkdir(parents=True, exist_ok=True)
    pt_path.write_bytes(b"dummy")
    onnx_path = tmp_path / "models" / "garbage.onnx"
    onnx_path.write_bytes(b"dummy")

    monkeypatch.setattr(preflight, "_is_module_available", lambda name: True)
    monkeypatch.setattr(
        preflight,
        "_get_runtime_versions",
        lambda: {
            "ultralytics": "8.4.211",
            "onnxruntime": "1.23.2",
            "opencv": "4.11.0.86",
            "numpy": "2.2.2",
            "torch": "2.9.1",
        },
    )
    monkeypatch.setattr(
        preflight,
        "_read_checkpoint_metadata",
        lambda p: (
            {"format": "onnx", "producer": "pytorch", "version": "8.3.250", "date": "2026-11-13"}
            if p.suffix == ".onnx"
            else {"format": "pt", "training_ultralytics": "8.4.211", "date": "2026-11-13"}
        ),
    )

    cfg_pt = SimpleNamespace(enabled=True, weights="models/garbage.pt")
    with caplog.at_level(logging.INFO):
        assert preflight.run_perception_preflight(cfg_pt, repo_root=tmp_path) == ("OK", "")

    caplog.clear()
    cfg_onnx = SimpleNamespace(enabled=True, weights="models/garbage.onnx")
    with caplog.at_level(logging.INFO):
        assert preflight.run_perception_preflight(cfg_onnx, repo_root=tmp_path) == ("OK", "")
    assert any("garbage.onnx" in rec.message and "pytorch" in rec.message for rec in caplog.records)
