"""Tests for Phase 1: dependencies, preflight check, and perception_status diagnostics."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from DroneOS.core.diagnostics import SystemHealthReporter
from DroneOS.perception.preflight import PerceptionStatus, run_perception_preflight
from DroneOS.shared.config.models import PerceptionConfig
from DroneOS.shared.protocol.messages import DiagnosticsMessage


def test_preflight_disabled():
    cfg = PerceptionConfig(enabled=False)
    status, reason = run_perception_preflight(cfg)
    assert status == "DISABLED"
    assert "disabled" in reason.lower()


def test_preflight_missing_weights(tmp_path, caplog):
    cfg = PerceptionConfig(enabled=True, weights=str(tmp_path / "nonexistent.pt"))
    with caplog.at_level(logging.ERROR):
        status, reason = run_perception_preflight(cfg, repo_root=tmp_path)
    assert status == "DEGRADED"
    assert "not found" in reason.lower()
    # Ensure one clear ERROR with fix was logged
    errors = [rec.message for rec in caplog.records if rec.levelno == logging.ERROR]
    assert any("Perception preflight failed" in m and "Fix:" in m for m in errors)


def test_preflight_missing_ultralytics(tmp_path, caplog):
    # Create fake weights file so weights exist
    fake_weights = tmp_path / "garbage.pt"
    fake_weights.write_bytes(b"dummy")
    cfg = PerceptionConfig(enabled=True, weights=str(fake_weights))

    with patch.dict("sys.modules", {"ultralytics": None}):
        with caplog.at_level(logging.ERROR):
            status, reason = run_perception_preflight(cfg, repo_root=tmp_path)
    assert status == "DEGRADED"
    assert "ultralytics" in reason.lower()
    errors = [rec.message for rec in caplog.records if rec.levelno == logging.ERROR]
    assert any("Fix:" in m and "ultralytics" in m for m in errors)


def test_preflight_onnx_missing_onnxruntime(tmp_path, caplog):
    fake_onnx = tmp_path / "garbage.onnx"
    fake_onnx.write_bytes(b"dummy")
    cfg = PerceptionConfig(enabled=True, weights=str(fake_onnx))

    with patch.dict("sys.modules", {"onnxruntime": None}):
        with caplog.at_level(logging.ERROR):
            status, reason = run_perception_preflight(cfg, repo_root=tmp_path)
    assert status == "DEGRADED"
    assert "onnxruntime" in reason.lower()
    errors = [rec.message for rec in caplog.records if rec.levelno == logging.ERROR]
    assert any("Fix:" in m and "onnxruntime" in m for m in errors)


def test_preflight_all_valid(tmp_path):
    fake_weights = tmp_path / "garbage.pt"
    fake_weights.write_bytes(b"dummy")
    cfg = PerceptionConfig(enabled=True, weights=str(fake_weights))

    status, reason = run_perception_preflight(cfg, repo_root=tmp_path)
    assert status == "OK"
    assert reason == ""


def test_perception_status_diagnostics_message():
    network = MagicMock()
    network._active_tasks = set()
    network.known_endpoints = {}
    fc = MagicMock(_connected=True, _reconnecting=False)
    swarm = MagicMock()
    swarm.registry.get_all_peers.return_value = []
    mission = MagicMock()
    mission.status.state = "IDLE"

    ps = PerceptionStatus("OK", "")
    reporter = SystemHealthReporter(
        network, fc, swarm, mission, perception_status_fn=lambda: ps
    )
    report = reporter.get_full_report()

    assert "perception_status" in report
    assert report["perception_status"] == "OK"
    assert report["perception_status"]["status"] == "OK"

    # Verify message serialization
    msg = DiagnosticsMessage(sender_id="drone1", timestamp=1000.0, diagnostics=report)
    assert msg.diagnostics["perception_status"]["status"] == "OK"
