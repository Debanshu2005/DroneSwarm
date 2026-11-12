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

### Step 2: Prepare Dataset and Download Images
Run the preparation script to convert annotations to YOLO format, group frames by flight, partition into train/val/test splits without leakage, and download the full-resolution Flickr images:

```bash
# Full preparation with multithreaded download (8 workers):
python ml/uavvaste/prepare_dataset.py --workers 8 --split 0.70 0.15 0.15 --seed 0

# Or generate labels and split files only (skip image downloading):
python ml/uavvaste/prepare_dataset.py --skip-download
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
# Standard training (fine-tuning from pretrained weights):
python ml/uavvaste/train.py --data ml/data/uavvaste_yolo/dataset.yaml --imgsz 1280 --epochs 100 --batch -1

# Train and install weights directly to models/garbage.pt:
python ml/uavvaste/train.py --install

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
