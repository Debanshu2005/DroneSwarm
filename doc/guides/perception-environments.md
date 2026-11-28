# Perception Environments & Reproducibility

This guide documents the perception runtime stacks tested on branch `garbage-detection`, why raw detector confidences differ across environments, and how to verify a deployment stack before flight.

## Reference Environment

The **reference environment** is the stack matching the `models/garbage.pt` training checkpoint (`ultralytics==8.4.211`).

- **Checkpoint Metadata (`models/garbage.pt`)**:
  - Training Ultralytics version: `8.4.211`
  - Training date: `2026-11-13T02:44:02.280542+05:30`
  - Training args: `model=yolo26n.pt`, `imgsz=1280`, `epochs=100`, `batch=2`, `device=0`
- **ONNX Metadata (`models/garbage.onnx`)**:
  - Exported with `author=Ultralytics`, `version=8.3.250`, `date=2026-11-13T17:53:38.654269`, `producer=pytorch`.

## Root Cause of Cross-Environment Confidence Differences

Single-package ablation in isolated virtual environments on the same 100 empty-scene AirSim frames, 100 target AirSim frames, and 116 UAVVaste test images showed that **`ultralytics` (`8.3.250` vs `8.4.211`)** is 100% responsible for the confidence/recall shift. Varying `opencv-python` (`4.11.0.86` vs `5.0.0.93`) and `numpy` (`2.2.2` vs `2.5.4`) while holding `ultralytics==8.3.250` constant produced identical results to 4 decimal places.

## Stack Comparison

| Environment | Pinned Requirements | Ultralytics / Runtime | AirSim Empty Shadow (100f) | AirSim Target (100f) | UAVVaste (`conf=0.55`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Sim GPU (`.venv-gpu`, reference)** | `requirements-sim-gpu.pinned.txt` | `ultralytics==8.4.211` | `mean=0.5684`, `max=0.6637`, `>=0.55`: 86 | `mean=0.1544`, `max=0.4834`, `>=0.35`: 7 | P `0.9583`, R `0.4990` (TP 253, FP 11, FN 254) |
| **Sim CPU (system Python, upgraded)** | `requirements-sim-cpu.pinned.txt` | `ultralytics==8.4.211` | — | — | P `0.9583`, R `0.4990` (TP 253, FP 11, FN 254); at `0.35`: P `0.8957`, R `0.6095` |
| **Sim CPU (historical `8.3.250`)** | — | `ultralytics==8.3.250` | `mean=0.1582`, `max=0.3112`, `>=0.55`: 0 | `mean=0.3218`, `max=0.5616`, `>=0.35`: 65 | P `0.9528`, R `0.4379` (TP 222, FP 11, FN 285) |
| **ONNX-only (Pi 5 target)** | `requirements-pi5-onnx.pinned.txt` | `onnxruntime==1.23.2` | `mean=0.2247`, `max=0.4410`, `>=0.55`: 0 | `mean=0.2933`, `max=0.5318`, `>=0.35`: 56 | P `0.9703`, R `0.4517` (TP 229, FP 7, FN 278) |

### Upgrading System Python to Match the Reference Stack

System Python was upgraded from `ultralytics==8.3.250` to `8.4.211` with:

```bash
python -m pip install --no-deps ultralytics==8.4.211
```

Re-running the UAVVaste real-split evaluation on system Python (`cpu_fp32`) after upgrading reproduced `.venv-gpu` exactly (`conf=0.55`: P `0.9583`, R `0.4990`, TP `253`, FP `11`, FN `254`; `conf=0.35`: P `0.8957`, R `0.6095`, TP `309`, FP `36`, FN `198`).

## Earlier Reports Measured Under `ultralytics==8.3.250`

The following reports in `ml/reports/` were generated using system Python while `ultralytics==8.3.250` was installed (prior to the upgrade to `8.4.211`):

- `ml/reports/step1_baseline_profiling.json` (Step-1 sim profiling) — **stack mismatch, re-run before relying on them**
- `ml/reports/step2_addendum_detectability_and_timing.json` (Step-2 addendum detectability & timing) — **stack mismatch, re-run before relying on them**
- `ml/reports/time_to_confirm_results.json` (0.25 time-to-confirm benchmark) — **stack mismatch, re-run before relying on them**
- `ml/reports/time_to_confirm_10_flights.json` (10-flight time-to-confirm benchmark) — **stack mismatch, re-run before relying on them**
- `ml/reports/vram_and_startup_benchmark.json` (VRAM & startup stagger benchmark) — **stack mismatch, re-run before relying on them**
- `ml/reports/real_split_benchmark.json` (`PyTorch CPU` section; `ONNX` section used `models/garbage.onnx`) — **stack mismatch, re-run before relying on them**
- `ml/reports/live_airsim_single_drone_validation.json` (single-drone AirSim validation) — **stack mismatch, re-run before relying on them**
- `ml/reports/live_airsim_execution.json` & `ml/reports/live_airsim_probe.json` (live AirSim execution & probe) — **stack mismatch, re-run before relying on them**
- `ml/reports/test_conf_sweep_640.json`, `ml/reports/temporal_confirmation_results.json`, `ml/reports/onnx_validation_report.json` (generated while `8.3.250` was active on system Python) — **stack mismatch, re-run before relying on them**

## Runtime Preflight & Golden-Frame Verification

At perception startup, `run_perception_preflight()` in `DroneOS/perception/preflight.py` logs installed versions (`ultralytics`, `onnxruntime`, `opencv`, `numpy`, `torch`) and checkpoint metadata (`.pt` or `.onnx`). If `ultralytics` is installed and its version differs from the `.pt` checkpoint's training version, it logs a `WARNING` and reports `perception_status` as `DEGRADED` with that reason without blocking flight.

To run the standalone golden-frame check manually:

```bash
python scripts/verify_detector_stack.py
```

## PT vs ONNX Gap

- **Parity image (`test_onnx_vs_pt_parity`)**: PT (`ultralytics==8.4.211`) scores `0.836` (`0.8358`) vs shipped ONNX (`8.3.250` export) `0.668` (`0.6679`), with a `2.54 px` box delta and identical box count/class.
- **UAVVaste real split (`conf=0.55`)**: PT (`8.4.211`) achieves `P 0.958 / R 0.499` vs shipped ONNX (`8.3.250` export) `P 0.970 / R 0.452` — expect roughly `0.05` lower recall on ONNX than PT at `conf=0.55`.
- **`8.4.211` ONNX re-export tested and rejected**: raw scores on the same frames dropped sharply (`0.836 -> 0.106` on the parity image; `uavvaste_1` third box `0.367 -> 0.214`), and sim empty-scene shadow hits jumped to `75/100 >= 0.55` (vs `0/100` on the `8.3.250` export). Our `(1, 300, 6)` decode path in `DroneOS/perception/detector.py` was verified and found correct.
- **Shipped Pi model**: remains the `8.3.250` ONNX export (`models/garbage.onnx`).
