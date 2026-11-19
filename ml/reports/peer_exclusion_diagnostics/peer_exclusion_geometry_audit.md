# Formal Mathematical & Geometric Audit of Peer-Drone Exclusion Filters

**Repository:** `DroneSwarm` / `PhoneOS_Swarm`  
**Branch:** `garbage-detection`  
**Document ID:** `peer_exclusion_geometry_audit.md`  
**Associated Results File:** [peer_exclusion_geometry_results.json](ml/reports/peer_exclusion_diagnostics/peer_exclusion_geometry_results.json)  
**Investigation Date:** 2026-11-16  

---

## 1. Executive Summary & Audit Objective

The forensic investigation of false garbage detections during 4-drone formation revealed that when drones fly vertically over one another, lower peer drones are frequently misidentified as garbage targets by YOLO and displayed as confirmed red markers on the mobile Ground Control Station (PhoneOS GCS).

While the preliminary diagnostic report proposed replacing the existing 2D ground-projection filter with a 3D ray-based filter, it contained an apparent mathematical discrepancy:
- The prose text derived a 1D collinear distance of $\text{dist} = |1.5r - r| = 0.5r$.
- However, the diagnostic table reported an evaluated distance of $4.51\text{m}$ at $r = 2.5\text{m}$ (where $0.5 \times 2.5 = 1.25\text{m} \ne 4.51\text{m}$).

This audit resolves the exact programmatic and physical causes of this discrepancy, audits all coordinate transformations and altitude datums in `geolocate.py` and `pipeline.py`, recalculates every row in the sweep, and formally designs the 3D peer-exclusion algorithm and telemetry failure handling before any production code is modified.

---

## 2. Coordinate Transformation & Frame Conventions Audit

To determine why the distance calculation diverged, every step of the perception pipeline was traced across coordinate frames:

### 2.1 Coordinate Frames
1. **Camera Frame $(\mathbf{X}_{\text{cam}}, \mathbf{Y}_{\text{cam}}, \mathbf{Z}_{\text{cam}})$:**
   - Standard OpenCV/Pinhole optical convention.
   - $\mathbf{X}_{\text{cam}}$: Right (parallel to image sensor horizontal axis).
   - $\mathbf{Y}_{\text{cam}}$: Down (parallel to image sensor vertical axis).
   - $\mathbf{Z}_{\text{cam}}$: Forward along the optical axis ($+Z$ into the scene).
   - Normalized ray coordinates from pixel $(u, v)$ with image dimensions $(W, H)$ and focal length $f_x, f_y$:
     $$x_{\text{cam}} = \frac{u - c_x}{f_x}, \quad y_{\text{cam}} = \frac{v - c_y}{f_y}, \quad z_{\text{cam}} = 1.0$$

2. **Body Frame (FRD — Forward, Right, Down):**
   - Fixed to the drone airframe center of mass.
   - $\mathbf{X}_{\text{body}}$: Forward.
   - $\mathbf{Y}_{\text{body}}$: Right.
   - $\mathbf{Z}_{\text{body}}$: Down.
   - Forward-looking camera baseline: $\mathbf{v}_{\text{body0}} = [z_{\text{cam}}, x_{\text{cam}}, y_{\text{cam}}]^T$.
   - Mount Pitch Rotation: The nadir camera is pitched downward by $\theta_{\text{mount}} = 90.0^\circ$ via rotation matrix $\mathbf{R}_y(-\theta_{\text{mount}})$:
     $$\mathbf{R}_y(-90^\circ) = \begin{bmatrix} 0 & 0 & -1 \\ 0 & 1 & 0 \\ 1 & 0 & 0 \end{bmatrix}$$
     $$\mathbf{v}_{\text{body}} = \mathbf{R}_y(-90^\circ) \begin{bmatrix} z_{\text{cam}} \\ x_{\text{cam}} \\ y_{\text{cam}} \end{bmatrix} = \begin{bmatrix} -y_{\text{cam}} \\ x_{\text{cam}} \\ z_{\text{cam}} \end{bmatrix} = \begin{bmatrix} -\frac{v - c_y}{f_y} \\ \frac{u - c_x}{f_x} \\ 1.0 \end{bmatrix}$$
     - $\mathbf{X}_{\text{body}}$ (Forward): $-y_{\text{cam}}$ (top of image corresponds to aircraft Forward).
     - $\mathbf{Y}_{\text{body}}$ (Right): $+x_{\text{cam}}$ (right of image corresponds to aircraft Right).
     - $\mathbf{Z}_{\text{body}}$ (Down): $+z_{\text{cam}} = 1.0$ (optical axis points Downward).

3. **World / Navigation Frame (NED — North, East, Down):**
   - Local tangent plane aligned with geographic North, East, and Nadir Down.
   - Rotated from Body frame via Euler Z-Y-X rotation:
     $$\mathbf{R}_{\text{body}\to\text{ned}} = \mathbf{R}_z(\psi) \cdot \mathbf{R}_y(\theta) \cdot \mathbf{R}_x(\phi)$$
     where $\psi = \text{yaw}, \theta = \text{pitch}, \phi = \text{roll}$.
   - For level flight $(\phi = 0, \theta = 0, \psi = 0)$:
     $$\mathbf{d}_{\text{ned}} = \mathbf{v}_{\text{body}} = \begin{bmatrix} d_N \\ d_E \\ d_D \end{bmatrix} = \begin{bmatrix} -\frac{v - c_y}{f_y} \\ \frac{u - c_x}{f_x} \\ 1.0 \end{bmatrix}$$
     - **Ray North component ($d_N$):** Governed solely by vertical pixel displacement $(v - c_y)$.
     - **Ray East component ($d_E$):** Governed solely by horizontal pixel displacement $(u - c_x)$.

4. **Geodetic Frame (WGS84 Latitude, Longitude, Altitude):**
   - Transformed to local metric displacements using meridian and parallel radii of curvature (`wgs84_meters_per_degree`):
     $$\Delta n = (\text{lat} - \text{base\_lat}) \cdot m_{\text{lat}}(\text{base\_lat})$$
     $$\Delta e = (\text{lon} - \text{base\_lon}) \cdot m_{\text{lon}}(\text{base\_lat})$$

---

## 3. Discrepancy Resolution: Collinear Derivation vs. Script Execution

### 3.1 The Stated Derivation (1D Collinear Geometry)
In the diagnostic report prose, the scenario was analyzed as a 1-dimensional collinear projection along a single horizontal axis:
- Upper Drone at altitude $z_A = 15.0\text{m}$, location $(0, 0)$.
- Lower Drone at altitude $z_B = 5.0\text{m}$, location $(r, 0)$ (offset along axis $X$).
- Ground plane at $z = 0.0\text{m}$.
- The ray passing through $(0, z_A)$ and $(r, z_B)$ has slope:
  $$\frac{dx}{dz} = \frac{r - 0}{z_A - z_B} = \frac{r}{10.0}$$
- The ray strikes the ground ($z=0$, $\Delta z_{\text{ground}} = z_A = 15.0\text{m}$) at:
  $$x_{\text{ground}} = z_A \cdot \frac{r}{z_A - z_B} = \frac{15.0}{10.0} \cdot r = 1.5 r$$
- Along this 1D collinear axis, the distance from the ground intersection point $x_{\text{ground}}$ to the peer's horizontal location $x_{\text{peer}} = r$ is:
  $$\text{dist}_{\text{collinear}} = |x_{\text{ground}} - x_{\text{peer}}| = |1.5r - r| = 0.5r$$
- Under this 1D formula, at $r = 2.5\text{m}$:
  $$\text{dist} = 0.5 \times 2.5 = 1.25\text{m}$$
  Since $1.25\text{m} < 4.0\text{m}$, a 1D collinear filter would have **REJECTED** the detection.

### 3.2 The Actual Implementation in `reproduce_peer_false_detections.py`
In `reproduce_peer_false_detections.py` (lines 319–331):
```python
d2_lat, d2_lon = meters_to_lat_lon(sep, 0.0, ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001)
...
tan_theta = sep / 10.0
u_px = 640 + int(640 * tan_theta)
det_d1.set_detections([Detection(bbox_xyxy=(u_px - 20, 340, u_px + 20, 380), ...)])
```
1. `meters_to_lat_lon(dn, de, base_lat, base_lon)`:
   The script passed `sep` as the **first argument (`dn`)**, and `0.0` as `de`!
   $$\mathbf{P}_{\text{peer}} = (r\text{ North}, 0.0\text{ East}, 5.0\text{ Alt})$$
2. But in the camera frame:
   The script placed the bounding box at horizontal pixel $u_{\text{px}}$ with $v = 360$ (the center row).
   From Section 2:
   $$d_N = -\frac{360 - 360}{640} = 0.0$$
   $$d_E = +\frac{u_{\text{px}} - 640}{640} = \tan\theta = \frac{r}{10.0}$$
   $$d_D = 1.0$$
3. Therefore, the ground intersection point in NED was:
   $$\text{Ground}_N = 15.0 \cdot d_N = 0.0\text{ m}$$
   $$\text{Ground}_E = 15.0 \cdot d_E = 1.5 r\text{ m}$$
   $$\mathbf{P}_{\text{ground}} = (0.0\text{ North}, 1.5r\text{ East})$$
4. When `pipeline.py` evaluated the distance using `lat_lon_to_meters`:
   $$\Delta n = \text{Ground}_N - \text{Peer}_N = 0.0 - r = -r$$
   $$\Delta e = \text{Ground}_E - \text{Peer}_E = 1.5r - 0.0 = 1.5r$$
   $$\text{dist}_{\text{script}} = \sqrt{(\Delta n)^2 + (\Delta e)^2} = \sqrt{(-r)^2 + (1.5r)^2} = \sqrt{r^2 + 2.25 r^2} = \sqrt{3.25} \cdot r \approx 1.8027756 \cdot r$$

### 3.3 Evaluation for $r = 2.5\text{m}$
$$\text{dist}_{\text{script}} = \sqrt{3.25} \times 2.5 = 1.8027756 \times 2.5 = 4.506939\text{ m} \approx 4.51\text{ m}$$

> [!IMPORTANT]
> **Root Cause of the Discrepancy:**
> The diagnostic table reported the actual programmatic output from `pipeline.py` ($4.51\text{m}$), which evaluated orthogonal axes ($\Delta n = -r, \Delta e = +1.5r$), while the prose text described a 1D collinear geometry ($0.5r$).
> Both mathematical expressions are correct for their respective coordinate setups:
> - **1D Collinear Geometry:** $\text{dist} = |1.5r - r| = 0.5r$
> - **Orthogonal Script Geometry:** $\text{dist} = \sqrt{1.0^2 + 1.5^2} \cdot r = \sqrt{3.25} \cdot r \approx 1.8028r$

---

## 4. Complete Recalculation Table of Horizontal Separation Sweep

The table below recalculates every row in the sweep across:
1. The **1D Collinear** case (peer East, ray East).
2. The **Test Script Orthogonal** case (peer North, ray East).
3. The **True 3D Ray-to-Peer Closest Approach ($d_\perp$)** in both configurations.

| Separation $r$ | Pixel $(u, v)$ | 1D Collinear Ground Dist ($0.5r$) | 1D Collinear 4m Decision | Script Orthogonal Ground Dist ($\sqrt{3.25}r$) | Script Orthogonal 4m Decision | 3D Ray-to-Peer $d_\perp$ (Collinear) | 3D Ray-to-Peer $d_\perp$ (Script) | True 3D Depth $s^*$ |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **0.0 m** | (640, 360) | 0.000 m | **REJECTED** | 0.000 m | **REJECTED** | **0.000 m** | 0.000 m | 10.000 m |
| **1.0 m** | (704, 360) | 0.500 m | **REJECTED** | 1.803 m | **REJECTED** | **0.000 m** | 1.411 m | 10.050 m |
| **2.0 m** | (768, 360) | 1.000 m | **REJECTED** | 3.606 m | **REJECTED** | **0.000 m** | 2.801 m | 10.198 m |
| **2.5 m** | (800, 360) | 1.250 m | **REJECTED** | 4.507 m | **ACCEPTED (FAIL)** | **0.000 m** | 3.483 m | 10.308 m |
| **3.5 m** | (864, 360) | 1.750 m | **REJECTED** | 6.310 m | **ACCEPTED (FAIL)** | **0.000 m** | 4.813 m | 10.595 m |
| **4.0 m** | (896, 360) | 2.000 m | **REJECTED** | 7.211 m | **ACCEPTED (FAIL)** | **0.000 m** | 5.458 m | 10.770 m |
| **4.5 m** | (928, 360) | 2.250 m | **REJECTED** | 8.113 m | **ACCEPTED (FAIL)** | **0.000 m** | 6.090 m | 10.966 m |
| **6.0 m** | (1024, 360) | 3.000 m | **REJECTED** | 10.817 m | **ACCEPTED (FAIL)** | **0.000 m** | 7.904 m | 11.662 m |
| **8.0 m** | (1152, 360) | 4.000 m | **REJECTED** | 14.422 m | **ACCEPTED (FAIL)** | **0.000 m** | 10.150 m | 12.806 m |
| **10.0 m** | (1280, 360) | 5.000 m | **ACCEPTED (FAIL)** | 18.028 m | **ACCEPTED (FAIL)** | **0.000 m** | 12.247 m | 14.142 m |

---

## 5. The Fundamental Flaw of 2D Ground Projection in Formation Flight

Beyond the script's orthogonal test setup, there is a fundamental mathematical flaw in using 2D ground projection for peer exclusion in swarm formations.

Let Upper Drone fly at $z_A$, and Lower Drone fly at $z_B = z_A - \Delta z$, with arbitrary horizontal offset vector $\vec{r} = (\Delta n, \Delta e)$ and magnitude $r = \|\vec{r}\|$.
If the camera ray is aimed at the lower drone, the ground intersection occurs at:
$$\vec{P}_{\text{ground}} = \frac{z_A}{\Delta z} \cdot \vec{r}$$
The peer's horizontal location is:
$$\vec{P}_{\text{peer}} = \vec{r}$$
The distance evaluated on the ground is:
$$\text{dist}_{\text{ground}} = \|\vec{P}_{\text{ground}} - \vec{P}_{\text{peer}}\| = \left\| \frac{z_A}{\Delta z} \vec{r} - \vec{r} \right\| = \left( \frac{z_A - \Delta z}{\Delta z} \right) \cdot r = \left( \frac{z_B}{\Delta z} \right) \cdot r$$

### Ground Error Amplification Analysis:
When drones fly in multi-layer swarm formations, the vertical layer spacing $\Delta z$ is typically $1.0\text{m} - 2.0\text{m}$, while operating altitude $z_B$ is $10\text{m} - 15\text{m}$:

| Upper Alt $z_A$ | Peer Alt $z_B$ | Vertical Layer $\Delta z$ | Horizontal Separation $r$ | Amplification Factor $\frac{z_B}{\Delta z}$ | Evaluated 2D Ground Distance | Filter Result ($R=4.0\text{m}$) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 15.0 m | 14.0 m | 1.0 m | **0.35 m** | **14.0×** | **4.90 m** | **ACCEPTED (FALSE POSITIVE)** |
| 15.0 m | 13.5 m | 1.5 m | **0.50 m** | **9.0×** | **4.50 m** | **ACCEPTED (FALSE POSITIVE)** |
| 15.0 m | 13.0 m | 2.0 m | **0.70 m** | **6.5×** | **4.55 m** | **ACCEPTED (FALSE POSITIVE)** |
| 15.0 m | 12.0 m | 3.0 m | **1.10 m** | **4.0×** | **4.40 m** | **ACCEPTED (FALSE POSITIVE)** |
| 10.0 m | 9.0 m | 1.0 m | **0.50 m** | **9.0×** | **4.50 m** | **ACCEPTED (FALSE POSITIVE)** |

> [!CAUTION]
> **Conclusion:** As $\Delta z \to 0$, the ground projection distance diverges to infinity:
> $$\lim_{\Delta z \to 0} \text{dist}_{\text{ground}} = \infty$$
> At normal formation altitudes, a minor horizontal separation of only $35\text{cm} - 70\text{cm}$ magnifies to over $4.5\text{m}$ on the ground, completely bypassing the 4m exclusion filter. 
> Therefore, **2D ground projection cannot reliably exclude peer drones during formation flight.**

---

## 6. Specification of the 3D Ray-to-Volume Exclusion Algorithm

To resolve this issue robustly without artificially expanding the exclusion radius, exclusion must be computed directly in 3D camera-ray space.

### 6.1 Mathematical Formulation

1. **Camera Ray in World (NED) Frame:**
   Given detected bounding-box center $(u, v)$, camera intrinsics $(f_x, f_y, c_x, c_y)$, mount pitch $\theta_{\text{mount}}$, and drone attitude $(\phi, \theta, \psi)$:
   $$\mathbf{v}_{\text{cam}} = \begin{bmatrix} (u - c_x) / f_x \\ (v - c_y) / f_y \\ 1.0 \end{bmatrix}$$
   $$\mathbf{v}_{\text{body}} = \mathbf{R}_y(-\theta_{\text{mount}}) \cdot \begin{bmatrix} \mathbf{v}_{\text{cam}, z} \\ \mathbf{v}_{\text{cam}, x} \\ \mathbf{v}_{\text{cam}, y} \end{bmatrix}$$
   $$\mathbf{d}_{\text{ned}} = \mathbf{R}_z(\psi) \mathbf{R}_y(\theta) \mathbf{R}_x(\phi) \cdot \mathbf{v}_{\text{body}}$$
   $$\hat{\mathbf{d}} = \frac{\mathbf{d}_{\text{ned}}}{\|\mathbf{d}_{\text{ned}}\|}$$

2. **Observing Camera 3D Position:**
   $$\mathbf{P}_{\text{cam}} = \mathbf{P}_{\text{drone}} + \mathbf{R}_{\text{body}\to\text{ned}} \cdot \mathbf{b}_{\text{cam\_offset}}$$

3. **Peer 3D Position Relative to Camera:**
   For peer $k$ with reported telemetry $(\text{lat}_k, \text{lon}_k, \text{alt}_k)$:
   $$\Delta n_k, \Delta e_k = \text{lat\_lon\_to\_meters}(\text{lat}_k, \text{lon}_k, \text{lat}_{\text{drone}}, \text{lon}_{\text{drone}})$$
   $$\Delta d_k = - (\text{alt}_k - \text{alt}_{\text{drone}})$$
   $$\mathbf{w}_k = \begin{bmatrix} \Delta n_k \\ \Delta e_k \\ \Delta d_k \end{bmatrix} - \mathbf{b}_{\text{cam\_offset\_ned}}$$

4. **Closest Point of Approach (CPA) on Forward Ray:**
   The forward camera ray is parameterized by along-ray distance $s \ge 0$:
   $$\mathbf{R}(s) = \mathbf{P}_{\text{cam}} + s \, \hat{\mathbf{d}}, \quad s \ge 0$$
   The along-ray depth to the closest point of approach is:
   $$s_k^* = \mathbf{w}_k \cdot \hat{\mathbf{d}}$$

5. **Forward Ray Constraint ($s^* > 0$):**
   - If $s_k^* \le 0$: The peer is behind or coplanar with the camera plane. It cannot be seen in the camera frame. The peer is **not matching**.

6. **Orthogonal Miss Distance ($d_\perp$):**
   For $s_k^* > 0$, the closest point on the ray to the peer is $\mathbf{P}_{\text{cpa}} = s_k^* \hat{\mathbf{d}}$.
   The 3D cross-track distance is:
   $$d_{\perp, k} = \|\mathbf{w}_k - s_k^* \hat{\mathbf{d}}\| = \sqrt{\max\left(0.0, \|\mathbf{w}_k\|^2 - (s_k^*)^2\right)}$$

7. **Peer Exclusion Volume Check:**
   A peer matches the ray if:
   $$d_{\perp, k} < R_{\text{exclusion}}$$
   where $R_{\text{exclusion}} = 1.8\text{m} - 2.0\text{m}$ (encompassing physical quadrotor diameter of $0.8\text{m}$, GPS positional uncertainty $\pm 0.5\text{m}$, and time sync latency).

8. **Depth Disambiguation (Genuine Ground Garbage vs. Airborne Peer):**
   To ensure genuine garbage on the ground directly along a similar line of sight is not excluded:
   - Ground intersection depth: $s_{\text{ground}} = \frac{\text{height\_agl}}{\hat{\mathbf{d}}_D}$.
   - If peer is airborne ($s_k^* < s_{\text{ground}} - 2.0\text{m}$) and $d_{\perp, k} < R_{\text{exclusion}}$:
     The peer physically occludes the camera ray in mid-air. The detection belongs to the peer and is **REJECTED**.
   - If the peer is on the ground ($s_k^* \approx s_{\text{ground}}$):
     A detection is only excluded if the ground target is within $R_{\text{exclusion}}$ of the landed peer. Genuine garbage further away is **ACCEPTED**.

---

## 7. Telemetry Failure Modes and Remediation Specification

A peer must not be excluded or ignored due to transient telemetry anomalies:

1. **Disarmed / Standby Peers:**
   - **Current Flaw:** `pipeline.py` checked `p_active and (p_alt > 1.0)`. Disarmed or standby peers were skipped.
   - **Remediation:** Remove the `is_active` filter. Any peer in the swarm registry with known coordinates is evaluated for exclusion regardless of flight mode.
2. **Telemetry Arriving Before Heartbeat:**
   - **Current Flaw:** `handle_telemetry` only updated position if `peer in self.registry.peers`. If telemetry arrived first, it was dropped.
   - **Remediation:** In `handle_telemetry`, if `msg.sender_id not in self.registry.peers`, automatically register the peer.
3. **Missing or Stale Telemetry Handling:**
   - **Current Flaw:** If a peer's GPS was temporarily invalid (`gps_valid = False` or `lat = None`), or stale ($> 2.0\text{s}$), the pipeline classified detections as confirmed garbage.
   - **Remediation:** When peer telemetry is missing or stale, detections within the active swarm operating boundary must NOT be promoted to confirmed status. Instead, they are marked with `status: "provisional"` (or held unconfirmed in local track aggregator) until peer telemetry recovers, preventing premature global object creation in GFS.

---

## 8. Summary of Resolution & Next Steps

1. The mathematical discrepancy between the report's $0.5r$ text and the table's $4.51\text{m}$ value is resolved: the former is 1D collinear, while the latter is orthogonal script geometry ($\sqrt{3.25}r$).
2. The 2D ground projection error has been mathematically proven to diverge as $\frac{z_B}{\Delta z} r$, causing systematic filter failures in multi-drone formations.
3. The 3D Ray-to-Volume algorithm has been specified with full coordinate transformations, forward ray constraints, and depth disambiguation.
4. With the geometry and coordinate assumptions documented and audited, implementation across all four DroneOS instances may proceed safely.
