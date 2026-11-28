"""Perception preflight validation for dependencies and model weights."""

from pathlib import Path
from typing import Optional, Tuple
from DroneOS1.shared.utils.logger import setup_logger

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


def _is_module_available(mod_name: str) -> bool:
    import sys
    if mod_name in sys.modules:
        return sys.modules[mod_name] is not None
    import importlib.util
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ValueError, AttributeError):
        return True


def _get_module_version(mod_name: str, pkg_name: Optional[str] = None) -> str:
    """Return installed version string for mod_name, or 'not_installed'."""
    import importlib
    import importlib.metadata
    import sys

    try:
        mod = sys.modules[mod_name] if mod_name in sys.modules else importlib.import_module(mod_name)
    except Exception:
        return "not_installed"
    if mod is None:
        return "not_installed"
    ver = getattr(mod, "__version__", None)
    if isinstance(ver, str) and ver:
        return ver
    try:
        return importlib.metadata.version(pkg_name or mod_name)
    except Exception:
        return "unknown"


def _get_runtime_versions() -> dict:
    """Collect versions of perception runtime libraries."""
    return {
        "ultralytics": _get_module_version("ultralytics"),
        "onnxruntime": _get_module_version("onnxruntime"),
        "opencv": _get_module_version("cv2", "opencv-python"),
        "numpy": _get_module_version("numpy"),
        "torch": _get_module_version("torch"),
    }


def _read_checkpoint_metadata(weights_path: Path) -> dict:
    """Read training/export metadata from .pt or .onnx weights without failing on dummy files."""
    suffix = weights_path.suffix.lower()
    if suffix == ".pt":
        try:
            import torch

            ckpt = torch.load(str(weights_path), map_location="cpu", weights_only=False)
            if isinstance(ckpt, dict):
                ver = ckpt.get("version")
                return {
                    "format": "pt",
                    "training_ultralytics": str(ver) if ver else None,
                    "date": str(ckpt.get("date")) if ckpt.get("date") else None,
                }
        except Exception as exc:
            return {"format": "pt", "error": str(exc)}
    elif suffix == ".onnx":
        try:
            import onnxruntime as ort

            sess = ort.InferenceSession(str(weights_path), providers=["CPUExecutionProvider"])
            meta = sess.get_modelmeta()
            custom = dict(meta.custom_metadata_map or {})
            return {
                "format": "onnx",
                "producer": meta.producer_name or None,
                "version": custom.get("version"),
                "date": custom.get("date"),
            }
        except Exception as exc:
            return {"format": "onnx", "error": str(exc)}
    return {"format": suffix.lstrip(".")}


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

    if not _is_module_available("numpy"):
        missing_deps.append("numpy")

    if not _is_module_available("cv2"):
        missing_deps.append("opencv-python-headless")

    raw_weights = getattr(perception_cfg, "weights", "models/garbage.pt")
    weights_path = Path(raw_weights)
    if not weights_path.is_absolute():
        weights_path = repo_root / weights_path

    is_onnx = weights_path.suffix.lower() == ".onnx"
    if is_onnx:
        if not _is_module_available("onnxruntime"):
            missing_deps.append("onnxruntime")
    else:
        if not _is_module_available("ultralytics"):
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

    versions = _get_runtime_versions()
    ckpt_meta = _read_checkpoint_metadata(weights_path)
    logger.info(
        "Perception runtime versions: ultralytics=%s, onnxruntime=%s, opencv=%s, numpy=%s, torch=%s; "
        "checkpoint (%s) metadata: %s",
        versions["ultralytics"],
        versions["onnxruntime"],
        versions["opencv"],
        versions["numpy"],
        versions["torch"],
        weights_path.name,
        ckpt_meta,
    )

    ckpt_ultralytics = ckpt_meta.get("training_ultralytics")
    installed_ultralytics = versions["ultralytics"]
    if (
        installed_ultralytics not in ("not_installed", "unknown")
        and ckpt_ultralytics
        and installed_ultralytics != ckpt_ultralytics
    ):
        reason = (
            f"ultralytics version mismatch: installed {installed_ultralytics} "
            f"!= checkpoint training {ckpt_ultralytics}"
        )
        logger.warning("Perception preflight version mismatch: %s", reason)
        return ("DEGRADED", reason)

    manifest_path = repo_root / "assets" / "perception" / "golden" / "manifest.json"
    if manifest_path.exists():
        import sys
        from unittest.mock import NonCallableMock

        backend_mod = sys.modules.get("onnxruntime" if is_onnx else "ultralytics")
        if not isinstance(backend_mod, NonCallableMock):
            status, reason, _ = verify_golden_frames(
                weights_path=weights_path,
                manifest_path=manifest_path,
                imgsz=int(getattr(perception_cfg, "imgsz", 640)),
                confidence=float(getattr(perception_cfg, "confidence", 0.55)),
                iou=float(getattr(perception_cfg, "iou", 0.5)),
                device=str(getattr(perception_cfg, "device", "auto")),
            )
            if status != "OK":
                logger.warning("Perception preflight golden-frame check degraded: %s", reason)
                return (status, reason)

    logger.info("Perception preflight check passed: dependencies and weights verified (OK).")
    return ("OK", "")


def verify_golden_frames(
    weights_path: Path,
    manifest_path: Path,
    imgsz: int = 640,
    confidence: float = 0.55,
    iou: float = 0.5,
    device: str = "auto",
) -> Tuple[str, str, list]:
    """Run golden-frame confidence check; returns (status, reason, per_frame_results)."""
    import json
    import cv2
    from DroneOS1.perception.detector import GarbageDetector

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    is_onnx = weights_path.suffix.lower() == ".onnx"
    default_tol = float(manifest.get("default_tolerance", 0.05))
    frames = manifest.get("frames", [])
    if not frames:
        return ("OK", "", [])

    det = GarbageDetector(
        weights_path=str(weights_path),
        imgsz=int(manifest.get("imgsz", imgsz)),
        confidence=float(manifest.get("confidence", confidence)),
        iou=float(manifest.get("iou", iou)),
        device=device,
    )
    if not det.load():
        return ("DEGRADED", f"golden-frame check failed: could not load {weights_path.name}", [])

    results = []
    drifted = []
    try:
        for entry in frames:
            name = entry["file"]
            img_path = manifest_path.parent / name
            img = cv2.imread(str(img_path))
            if img is None:
                drifted.append(f"{name} (unreadable)")
                results.append({"file": name, "expected": None, "actual": None, "ok": False})
                continue
            det._sync_predict(img)
            actual = float(det.last_timing.get("max_raw_conf", 0.0))
            expected = float(entry["expected_onnx"] if is_onnx and "expected_onnx" in entry else entry["expected_pt"])
            tol = float(entry.get("tolerance", default_tol))
            ok = abs(actual - expected) <= tol
            results.append({"file": name, "expected": expected, "actual": actual, "tolerance": tol, "ok": ok})
            if not ok:
                drifted.append(f"{name} (expected {expected:.4f}+/-{tol:.2f}, got {actual:.4f})")
    finally:
        det.close()

    if drifted:
        return ("DEGRADED", "golden-frame drift: " + "; ".join(drifted), results)
    return ("OK", "", results)
