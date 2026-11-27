# Perception Environments & Reproducibility

This guide documents the perception runtime stacks tested on branch `garbage-detection`, why raw detector confidences differ across environments, and how to verify a deployment stack before flight.

## Root Cause of Cross-Environment Confidence Differences

Single-package ablation in isolated virtual environments on the same 100 empty-scene AirSim frames, 100 target AirSim frames, and 116 UAVVaste test images showed that **`ultralytics` (`8.3.250` vs `8.4.211`)** is 100% responsible for the confidence/recall shift. Varying `opencv-python` (`4.11.0.86` vs `5.0.0.93`) and `numpy` (`2.2.2` vs `2.5.4`) while holding `ultralytics==8.3.250` constant produced identical results to 4 decimal places.

### Checkpoint Metadata (`models/garbage.pt`)

- **Training Ultralytics version**: `8.4.211`
- **Training date**: `2026-11-13T02:44:02.280542+05:30`
- **Training args**: `model=yolo26n.pt`, `imgsz=1280`, `epochs=100`, `batch=2`, `device=0`

## Stack Comparison

| Environment | Pinned Requirements | Ultralytics / Runtime | AirSim Empty Shadow (100f) | AirSim Target (100f) | UAVVaste (`conf=0.55`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Sim GPU (`.venv-gpu`)** | `requirements-sim-gpu.pinned.txt` | `ultralytics==8.4.211` | `mean=0.5684`, `max=0.6637`, `>=0.55`: 86 | `mean=0.1544`, `max=0.4834`, `>=0.35`: 7 | P `0.9583`, R `0.4990` (TP 253, FP 11, FN 254) |
| **Sim CPU (system Python)** | `requirements-sim-cpu.pinned.txt` | `ultralytics==8.3.250` | `mean=0.1582`, `max=0.3112`, `>=0.55`: 0 | `mean=0.3218`, `max=0.5616`, `>=0.35`: 65 | P `0.9528`, R `0.4379` (TP 222, FP 11, FN 285) |
| **ONNX-only (Pi 5 target)** | `requirements-pi5-onnx.pinned.txt` | `onnxruntime==1.23.2` | `mean=0.2247`, `max=0.4410`, `>=0.55`: 0 | `mean=0.2933`, `max=0.5318`, `>=0.35`: 56 | P `0.9703`, R `0.4517` (TP 229, FP 7, FN 278) |

- **Original benchmark (`ml/reports/real_split_benchmark.json`)**: `PyTorch CPU` on `garbage.pt` at `conf=0.55` reported `precision: 0.9628`, `recall: 0.4596` (Ultralytics val evaluator at `imgsz=640`).
- **`.venv-gpu` (`ultralytics==8.4.211`)** matches the training checkpoint version.
- **ONNX-only (`models/garbage.onnx`)** uses `DroneOS`'s own letterbox and filter code without `ultralytics` or `torch`, making it version-independent of `ultralytics` and well suited for Raspberry Pi 5 (ARM64 Pi 5 hardware execution remains unverified).

## Verifying a Stack with Golden Frames

Run the standalone golden-frame check before flying or after updating packages:

```bash
python scripts/verify_detector_stack.py
```

`run_perception_preflight()` in `DroneOS/perception/preflight.py` also runs this check automatically when `assets/perception/golden/manifest.json` and real weights are present, logging a `WARNING` and returning `("DEGRADED", reason)` on drift without blocking flight.
