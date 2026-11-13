# UAVVaste Garbage Detection ML Pipeline

This directory contains the dataset preparation and model training pipeline for aerial litter/garbage detection using the UAVVaste dataset and Ultralytics YOLO.

---

## 1. Prerequisites and Licensing

- **UAVVaste Dataset**: The dataset code and annotations are licensed under **Apache-2.0**. Image files are hosted on Flickr and governed by individual image creator Flickr licenses (see the `licenses` section in `annotations.json`).
- **Ultralytics**: Ultralytics YOLO is licensed under **AGPL-3.0**. Please consult the Ultralytics license terms when deploying models trained with it.

---

## 2. Step-by-Step Workflow

### Step 1: Clone UAVVaste Annotations
```bash
git clone https://github.com/UAVVaste/UAVVaste ml/data/UAVVaste
```
The repository includes `annotations/annotations.json` containing 772 image metadata entries and 3,718 bounding-box annotations for class `rubbish`.

### Step 2: Prepare Dataset
Run the preparation script to convert annotations to YOLO format, apply the official 540/116/116 train/val/test split, validate images and bounding boxes, and organize the dataset:

```bash
# Using the extracted official dataset (default paths):
python ml/uavvaste/prepare_dataset.py

# With custom paths if needed:
python ml/uavvaste/prepare_dataset.py `
  --images-dir ml/data/uavvaste_official_extracted/images `
  --annotations ml/data/uavvaste_official_extracted/annotations/annotations.json `
  --split-file ml/data/uavvaste_official_extracted/annotations/train_val_test_distribution_file.json `
  --out ml/data/uavvaste_yolo `
  --workers 8
```

Output directory layout:
```text
ml/data/uavvaste_yolo/
├── dataset.yaml       # Ultralytics dataset definition
├── train.txt          # Filepaths to training images
├── val.txt            # Filepaths to validation images
├── test.txt           # Filepaths to test images
├── images/            # Downloaded .jpg images
└── labels/            # Normalized YOLO .txt annotations
```

### Step 3: Train / Fine-Tune Model

Fine-tune pretrained weights on the prepared dataset:
```bash
# Standard training (fine-tuning from pretrained weights, default --workers 0 for Windows memory safety):
python ml/uavvaste/train.py --data ml/data/uavvaste_yolo/dataset.yaml --imgsz 1280 --epochs 100 --batch -1 --workers 0

# For systems with constrained VRAM / RAM (e.g. GTX 1650 4GB / 8GB RAM):
python ml/uavvaste/train.py --batch 2 --workers 0 --imgsz 1280

# Train and install weights directly to models/garbage.pt:
python ml/uavvaste/train.py --install --batch 2 --workers 0

# Optional export to ONNX or NCNN:
python ml/uavvaste/train.py --export onnx
```

If `yolo26n.pt` is not available in the installed Ultralytics version, the script automatically falls back to `yolo11n.pt` with a clear notice.

### Step 4: Training in Google Colab (Recommended for GPU Access)
1. Zip `ml/` or clone your repo in Google Colab.
2. Select a GPU runtime (T4 or A100).
3. Execute:
   ```bash
   pip install ultralytics
   git clone https://github.com/UAVVaste/UAVVaste ml/data/UAVVaste
   python ml/uavvaste/prepare_dataset.py --workers 16
   python ml/uavvaste/train.py --imgsz 1280 --epochs 100 --batch 16 --install
   ```
4. Download the resulting `models/garbage.pt` and place it in the project root `models/garbage.pt`.

---

## 3. Honest Expectations & Engineering Reality

- **Hardware & Network Connectivity**: Training an object detection model with `imgsz=1280` on 772 high-resolution aerial images requires a GPU machine (CUDA) or Google Colab. Downloading Flickr images requires unrestricted internet access; sandboxed or proxy environments may block Flickr static CDNs.
- **Sim-to-Real Domain Gap**: UAVVaste consists of real-world aerial imagery of beaches, parks, roads, and scrubland. The default Unreal Engine scenery in AirSim contains pristine textures with no litter. As a result, the detector running in standard AirSim simulation will not detect garbage unless custom litter 3D meshes and textures are imported into the UE environment. For live demonstration and verification of the end-to-end swarm perception and mapping pipeline, use the `ImageFolderSource` replay mode (detections from replay are explicitly flagged `synthetic=True`).
- **Geolocation Precision**: Geolocation relies on a flat-ground assumption using the drone's relative altitude as height above ground. Real-world GPS drift, heading sensor noise, gimbal/camera mount tolerance, and network transmission latency introduce several meters of positional variance. The track aggregator uses a `merge_radius_m` of 2.5 m and confidence-weighted position averaging to fuse multi-frame observations into stable ground markers.
- **Empirical Metrics**: Real mAP, precision, and recall metrics should only be reported from actual evaluation runs on the test split after GPU training completes. Pre-trained base models without fine-tuning on UAVVaste will not identify aerial litter.

---

## 4. Installed Model & Evaluation Utilities

The production model weights are installed at:
- `models/garbage.pt` (YOLO26n, 2.39M parameters, 5.4 GFLOPs)
- Checkpoint source: `runs/detect/ml/runs/uavvaste-2/weights/best.pt`
- Metadata: `ml/model_metadata.json`

### Official Test Split Results (116 Images, 507 Instances):
- **Precision**: 91.84%
- **Recall**: 78.50%
- **mAP@50**: 83.62%
- **mAP@50-95**: 51.30%

### Running Threshold Evaluation:
```bash
python ml/uavvaste/evaluate_thresholds.py --split val --device cuda:0
```

### Running Replay Smoke Test on UAV Images:
```bash
python scripts/test_replay_detector.py --num-images 5 --device cuda:0
```


