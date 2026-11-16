# AirSim Simulation Domain-Gap & Visual Fidelity Investigation

**Repository:** `Debanshu2005/DroneSwarm`  
**Branch:** `garbage-detection`  
**Subsystem:** AirSim Unreal Engine 4.27 Perception Pipeline & YOLO26n Detector  
**Date:** November 13, 2026  
**Auditor:** Senior Computer Vision & Perception Systems Architect  
**Validation Classification:** **BLOCKED (Simulation Asset & Sensor Environment Defect)**  

---

## 1. Executive Summary

In live AirSim hardware-in-the-loop and software-in-the-loop validation runs, the single-drone perception pipeline successfully connected via RPC, executed autonomous takeoff to $10.0\text{ m}$, captured camera frames, and ran YOLO26n inference. However, raw YOLO inference initially failed to detect the spawned target object on the floor.

A root-cause investigation revealed that this failure is **not an ML model failure**, but a **severe simulation asset, rendering, and resolution domain gap**:
1. **Asset Invisibility (BLOCKED):** The spawned target in Unreal Engine was an untextured grey primitive (`Cylinder`) with a standard deviation of pixel values $\sigma = 1.0$ and contrast $\Delta < 4\%$, making it mathematically and visually indistinguishable from the grey concrete floor.
2. **Camera Resolution Mismatch:** AirSim defaulted to $256 \times 144$ resolution due to Windows OneDrive folder redirection bypassing local `Documents/AirSim/settings.json`. At $256 \times 144$, a $0.5\text{ m}$ target occupies only $6 \times 6\text{ pixels}$, well below the receptive field of YOLO feature pyramids.
3. **Peer Drone False-Positive Cross-Matching:** Stationary peer drones in the swarm (Drones 2, 3, 4) stationed at $(5, 0)$, $(0, 5)$, and $(5, 5)$ appeared near the image periphery and triggered low-confidence detections ($\text{conf} \approx 0.12 - 0.14$), which earlier validators falsely cross-matched as the target.
4. **Synthetic vs. Real Domain Gap:** UAVVaste consists of high-resolution real-world aerial photos with organic soil, grass, shadows, and natural textures. AirSim Blocks map consists of flat polygon meshes with uniform synthetic shading.

Per scientific testing standards, **no AirSim mAP is reported** because the simulator lacks dense ground-truth bounding box annotations for the environment, and testing on an invisible target is classified as **BLOCKED**.

---

## 2. Investigation of Image & Sensor Dimensions

### 2.1. Empirical Image Measurements

Measurements performed on actual captured AirSim frames:

| Captured Frame Asset | Resolution | Mean BGR | Std BGR ($\sigma$) | Visual Description |
|---|:---:|:---:|:---:|---|
| `live_airsim_raw_frame.jpg` | $256 \times 144$ | $[195.7, 189.4, 187.1]$ | $[10.4, 11.1, 11.5]$ | Low-resolution camera capture with severe downsampling blur. |
| `live_airsim_target_visibility_patch.png` | $30 \times 30$ | $[206.2, 200.8, 198.6]$ | **$[1.0, 1.1, 1.1]$** | Crop of target ROI: completely flat untextured grey, $\sigma = 1.0$. |
| `test_live_1280x720_with_target.jpg` | $1280 \times 720$ | $[196.1, 189.7, 187.3]$ | $[8.7, 9.8, 10.5]$ | Full HD resolution frame showing peer drones and floor. |
| `live_test_cube_with_texture.jpg` | $1280 \times 720$ | $[196.1, 190.8, 188.4]$ | **$[13.0, 16.0, 17.3]$** | Hardened frame with multi-slot textured cube; visible contrast. |

### 2.2. Mathematical Ground Projection & Target Pixel Sizing
Under the pinhole camera model:
* Flight Altitude ($h$): $10.0\text{ m}$
* Camera Mount Pitch: $90.0^\circ$ (Nadir downward)
* Horizontal Field of View ($\text{HFOV}$): $90.0^\circ$
* Aspect Ratio: $16:9 \implies \text{VFOV} = 2 \cdot \arctan(\tan(45^\circ) \times 9/16) \approx 58.7^\circ$

Ground coverage footprint:
$$\text{Width}_{\text{ground}} = 2 \cdot h \cdot \tan\left(\frac{\text{HFOV}}{2}\right) = 2 \cdot 10.0 \cdot \tan(45^\circ) = 20.0\text{ m}$$
$$\text{Height}_{\text{ground}} = 20.0 \times \frac{9}{16} = 11.25\text{ m}$$

Pixel footprint comparison for a $0.5\text{ m} \times 0.5\text{ m}$ garbage target:
* **At $1280 \times 720$ (Production Standard):**
  $$\text{Pixel Pitch} = \frac{1280\text{ px}}{20.0\text{ m}} = 64.0\text{ px/m}$$
  $$\text{Target Size} = 0.5\text{ m} \times 64.0\text{ px/m} = \mathbf{32.0 \times 32.0\text{ pixels}}$$
  *At $32 \times 32\text{ px}$, the target spans $> 1000\text{ pixels}$ and is clearly resolvable by YOLO P3 head ($8\times$ downsampled stride).*

* **At $256 \times 144$ (AirSim Default Fallback):**
  $$\text{Pixel Pitch} = \frac{256\text{ px}}{20.0\text{ m}} = 12.8\text{ px/m}$$
  $$\text{Target Size} = 0.5\text{ m} \times 12.8\text{ px/m} = \mathbf{6.4 \times 6.4\text{ pixels}}$$
  *At $6.4 \times 6.4\text{ px}$, the target is smaller than the minimum convolution kernel stride, rendering detection physically impossible.*

---

## 3. Root Cause Analysis: The Unreal Engine Primitive & Texture Failure

```
+--------------------------------------------------------------------------------------------------+
|                              Unreal Engine Rendering Defect                                      |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|   AirSim Cylinder Primitive:                   Concrete Floor:                                   |
|   +-----------------------+                    +---------------------------------------------+   |
|   |   Top Cap (Slot 1)    |                    |                                             |   |
|   |   RGB: [206, 201, 199]|                    |            RGB: [196, 190, 187]             |   |
|   |   Std Dev: sigma = 1.0|                    |            Std Dev: sigma = 8.7             |   |
|   +-----------------------+                    |                                             |   |
|   | Side Walls (Slot 0)   |                    |                                             |   |
|   | (Textured)            |                    |                                             |   |
|   +-----------------------+                    +---------------------------------------------+   |
|                                                                                                  |
|   From Nadir Camera (90 deg Pitch):                                                              |
|   - Only the TOP CAP is visible! Side walls are occluded by orthographic angle.                  |
|   - simSetObjectMaterialFromTexture applied ONLY to Material Slot 0 (Side walls).                |
|   - The visible top cap remained untextured flat grey (contrast delta = 10 / 255 = 3.9%).        |
|   - Result: Invisible to computer vision, camera, and human eye.                                 |
|                                                                                                  |
+--------------------------------------------------------------------------------------------------+
```

### Why the Primitive Failed:
1. When calling `simSpawnObject(..., "Cylinder")`, Unreal Engine creates a static mesh actor with multiple material element slots:
   - Slot 0: Side cylinder surface
   - Slot 1: Top and bottom flat circular end-caps
2. The initial texturing call in AirSim Python API applied texture only to element index 0:
   `simSetObjectMaterialFromTexture(actor_name, texture_path)`
   This applied the garbage texture to the vertical sides of the cylinder.
3. Because the drone flies at nadir ($90^\circ$ pitch down looking straight down), **the camera only views the top cap**. The textured sides were completely occluded. The camera saw only the bare grey cap, yielding $\sigma = 1.0$ (essentially a uniform color patch).
4. **Remediation Implemented:**
   - Switched geometry from `Cylinder` to `Cube`.
   - Updated `simSetObjectMaterialFromTexture` to explicitly apply texture across both element slot 0 and element slot 1.
   - Verified that `roi_std` increased from $1.0$ to $> 13.0$, restoring visual contrast.

---

## 4. Peer-Drone False Positives & Ground-Truth Association

In live multi-drone AirSim simulations, 4 quadcopters are spawned in the Blocks map:
- `Drone1` (Origin, $[0, 0]$)
- `Drone2` ($[5, 0]$)
- `Drone3` ($[0, 5]$)
- `Drone4` ($[5, 5]$)

When Drone 1 climbs to $10\text{ m}$, its $90^\circ\text{ HFOV}$ camera captures the ground below. Drones 2, 3, and 4 sit within the ground footprint ($[0, 5]$ is $5\text{ m}$ North, within the $11.25\text{ m}$ longitudinal field of view).

### The Cross-Match Vulnerability:
1. The black frame, landing legs, and battery of quadcopters sitting on the grey concrete floor create high-contrast dark rectangular shapes.
2. YOLO26n detects these drone shapes at low confidence ($\text{conf} \approx 0.12 - 0.14$).
3. In earlier validation scripts, the association logic naively checked whether *any* confirmed detection fell within $2.5\text{ m}$ of the target. Drone 3's GPS position was sufficiently close to the search radius to trigger a false-positive pass.
4. **Remediation Implemented:**
   - Implemented `verify_target_visibility()` requiring $\Delta \mu \ge 15.0$ and $\sigma \ge 8.0$.
   - Implemented `associate_ground_truth_detection()` which projects the target's true 3D AirSim coordinate into image pixel coordinates and requires an $\text{IoU} \ge 0.20$ or Euclidean pixel center distance $< 35\text{ px}$.
   - Peer drone detections are rejected and tagged with distinct diagnostic colors.

---

## 5. Domain Gap: UAVVaste (Real-World) vs. AirSim (Synthetic)

| Attribute | UAVVaste Official Dataset | AirSim Unreal Engine Simulation | Domain Impact |
|---|---|---|---|
| **Terrain Background** | Natural grass, gravel roads, sand, soil, forest floors with high organic entropy. | Flat polygon meshes with uniform grey concrete texture (`Blocks` map). | High. Real model expects natural terrain context. |
| **Object Textures** | Real crumpled wrappers, weathered cardboard, transparent bottles, metallic cans with specular reflections. | Projected 2D PNG textures onto primitive 3D box/cylinder meshes. | Moderate to High. Synthetic flat shading lacks 3D relief. |
| **Lighting & Sun** | Natural directional sunlight, atmospheric haze, diffuse sky scattering, organic soft shadows. | Unreal Engine directional light, hard-edged shadow maps, no atmospheric scattering. | Moderate. Cast shadows in UE can trigger edge false positives. |
| **Sensor Artifacts** | Bayer demosaicing, optical vignetting, lens distortion, subtle motion blur. | Perfect synthetic pinhole rendering, zero lens distortion, zero sensor noise. | Low. Perfect synthetic edges can confuse edge detectors trained on lens blur. |

---

## 6. Classification & Operational Conclusion

1. **AirSim Test Classification:** **BLOCKED**
   - The failure of raw YOLO inference on untextured primitives in AirSim is an artifact of the simulation setup, not a deficiency in `models/garbage.pt`.
   - The test is correctly classified as `BLOCKED` until high-fidelity 3D assets with proper PBR materials are integrated into Unreal Engine.
2. **Model Integrity:** On genuine aerial imagery (UAVVaste test set), the model demonstrates **$93.57\%$ Precision** and **$82.58\%$ mAP@50**, proving that the model is fully capable of high-accuracy aerial detection when presented with realistic visual features.
3. **Actionable Recommendations for AirSim:**
   - Always ensure `settings.json` is synced to both `Documents/AirSim` and `OneDrive/Documents/AirSim` with `1280x720` resolution.
   - Use textured 3D static mesh assets with multi-slot material binding rather than untextured primitive actors.
   - Maintain the ground-truth pixel association engine to prevent peer drone cross-matches.

