# Live AirSim Validation Report: 3D Peer-Drone Exclusion in Multi-Drone Swarm Formation

**Repository:** `DroneSwarm` / `PhoneOS_Swarm`  
**Branch:** `garbage-detection`  
**Date:** November 17, 2026  
**Environment:** AirSim Unreal Engine 4 (`UE4Editor`, PID 32296, RPC `127.0.0.1:41451`)  
**Vehicles:** `['Drone1', 'Drone2', 'Drone3', 'Drone4']`  
**Model & Weights:** YOLO26n (`models/garbage.pt`, frozen, confidence=0.55, IOU=0.50)  
**Overall Verdict:** **PASS**

---

## 1. Executive Summary

During live formation flight in prior builds, mobile GCS users observed phantom garbage targets when four drones converged into close vertical or horizontal formation (e.g., displaying `GARBAGE (4)` or `GARBAGE (6)` despite only 2 physical garbage targets being present in the world). 

Forensic auditing revealed that the legacy 2D ground-projection peer filter mathematically projected camera rays past airborne lower drones down to the ground plane ($Z = 0$), evaluating horizontal ground distances as $> 4.0\text{ m}$ (often 4.51 m – 5.8 m) and erroneously accepting lower drones as genuine garbage objects.

We replaced the 2D projection mechanism with a **3D Camera-Ray Closest Point of Approach (CPA)** exclusion filter ($R_{\text{exclusion}} = 2.0\text{ m}$) across all four drone flight software instances (`DroneOS`, `DroneOS1`, `DroneOS2`, `DroneOS3`). 

This document certifies that the 3D peer-exclusion mechanism has now undergone rigorous live validation in the actual AirSim visual flight simulator across all 12 operational mission scenarios.

```
================================================================================
ALL 12 LIVE AIRSIM SCENARIOS PASSED! VERDICT: PASS
================================================================================
```

---

## 2. Test Architecture & Evidence Differentiation

To maintain strict scientific and forensic validity, the testing results are categorized into four distinct layers:

| Layer | Environment | Scope | Status | Result Artifacts |
| :--- | :--- | :--- | :--- | :--- |
| **1. Automated Suite** | Pytest unit/regression | Ray geometry, vector transformations, mathematical invariants | **PASS** (222 / 222 passed) | [test_peer_exclusion_3d_comprehensive.py](tests/test_peer_exclusion_3d_comprehensive.py) |
| **2. Headless Simulation** | Synthetic telemetry & mock frames | Multi-drone formation sweeps, edge cases, telemetry dropouts | **PASS** (11 / 11 scenarios) | [peer_exclusion_test_results.md](ml/reports/peer_exclusion_diagnostics/peer_exclusion_test_results.md) |
| **3. Live AirSim Visual** | `UE4Editor` (PID 32296) RPC `127.0.0.1:41451` | Live nadir camera acquisition from `bottom_center`, physical drone meshes, real scenery | **PASS** (12 / 12 scenarios) | [live_airsim_peer_exclusion_metrics.json](ml/reports/peer_exclusion_diagnostics/live_airsim_peer_exclusion_metrics.json) |
| **4. PhoneOS Integration** | Live Relay WebSocket `ws://127.0.0.1:8088` | End-to-end packet delivery from GFS through Relay to Mobile GCS | **PASS** (0 phantoms, 2 confirmed) | [live_relay_acks.db](ml/reports/peer_exclusion_diagnostics/live_relay_acks.db) |

---

## 3. Mathematical Comparison: Legacy 2D Ground Projection vs. 3D Ray CPA

### Legacy 2D Ground Projection (Why Failure Occurred)
When observing drone at altitude $z_{\text{obs}} = 15\text{ m}$ views a peer drone at altitude $z_{\text{peer}} = 5\text{ m}$ with horizontal offset $\Delta x = 2.5\text{ m}$:
$$x_{\text{ground}} = \frac{z_{\text{obs}}}{z_{\text{obs}} - z_{\text{peer}}} \cdot \Delta x = \frac{15}{15 - 5} \times 2.5 = 3.75\text{ m}$$
Because ground coordinate calculation projected through the peer to ground, the distance between the peer's actual ground coordinate ($2.5\text{ m}$) and projected point ($3.75\text{ m}$) or ground origin exceeded exclusion boundaries, causing the filter to accept the drone as a ground object. In PhoneOS, this generated:
- **`GARBAGE (4)` at alt 5.7 m:** 2 genuine objects + 2 lower drones detected.
- **`GARBAGE (6)` at alt 15.8 m:** 2 genuine objects + 4 cross-drone peer sightings.

### 3D Camera-Ray Closest Point of Approach (CPA)
For every detected bounding box center $(u, v)$:
1. Traces normalized camera ray $\vec{r}_{\text{NED}}$ via camera FOV and vehicle orientation:
   $$\vec{r}_{\text{NED}} = \mathbf{R}_{\text{body}\to\text{NED}} \cdot \mathbf{R}_{\text{cam}\to\text{body}} \cdot \begin{pmatrix} \frac{u - c_x}{f_x} \\ \frac{v - c_y}{f_y} \\ 1 \end{pmatrix}_{\text{normalized}}$$
2. Computes 3D relative position vector to peer drone:
   $$\vec{p} = \vec{P}_{\text{peer}} - \vec{P}_{\text{cam}}$$
3. Evaluates forward depth along ray:
   $$s^* = \vec{r}_{\text{NED}} \cdot \vec{p}$$
4. Evaluates perpendicular distance of closest approach:
   $$d_\perp = \|\vec{p} - s^* \vec{r}_{\text{NED}}\|$$
5. Peer exclusion rejection condition:
   $$\text{Decision} = \begin{cases} \text{REJECTED} & \text{if } s^* > 0 \text{ and } d_\perp < R_{\text{exclusion}} (2.0\text{ m}) \\ \text{DEFERRED} & \text{if peer telemetry missing or stale } (> 2.0\text{ s}) \\ \text{ACCEPTED} & \text{otherwise} \end{cases}$$

---

## 4. Live AirSim Scenario Results (12 / 12 Scenarios)

### Scenario 1: Peer Telemetry Mesh Across 4 Drones
- **Test:** Connected to live AirSim vehicles `['Drone1', 'Drone2', 'Drone3', 'Drone4']` and verified mesh discovery.
- **Result:** **PASS**. Every drone node verified 3 active peers (`['drone2', 'drone3', 'drone4']`, etc.) with continuous 10 Hz telemetry synchronization.

### Scenario 2: Baseline Genuine Target Acquisition
- **Target 1:** NED $(19.13, 4.42, 0.0)$, GPS $(47.641640, -122.140106)$
- **Target 2:** NED $(16.33, 9.71, 0.0)$, GPS $(47.641615, -122.140036)$
- **Screenshots Captured:**
  - [live_airsim_scenario2_target1.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario2_target1.png)
  - [live_airsim_scenario2_target2.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario2_target2.png)
- **GFS Fusion Count:** Exactly 2 confirmed global objects (`G-7bfd38df` and `G-2cfc8b87`).
- **Result:** **PASS**.

### Scenario 3: Swarm Formation Maneuvering
- **Maneuver:** 4-drone dynamic `V` formation at spacing $12.0\text{ m}$.
- **Screenshot Captured:** [live_airsim_scenario3_formation.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario3_formation.png)
- **Result:** **PASS**.

### Scenario 4: Direct Vertical Overlap (Drone 1 at 15 m over Drone 2 at 5 m)
- **Position:** Drone 1 at NED $(10.0, 10.0, -15.0)$, Drone 2 at NED $(10.0, 10.0, -5.0)$.
- **Evaluated Geometry:** Forward depth $s^* = 10.00\text{ m}$, CPA $d_\perp = 0.000\text{ m} < 2.00\text{ m}$.
- **Decision:** `REJECTED` (`REJECTED_PEER_EXCLUSION_MATCH_3D`).
- **GFS Target Count:** Strictly 2 (0 phantom objects created).
- **Screenshot Captured:** [live_airsim_scenario4_vertical_d1_over_d2.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario4_vertical_d1_over_d2.png)
- **Result:** **PASS**.

### Scenario 5: Reverse Vertical Overlap (Drone 2 at 15 m over Drone 1 at 5 m)
- **Position:** Drone 2 at NED $(10.0, 10.0, -15.0)$, Drone 1 at NED $(10.0, 10.0, -5.0)$.
- **Evaluated Geometry:** Forward depth $s^* = 10.00\text{ m}$, CPA $d_\perp = 0.000\text{ m} < 2.00\text{ m}$.
- **Decision:** `REJECTED` (`REJECTED_PEER_EXCLUSION_MATCH_3D`).
- **GFS Target Count:** Strictly 2.
- **Screenshot Captured:** [live_airsim_scenario5_vertical_d2_over_d1.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario5_vertical_d2_over_d1.png)
- **Result:** **PASS**.

### Scenario 6: Horizontal & Vertical Separation Sweep
- **Configuration:** Drone 1 at $(0.0, 0.0, -15.0)$, Drone 2 at $(r, 0.0, -5.0)$ for $r \in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.5, 5.0, 8.0, 12.0]\text{ m}$.
- **Quantitative Results Table:**

| Separation $r$ | Target Pixel $(u, v)$ | In FOV? | Ray Depth $s^*$ | Closest Approach $d_\perp$ | Filter Decision | Rejection Reason |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **0.0 m** | $(640, 360)$ | YES | $10.0\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **0.5 m** | $(640, 328)$ | YES | $10.0\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **1.0 m** | $(640, 296)$ | YES | $10.0\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **1.5 m** | $(640, 264)$ | YES | $10.1\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **2.0 m** | $(640, 232)$ | YES | $10.2\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **2.5 m** | $(640, 200)$ | YES | $10.3\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **3.5 m** | $(640, 136)$ | YES | $10.6\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **5.0 m** | $(640, 40)$ | YES | $11.2\text{ m}$ | **$0.00\text{ m}$** | **REJECTED** | `MATCH_3D (peer=drone2, d_perp=0.00m)` |
| **8.0 m** | $(640, -152)$| OUT OF FOV | — | — | — | Outside camera nadir frustum |
| **12.0 m**| $(640, -408)$| OUT OF FOV | — | — | — | Outside camera nadir frustum |

- **Result:** **PASS** (100% rejection rate for in-FOV peer detections).

### Scenario 7: Dynamic Trajectory Crossing Flight Test
- **Trajectory:** Drone 3 moving $+X$ ($2\to 18\text{ m}$) at $Z = -10\text{ m}$; Drone 4 moving $-X$ ($18\to 2\text{ m}$) at $Z = -15\text{ m}$.
- **Result:** **PASS**. In-flight crossing intercepted and rejected with 0 phantom targets.
- **Screenshot Captured:** [live_airsim_scenario7_crossing.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario7_crossing.png)

### Scenario 8: Standby & Disarmed Peer Handling
- **Configuration:** Drone 3 disarmed on ground ($Z = 0.0\text{ m}$); Drone 1 observing from $Z = -10.0\text{ m}$.
- **Detection 1 (Directly over landed Drone 3):** Evaluated and **REJECTED** ($d_\perp = 0.00\text{ m}$).
- **Detection 2 (Genuine target 7 m away):** Evaluated and **ACCEPTED** (`ACCEPTED_NO_PEER_MATCH`).
- **Result:** **PASS**. Landed peers excluded; genuine ground targets preserved.

### Scenario 9: Temporary Telemetry Interruption & Recovery
- **Interruption:** Drone 2 telemetry updates suspended (`skip_drone="drone2"`), telemetry age aged to $5.0\text{ s} > 2.0\text{ s}$.
- **Evaluation 1 (Stale Telemetry):** Evaluated as **`DEFERRED`** (`DEFERRED_UNCERTAIN_PEER_TELEMETRY`). GFS confirmed objects count held strictly at 2.
- **Evaluation 2 (Telemetry Recovered):** Telemetry re-established; detection evaluated as **`REJECTED`** (`REJECTED_PEER_EXCLUSION_MATCH_3D`).
- **Result:** **PASS**.

### Scenario 10: Genuine Garbage Near a Peer Drone
- **Configuration:** Target 1 at $(19.13, 4.42, 0.0)$. Drone 1 observing from $(19.13, 4.42, -10.0)$. Drone 2 stationed at $(19.13, 7.92, -5.0)$ ($3.5\text{ m}$ horizontal separation).
- **Evaluation:** Perpendicular CPA $d_\perp = 3.50\text{ m} > 2.00\text{ m}$ exclusion radius.
- **Decision:** **`ACCEPTED`** (`ACCEPTED_NO_PEER_MATCH`).
- **Screenshot Captured:** [live_airsim_scenario10_target_near_peer.png](ml/reports/peer_exclusion_diagnostics/live_airsim_scenario10_target_near_peer.png)
- **Result:** **PASS**. Genuine target within 3.5 m of a peer drone remains completely detectable.

### Scenario 11: PhoneOS / GCS Live Integration Verification
- **Relay WebSocket:** Connected to `ws://127.0.0.1:8088`.
- **Payload Received:**
  ```json
  {
    "type": "GLOBAL_TARGETS_UPDATE",
    "msg_type": "detection",
    "sender_id": "gfs",
    "confirmed_count": 2,
    "targets": [
      {"id": "G-7bfd38df", "lat": 47.641640, "lon": -122.140106, "hits": 2, "status": "CONFIRMED"},
      {"id": "G-2cfc8b87", "lat": 47.641615, "lon": -122.140036, "hits": 1, "status": "CONFIRMED"}
    ]
  }
  ```
- **Result:** **PASS**. PhoneOS mobile UI displays strictly `GARBAGE (2)`.

### Scenario 12: Continuous Swarm Formation Stability Invariant
- **Flight Test:** 15 seconds of continuous collaborative 4-drone flight in formation.
- **Cycles Completed:** 2 complete multi-drone camera acquisition and inference cycles.
- **Final Confirmed Global Objects:** Exactly 2 (0 phantom targets).
- **Result:** **PASS**.

---

## 5. Quantitative Diagnostic Metrics Summary

From [live_airsim_peer_exclusion_metrics.json](ml/reports/peer_exclusion_diagnostics/live_airsim_peer_exclusion_metrics.json) and [live_airsim_peer_exclusion_events.jsonl](ml/reports/peer_exclusion_diagnostics/live_airsim_peer_exclusion_events.jsonl):

- **Total Diagnostic Events Logged:** 13,523 events
- **Total Raw Detections:** 24
- **Accepted Detections:** 10
- **Rejected Detections:** 13
- **Deferred Detections:** 1 (safely recovered upon telemetry re-establishment)
- **Peer Exclusion Decisions:** 24
- **Peer Exclusion Rejections:** 13
- **GFS Confirmed Global Objects:** Exactly 2
- **Phantom Targets Generated:** **0**

---

## 6. Parity Verification Across Flight Nodes

The automated synchronization script (`scripts/sync_perception.py`) verified 100% code parity across all 4 production nodes:
- `DroneOS/perception/pipeline.py` $\iff$ `DroneOS1/perception/pipeline.py` (0 drift)
- `DroneOS/perception/pipeline.py` $\iff$ `DroneOS2/perception/pipeline.py` (0 drift)
- `DroneOS/perception/pipeline.py` $\iff$ `DroneOS3/perception/pipeline.py` (0 drift)
- `DroneOS/perception/geolocate.py` $\iff$ `DroneOS1, 2, 3` (0 drift)

---

## 7. Final Certification & Verdict

All requirements have been met without modifying YOLO model weights, without changing detector confidence thresholds, and without arbitrarily inflating the peer exclusion radius:

1. **Automated Unit & Regression Tests:** PASS (222 passed)
2. **Headless Reproduction Tests:** PASS (11 scenarios passed)
3. **Live AirSim Simulation Tests:** PASS (12 / 12 scenarios passed)
4. **PhoneOS Relay Integration:** PASS (Strictly 2 confirmed global targets)

**FINAL VERDICT:** **PASS**
