"""Perception preflight validation for dependencies and model weights."""

from pathlib import Path
import sys
from typing import Optional, Tuple
from DroneOS.shared.utils.logger import setup_logger

logger = setup_logger("PerceptionPreflight")


class PerceptionStatus(dict):
    """
    Structured perception status (status, reason, state) with clean equality
    and string formatting for health/diagnostics messages.
    """

    def __init__(self, status: str = "OK", reason: str = ""):
        super().__init__(status=status, reason=reason, state=status)
        self.status = status
        self.reason = reason
        self.state = status

    def __str__(self) -> str:
        return f"{self.status}: {self.reason}" if self.reason else self.status

    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            return self.get("status") == other or self.get("state") == other or str(self) == other
        return super().__eq__(other)


def run_perception_preflight(
    perception_cfg,
    repo_root: Optional[Path] = None,
) -> Tuple[str, str]:
    """
    Verify perception dependencies, imports, and weights existence before running.
    Returns (status, reason) where status is 'OK', 'DEGRADED', or 'DISABLED'.
    Logs one clear ERROR with the exact fix if degraded.
    """
    if not perception_cfg or not getattr(perception_cfg, "enabled", False):
        return ("DISABLED", "perception disabled in configuration")

    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent

    missing_deps = []
    try:
        import numpy
    except ImportError:
        missing_deps.append("numpy")

    try:
        import cv2
    except ImportError:
        missing_deps.append("opencv-python-headless")

    raw_weights = getattr(perception_cfg, "weights", "models/garbage.pt")
    weights_path = Path(raw_weights)
    if not weights_path.is_absolute():
        weights_path = repo_root / weights_path

    is_onnx = weights_path.suffix.lower() == ".onnx"
    if is_onnx:
        try:
            import onnxruntime
        except ImportError:
            missing_deps.append("onnxruntime")

    try:
        import ultralytics
    except ImportError:
        missing_deps.append("ultralytics")

    if missing_deps:
        fix = f"pip install {' '.join(missing_deps)}"
        err_msg = (
            f"Perception preflight failed: missing required package(s): {', '.join(missing_deps)}. "
            f"Fix: Run '{fix}'. (Note: ultralytics is AGPL-3.0 licensed)."
        )
        logger.error(err_msg)
        return ("DEGRADED", f"missing imports: {', '.join(missing_deps)}")

    if not weights_path.exists():
        err_msg = (
            f"Perception preflight failed: weights file not found at '{weights_path}'. "
            f"Fix: Place model weights at '{weights_path}' or update perception.yaml weights path."
        )
        logger.error(err_msg)
        return ("DEGRADED", f"weights file not found: {weights_path.name}")

    logger.info("Perception preflight check passed: dependencies and weights verified (OK).")
    return ("OK", "")
