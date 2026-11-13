# Aerial Garbage Detection & Swarm Mapping Guide

This guide details the end-to-end aerial garbage detection, ground geolocation, and swarm mapping subsystem in PhoneOS Swarm.

---

## 1. Subsystem Architecture

```mermaid
flowchart TD
    subgraph DroneNode["Drone Companion Computer (DroneOS)"]
        FS["FrameSource (AirSim / OpenCV / Folder)"] --> Pipe["PerceptionPipeline"]
        FC["Flight Controller (PX4 / AirSim)"] -->|Telemetry Snapshot| Pipe
        Pipe -->|Inference (imgsz=640/1280)| Det["GarbageDetector (YOLO26n)"]
        Det --> Pipe
        Pipe -->|Pinhole & WGS84 Projection| Geo["Geolocation Engine"]
        Geo --> Pipe
        Pipe -->|Greedy Matching (radius=2.5m)| Agg["TrackAggregator"]
        Agg -->|New / Update Markers (<=10 chunk)| Net["UdpNetworkAdapter"]
    end

    subgraph Bridge["Relay Layer (relay.py)"]
        Relay["UDP-to-WebSocket Bridge"]
    end

    subgraph MobileGCS["Ground Control Station (React + Leaflet)"]
        WS["MultiWebSocketManager"] --> Ctx["DroneContext (PhoneOS_Garbage)"]
        Ctx --> Map["MapView (GarbageLayer)"]
    end

    Net -->|UDP Port 14550| Relay
    Relay -->|WebSocket Port 8081| WS
```

---

## 2. Trained Model Specifications

The trained aerial garbage detection model is installed at `models/garbage.pt` and documented in `ml/model_metadata.json`:

- **Architecture**: YOLO26n (Ultralytics)
- **Parameters**: 2,389,735
- **Layers**: 121
- **GFLOPs**: 5.4
- **Class Label**: `0: rubbish`
- **Training Dataset**: UAVVaste (772 images, 3,718 bounding-box annotations)
- **Official Splits**: 540 Train / 116 Val / 116 Test
- **Training Epochs**: 100
- **Training Checkpoint**: `runs/detect/ml/runs/uavvaste-2/weights/best.pt`

### 2.1 Official Test Split Benchmark (116 Images, 507 Instances)
Evaluated on the untouched official test split at resolution $1280 \times 1280$:

| Metric | Measured Score |
|---|---:|
| **Precision** | **91.84%** |
| **Recall** | **78.50%** |
| **mAP@50** | **83.62%** |
| **mAP@50–95** | **51.30%** |
| **Inference Latency** | 21.4 ms / image (GTX 1650) |

---

## 3. Empirical Confidence Threshold Trade-Offs

The confidence threshold trade-off was evaluated systematically on the **validation split** (116 images, 616 instances) using `ml/uavvaste/evaluate_thresholds.py`:

| Confidence | Precision | Recall | F1 Score | Est. TP | Est. FP | Est. FN | FP Reduction |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 0.25 | 88.37% | 76.95% | 0.8227 | 474 | 62 | 142 | Base |
| 0.30 | 88.37% | 76.95% | 0.8227 | 474 | 62 | 142 | 0.0% |
| **0.35 (Default)** | **88.07%** | **77.89%** | **0.8267** | **480** | **65** | **136** | **Reference** |
| 0.40 | 88.56% | 76.62% | 0.8216 | 472 | 61 | 144 | -6.2% |
| 0.45 | 90.25% | 75.16% | 0.8202 | 463 | 50 | 153 | -23.1% |
| **0.50 (Recommended)** | **92.28%** | **73.70%** | **0.8195** | **454** | **38** | **162** | **-41.5%** |
| **0.55 (High Prec)** | **93.99%** | **71.10%** | **0.8096** | **438** | **28** | **178** | **-56.9%** |
| 0.60 | 94.70% | 66.72% | 0.7829 | 411 | 23 | 205 | -64.6% |
| 0.65 | 95.96% | 61.69% | 0.7510 | 380 | 16 | 236 | -75.4% |
| 0.70 | 97.19% | 50.49% | 0.6645 | 311 | 9 | 305 | -86.2% |

> [!NOTE]
> Increasing confidence from `0.35` to `0.50` reduces false positives by **41.5%** while retaining 94.6% of all true rubbish detections (73.70% recall vs 77.89%). For missions requiring even lower false-alarm rates, `0.55` achieves **93.99% precision** with 71.10% recall.

---

## 4. On-Drone Perception (`DroneOS/perception/`)

### 4.1 Safety Gating & Isolated Execution
Perception runs as an isolated `asyncio` task and never blocks or interferes with flight management:
- **Armed State**: Inference is gated until the drone is `ARMED`.
- **GPS Fix**: Telemetry `gps_valid` must be `True`.
- **Minimum Altitude**: Telemetry `altitude >= 2.0 m`.
- **Tilt Limits**: Drone tilt (`|roll| <= 20°` and `|pitch| <= 20°`) to prevent distorted oblique ground projections.
- **Fail-Safe Operation**: All exceptions in the inference loop are caught with automatic backoff; if CUDA initialization fails, the detector automatically falls back to CPU without crashing DroneOS.

### 4.2 Telemetry Snapshot & Geolocation
Immediately after frame capture (before model inference), a flight telemetry snapshot is recorded. The bounding-box center is transformed into geographic WGS84 coordinates:
1. **Pinhole Camera Ray**: $r_{cam} = [(u - c_x)/f_x, (v - c_y)/f_y, 1]^T$ where $f = (W / 2) / \tan(\text{hfov} / 2)$.
2. **Mount Pitch**: Pitch down by mount pitch angle via $R_y(-\text{mount\_pitch})$ ($90^\circ$ = nadir).
3. **Attitude Rotation**: Rotate from body to NED using $R_z(\text{yaw}) R_y(\text{pitch}) R_x(\text{roll})$.
4. **Ground Plane Intersection**: $t = h_{\text{AGL}} / d_{\text{down}}$; $\text{North} = t \cdot d_n, \text{East} = t \cdot d_e$.
5. **WGS84 Conversion**: Metres-per-degree series converts NED offsets to latitude/longitude.

### 4.3 Temporal Track Aggregation
To prevent one static piece of garbage from generating multiple markers across successive frames:
- **Association Radius**: Greedy matching within `merge_radius_m = 2.5 m`.
- **Position Smoothing**: Running confidence-weighted mean position across detections.
- **Confirmation Threshold**: Requires $\ge 2$ observations (`min_hits = 2`) before emitting a `"new"` marker.
- **Repeated Frame Guard**: Validates that elapsed time has progressed (`now > track.last_seen`) before incrementing hit counts. Stalled video feeds or zero-delta calls do not increment hits.
- **Throttled Updates**: Position updates are emitted at most every 10 seconds (`update_interval_s = 10.0`).
- **Pruning**: Unconfirmed tracks older than 30 seconds are pruned; total tracks are bounded to 2,000.

---

## 5. Configuration & Usage Guide

### 5.1 Configuration (`DroneOS/configs/perception.yaml`)
```yaml
enabled: false          # Keep disabled by default; set true to activate
source: "airsim"        # "airsim" | "opencv" | "folder"
weights: "models/garbage.pt"
imgsz: 640              # 640 for edge/RPi5 efficiency, 1280 for max detail
confidence: 0.35        # Starting confidence threshold (use 0.50 for high precision)
iou: 0.5                # NMS IoU threshold
device: null            # null for auto-detect (cuda:0 if available, else cpu)
classes: null           # null for all classes, or ["rubbish"]
max_fps: 1.0            # Max perception loops per second
min_altitude_m: 2.0     # Minimum altitude required for geolocation
max_tilt_deg: 20.0      # Max roll/pitch tilt allowed for nadir projection
max_range_m: 120.0      # Maximum valid ground projection distance
ground_elevation_m: 0.0 # Elevation of target ground plane in meters
merge_radius_m: 2.5     # Ground distance radius for temporal association
min_hits: 2             # Hits required to confirm a track (suppresses single-frame noise)
update_interval_s: 10.0 # Throttle interval between track update broadcasts
max_tracks: 2000        # Maximum active tracks kept in memory
camera:
  hfov_deg: 90.0
  mount_pitch_deg: 90.0 # 90.0 = nadir downward-facing camera
  airsim_camera: "bottom_center"
  device: 0
  image_dir: null
```

### 5.2 Exact Commands to Run Offline Replay Smoke Test
Test the detector and visual annotation pipeline directly on real UAV images:
```bash
# 1. Run on GPU with 5 sample images and generate annotated bounding box crops:
python scripts/test_replay_detector.py --num-images 5 --device cuda:0

# 2. Run on CPU:
python scripts/test_replay_detector.py --num-images 5 --device cpu

# 3. Run on a custom folder of drone images at 0.50 confidence:
python scripts/test_replay_detector.py --images-dir path/to/images --conf 0.50 --imgsz 640
```
Annotated images will be saved to `ml/reports/smoke_test_output/`.

### 5.3 Exact Commands to Enable AirSim Live Perception
1. Start AirSim and connect your flight controller (PX4 or SimpleFlight).
2. Edit `DroneOS/configs/perception.yaml`:
   ```yaml
   enabled: true
   source: "airsim"
   weights: "models/garbage.pt"
   ```
3. Sync configuration across drone instances:
   ```bash
   python scripts/sync_perception.py
   ```
4. Start DroneOS:
   ```bash
   python -m DroneOS.main
   ```

### 5.4 Exact Commands to Re-run Threshold Evaluation
```bash
python ml/uavvaste/evaluate_thresholds.py --split val --device cuda:0 --imgsz 1280
```
Outputs:
- `ml/runs/threshold_evaluation.csv`
- `ml/runs/threshold_evaluation.json`
- `ml/runs/threshold_tradeoffs.png`
- `ml/runs/pr_curve.png`

---

## 6. Mobile Ground Control Station Integration

### 6.1 Cross-Drone Marker Fusion (`mobile/src/utils/garbage.js`)
When multiple drones in the swarm observe the same ground target:
- Sighting with the same track ID updates hits and confidence.
- Sighting with a different track ID within 2.0 m is treated as the same physical object seen by another swarm member: hits are accumulated, the reporting drone is appended to `seenBy`, and ground coordinates are maintained.
- Markers are bounded to 500 entries (oldest evicted) and persisted to `localStorage` under `PhoneOS_Garbage`.

### 6.2 Interactive Map Display (`GarbageLayer.jsx`)
- **Icon**: Distinctive trash SVG marker.
- **Confidence Tinting**:
  - $\ge 70\%$: Red (`#ef4444`)
  - $50\% - 70\%$: Orange (`#f97316`)
  - $< 50\%$: Amber (`#f59e0b`)
- **Replay / Synthetic Indicator**: Detections from `ImageFolderSource` display a dashed ring and `[SYNTHETIC]` badge.
- **Popup Details**: Displays confidence, times seen, reporting drone, observing drones, first/last seen timestamps, and coordinates.
- **False-Positive Dismissal**: The "Dismiss (false positive)" button removes the marker and prevents it from reappearing during the session.

---

## 7. Known Limitations & Edge Deployment Notes

1. **AirSim Scenery Gap**: Default AirSim environments (Blocks, CityEnviron) contain clean, sterile terrain textures with zero litter. Use `ImageFolderSource` or custom 3D litter meshes in Unreal Engine for realistic testing.
2. **Flat-Ground Assumption**: Ray-ground intersection assumes constant ground elevation ($Z = \text{ground\_elevation\_m}$). In hilly or sloped terrain, positional error increases with distance from nadir.
3. **GPS Accuracy**: Detections are georeferenced relative to the drone's reported GPS coordinates. Commercial civilian GNSS receivers typically exhibit $1.5 - 3.0$ m horizontal variance.
4. **Raspberry Pi 5 Deployment**: On ARM64 Linux (Raspberry Pi 5), run PyTorch CPU or export the model to ONNX using `yolo export model=models/garbage.pt format=onnx imgsz=640` for execution via ONNX Runtime or NCNN.
