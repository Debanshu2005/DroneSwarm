# Architecture: Machine Learning Model & Training Pipeline

## 1. Executive Summary

PhoneOS Swarm utilizes an aerial object detection model specifically trained to identify municipal litter, dumped waste, and debris from unmanned aerial vehicles (UAVs).

The production model is installed at `models/garbage.pt` and tracked in `ml/model_metadata.json`. It operates as the front-end visual perception engine for `DroneOS`, feeding 2D bounding boxes and confidence scores into the 3D ray geolocation and peer-exclusion pipeline.

---

## 2. Model Architecture & Technical Specifications

| Parameter | Specification | Notes |
|---|---|---|
| **Model Name** | `DroneSwarm-YOLO26n-UAVVaste` | Production aerial garbage detector |
| **Installed Weights** | `models/garbage.pt` | Project root checkpoint |
| **Source Checkpoint** | `runs/detect/ml/runs/uavvaste-2/weights/best.pt` | Best validation mAP checkpoint |
| **Architecture Family** | YOLOv8n / YOLO26n (Ultralytics) | Lightweight anchor-free convolutional architecture |
| **Task** | Object Detection (`detect`) | 2D bounding boxes $(x_1, y_1, x_2, y_2, \text{conf}, \text{class})$ |
| **Parameter Count** | **2,389,735** (~2.39M parameters) | Optimized for companion computer edge execution |
| **Layer Count** | **121 layers** | Conv, C2f, SPPF, and decoupled detection heads |
| **Computational Cost** | **5.4 GFLOPs** (at $640 \times 640$) | Real-time capable on embedded GPUs and ARM64 CPUs |
| **Target Classes** | `0: rubbish` | Unified class encompassing cans, bottles, bags, and bulk waste |

---

## 3. Dataset Curation, Licensing & Split Strategy

The model was trained on the **UAVVaste** aerial dataset, capturing realistic municipal litter across varied outdoor environments (beaches, parks, roadways, scrubland, and industrial zones).

- **Code & Annotation License:** Apache-2.0
- **Total Images:** 772 high-resolution aerial photographs
- **Total Annotations:** 3,718 bounding-box instances of class `rubbish`
- **Flickr Ground Truth Resolution:** Ranging from $1920 \times 1080$ to $4000 \times 3000$ pixels

### Official Split Distribution
Dataset preparation enforces the official distribution (`ml/uavvaste/prepare_dataset.py`):

| Split | Image Count | Instance Count | Purpose |
|---|:---:|:---:|---|
| **Train** | 540 (70%) | 2,595 | Model parameter learning with data augmentation |
| **Validation** | 116 (15%) | 616 | Hyperparameter tuning, checkpoint selection, and threshold sweep |
| **Test** | 116 (15%) | 507 | Independent benchmark evaluation (untouched during training) |

---

## 4. Training Pipeline & Hyperparameters

Training is governed by `ml/uavvaste/train.py` with multi-scale data augmentations:

```bash
# Standard training invocation (100 epochs, high-resolution 1280px):
python ml/uavvaste/train.py --data ml/data/uavvaste_yolo/dataset.yaml --imgsz 1280 --epochs 100 --batch 8 --install
```

### Key Training Hyperparameters:
- **Input Resolution (`imgsz_train`):** $1280 \times 1280$  
  *(High resolution is mandatory to prevent small litter items—such as beverage cans occupying $< 15\text{ px}$—from being lost in stride-32 feature maps).*
- **Epochs:** 100 epochs with early stopping patience of 20 epochs.
- **Batch Size:** 8 (dynamically scalable to 2 for 4GB VRAM GPUs like GTX 1650).
- **Optimizer:** `AdamW` with automatic learning rate scheduling and cosine decay.
- **Augmentation Pipeline:**
  - Mosaic augmentation (prob = 1.0, turned off for final 10 epochs)
  - Random affine transformations (degrees = $\pm 10^\circ$, translate = 0.1, scale = 0.5)
  - Color space jitter (HSV-H = 0.015, HSV-S = 0.7, HSV-V = 0.4)
  - Random horizontal and vertical flips

---

## 5. Empirical Benchmark Results

### 5.1 Official Test Split Benchmark (116 Images, 507 Instances)
Evaluated at test resolution $1280 \times 1280$ using `ml/uavvaste/evaluate_official_test.py`:

| Metric | Measured Value | Operational Interpretation |
|---|:---:|---|
| **Precision** | **91.84%** | Out of 100 rubbish predictions, ~92 are genuine litter |
| **Recall** | **78.50%** | Detects ~79% of all visually present ground litter |
| **mAP@50** | **83.62%** | High overlap accuracy at standard IoU 0.50 threshold |
| **mAP@50–95** | **51.30%** | Comprehensive bounding-box localization precision across IoUs |
| **Inference Latency** | **21.4 ms** / image | Tested on NVIDIA GeForce GTX 1650 (CUDA) |
| - *Pre-process* | 1.5 ms | Letterbox resizing and FP32 normalization |
| - *Inference* | 18.2 ms | Forward tensor pass through 121 layers |
| - *NMS Post-process*| 1.7 ms | Non-Maximum Suppression (IoU = 0.50) |

---

## 6. Confidence Threshold Optimization for Swarm Operations

The confidence threshold trade-off was evaluated systematically across the validation split (116 images, 616 instances) using `ml/uavvaste/evaluate_thresholds.py` (measured at high-resolution `imgsz = 1280`):

| Confidence | Precision | Recall | F1 Score | Est. TP | Est. FP | Est. FN | FP Reduction | Recommended Operational Domain |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| 0.25 | 88.37% | 76.95% | 0.8227 | 474 | 62 | 142 | Base | Search & Rescue (Max Recall) |
| 0.35 | 88.07% | 77.89% | **0.8267** | 480 | 65 | 136 | Reference | Default Exploration Mode |
| 0.50 | 92.28% | 73.70% | 0.8195 | 454 | 38 | 162 | -41.5% | Balanced Precision Mode |
| **0.55** | **93.99%** | **71.10%** | **0.8096** | **438** | **28** | **178** | **-56.9%** | **Swarm Formation Mode (Production Baseline)** |
| 0.65 | 95.96% | 61.69% | 0.7510 | 380 | 16 | 236 | -75.4% | Ultra-Clean Inspection |

> [!TIP]
> **Why `0.55` is the Production Swarm Standard:**  
> At `conf = 0.55`, the model achieves **93.99% precision**, reducing false positive alarms by **56.9%** while maintaining over 71% single-frame recall. When paired with multi-drone track aggregation and temporal voting (requiring $\ge 2$ sightings), false-alarm rates drop to near zero.

---

## 7. Edge Optimization & Hardware Deployment

### 7.1 Multi-Resolution Runtime Support
The `GarbageDetector` class supports dynamic image sizing:
- **`imgsz = 640` (Default Edge Mode):** Used on Raspberry Pi 5 companion computers. Yields 10–15 FPS on CPU / NCNN.
- **`imgsz = 1280` (High-Detail Mode):** Used on GPU-equipped workstations or high-altitude flights ($> 15\text{ m}$ AGL) to resolve small litter targets.

### 7.2 ONNX & Embedded Acceleration
For ARM64 Linux deployment without a full PyTorch CUDA environment, the pipeline supports ONNX export:
```bash
# Export trained PyTorch weights to ONNX (default opset 17, static imgsz 640)
python ml/uavvaste/export_onnx.py --weights models/garbage.pt --imgsz 640 --opset 17
```
The resulting ONNX graph runs via `onnxruntime` or NCNN, leveraging ARM NEON SIMD instructions on the Raspberry Pi's Quad-core Cortex-A76 processor.

### 7.3 Fail-Safe Inference Engine
The detector implementation in `DroneOS/perception/detector.py`:
1. **Isolated Execution:** Runs inside a background worker thread (`run_in_executor`) to prevent blocking the 10 Hz flight pipeline.
2. **Automatic CPU Fallback:** If CUDA initialization encounters driver issues or memory allocation faults, it silently falls back to CPU inference without terminating DroneOS.
3. **Synthetic Frame Tagging:** When processing synthetic frames (e.g. from `ImageFolderSource`), detections are tagged with `synthetic: true` to inform the mobile GCS.

---

## 8. Verification & Test Suite

The machine learning subsystem is protected by dedicated unit and regression tests in `ml/tests/`:

- `test_ml_smoke.py`: Validates model loading, inference shapes, and outputs.
- `test_evaluate_thresholds.py`: Verifies metric computation and threshold sweeps.
- `test_prepare_dataset.py`: Tests annotation formatting, split distribution, and bounds checking.
- `test_onnx_export.py`: Validates ONNX export integrity and output tensor parity with PyTorch.
- `test_temporal_confirmation.py`: Confirms multi-frame hit accumulation and greedy track merging.
