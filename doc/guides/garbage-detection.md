# Aerial Garbage Detection & Swarm Mapping Guide

This guide details the end-to-end aerial garbage detection, ground geolocation, and swarm mapping subsystem in PhoneOS Swarm.

---

## 1. Subsystem Architecture

```mermaid
flowchart TD
    subgraph DroneNode["Drone Companion Computer (DroneOS)"]
        FS["FrameSource (AirSim / OpenCV / Folder)"] --> Pipe["PerceptionPipeline"]
        FC["Flight Controller (PX4 / AirSim)"] -->|Telemetry Snapshot| Pipe
        Pipe -->|Inference (imgsz=1280)| Det["GarbageDetector (YOLO)"]
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

## 2. Machine Learning Training Pipeline (`ml/`)

### 2.1 UAVVaste Dataset Preparation
The UAVVaste dataset contains 772 high-resolution aerial images and 3,718 bounding-box annotations for class `rubbish`. Because consecutive video frames in a flight group are near-duplicates, images are split by flight group using greedy partitioning to prevent data leakage.

```bash
# Clone the UAVVaste annotations:
git clone https://github.com/UAVVaste/UAVVaste ml/data/UAVVaste

# Convert annotations to YOLO format, split by flight group, and download images:
python ml/uavvaste/prepare_dataset.py --workers 8 --split 0.70 0.15 0.15 --seed 0

# Or skip image downloads to generate labels and split definitions only:
python ml/uavvaste/prepare_dataset.py --skip-download
```

### 2.2 Model Fine-Tuning
Litter in high-resolution aerial imagery is extremely small (median box is ~2.0% of image width and ~3.5% of height). Training is executed at `imgsz=1280` with augmentations adapted for top-down flight (vertical and horizontal flips, 10° random rotations):

```bash
python ml/uavvaste/train.py --data ml/data/uavvaste_yolo/dataset.yaml --imgsz 1280 --epochs 100 --batch -1 --install
```

Pretrained weights are automatically fine-tuned (falling back to `yolo11n.pt` if `yolo26` is not present in Ultralytics). Upon completion, `--install` copies the best weights to `models/garbage.pt`.

---

## 3. On-Drone Perception (`DroneOS/perception/`)

### 3.1 Safety Gating & Isolated Execution
Perception runs as an isolated `asyncio` task and never blocks or interferes with flight management:
- **Armed State**: Inference is gated until the drone is `ARMED`.
- **GPS Fix**: Telemetry `gps_valid` must be `True`.
- **Minimum Altitude**: Telemetry `altitude >= 2.0 m`.
- **Tilt Limits**: Drone tilt (`|roll| <= 20°` and `|pitch| <= 20°`) to prevent distorted oblique ground projections.
- **Fail-Safe Operation**: All exceptions in the inference loop are caught with automatic backoff; if dependencies are missing, the pipeline disables itself without impacting flight control.

### 3.2 Telemetry Snapshot & Geolocation
Immediately after frame capture (before model inference), a flight telemetry snapshot is recorded. The bounding-box center is transformed into geographic WGS84 coordinates:
1. **Pinhole Camera Ray**: $r_{cam} = [(u - c_x)/f_x, (v - c_y)/f_y, 1]^T$ where $f = (W / 2) / \tan(\text{hfov} / 2)$.
2. **Mount Pitch**: Pitch down by mount pitch angle via $R_y(-\text{mount\_pitch})$.
3. **Attitude Rotation**: Rotate from body to NED using $R_z(\text{yaw}) R_y(\text{pitch}) R_x(\text{roll})$.
4. **Ground Plane Intersection**: $t = h_{\text{AGL}} / d_{\text{down}}$; $\text{North} = t \cdot d_n, \text{East} = t \cdot d_e$.
5. **WGS84 Conversion**: Metres-per-degree series converts NED offsets to latitude/longitude.

### 3.3 Temporal Track Aggregation
To prevent one static piece of garbage from generating multiple markers across successive frames:
- **Association Radius**: Greedy matching within `merge_radius_m = 2.5 m`.
- **Position Smoothing**: Running confidence-weighted mean position across detections.
- **Confirmation Threshold**: Requires $\ge 2$ observations (`min_hits = 2`) before emitting a `"new"` marker.
- **Throttled Updates**: Position updates are emitted at most every 10 seconds (`update_interval_s = 10.0`).
- **Pruning**: Unconfirmed tracks older than 30 seconds are pruned; total tracks are bounded to 2,000.

---

## 4. Mobile Ground Control Station Integration

### 4.1 Cross-Drone Marker Fusion (`mobile/src/utils/garbage.js`)
When multiple drones in the swarm observe the same ground target:
- Sighting with the same track ID updates hits and confidence.
- Sighting with a different track ID within 2.0 m is treated as the same physical object seen by another swarm member: hits are accumulated, the reporting drone is appended to `seenBy`, and the existing ground coordinates are preserved.
- Markers are bounded to 500 entries (oldest evicted) and persisted to `localStorage` under `PhoneOS_Garbage`.

### 4.2 Interactive Map Display (`MapView.jsx`)
- **Icon**: Distinctive trash SVG marker.
- **Confidence Tinting**:
  - $\ge 70\%$: Red (`#ef4444`)
  - $50\% - 70\%$: Orange (`#f97316`)
  - $< 50\%$: Amber (`#f59e0b`)
- **Replay / Synthetic Indicator**: Detections from `ImageFolderSource` display a dashed ring and `[SYNTHETIC]` badge.
- **Popup Details**: Displays confidence, times seen, reporting drone, observing drones, first/last seen timestamps, and coordinates.
- **False-Positive Dismissal**: The "Dismiss (false positive)" button removes the marker and prevents it from reappearing during the session.
- **Command Controls**: A "Garbage (N)" toggle shows or hides markers, and a "Clear" button removes all session markers upon confirmation.

---

## 5. Honest Expectations & Simulation Notes

1. **Hardware & Training Requirements**: Fine-tuning YOLO on 772 high-resolution images at `imgsz=1280` requires a GPU (NVIDIA CUDA) or Google Colab. Sandboxes with restricted internet access will block Flickr CDN downloads.
2. **AirSim Domain Gap**: AirSim's default Unreal Engine environments contain clean, pristine terrain textures with zero litter. To test and demonstrate the end-to-end perception, geolocation, and mapping flow in simulation, use `ImageFolderSource` replay mode (which flags all detections as `synthetic=True`).
3. **Geolocation Drift**: Flat-ground projection assumes ground elevation equals the launch home elevation and relies on barometric/GPS relative altitude. Real-world GPS drift and sensor latency will introduce small positional offsets, which the 2.5 m merge radius accommodates.
4. **Empirical Metrics**: Only report mAP and precision numbers obtained from actual test split evaluation runs after fine-tuning.
