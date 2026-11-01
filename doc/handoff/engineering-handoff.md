# Handoff: Engineering Handoff Document

## 1. Project Health & Current Status

As of November 2026, the **PhoneOS Swarm** repository is fully operational and verified:
- **Test Suite Status:** 1,119 tests passing in `DroneOS/tests` (118 skipped for live hardware/sim, 0 failures).
- **Mirrored Test Parity:** Complete regression suites pass across all four mirrored packages (`DroneOS`, `DroneOS1`, `DroneOS2`, `DroneOS3`).
- **Core Capability:**
  - 4-drone autonomous formation flight (V, Diamond, Column, Line, Circle, Square, Grid, Echelon).
  - Autonomous mid-air self-healing for both anchor (leader) loss and follower loss.
  - Full SITL simulation via Microsoft AirSim.
  - Production hardware readiness on Raspberry Pi + Pixhawk PX4.
  - Mobile Ground Control Station with live artificial horizons and NLP terminal.

---

## 2. Non-Negotiable Architectural Invariants

Any engineer modifying or maintaining this codebase must adhere to the following rules:

### 2.1 The Four-Package Mirrored Isolation Invariant
- The repository maintains four separate drone packages: `DroneOS`, `DroneOS1`, `DroneOS2`, `DroneOS3`.
- `DroneOS1`, `DroneOS2`, and `DroneOS3` must **strictly import only from their own package** (`from DroneOS1.core...`, `from DroneOS2.core...`, etc.).
- They must **never** import from `DroneOS.*` (outside docstrings and comments).
- **CI / Verification Check:** Always execute this audit before pushing any branch:
  ```powershell
  python -c "import os; violations = [f'{root}/{f}: {line.strip()}' for root, _, files in os.walk('.') for f in files if f.endswith('.py') and any(root.startswith(d) for d in ['DroneOS1','DroneOS2','DroneOS3']) for line in open(os.path.join(root,f), errors='ignore') if 'from DroneOS.' in line.split('#')[0] or 'import DroneOS.' in line.split('#')[0]]; print(f'Violations: {len(violations)}')"
  ```
  This must return `Violations: 0`.

### 2.2 Autopilot Safety Authority
- DroneOS is an autonomous intelligence layer. It **does not replace certified autopilot safety checks**.
- Under no circumstances should Pixhawk's native pre-arm checks, sensor calibration validations, or hardware geofence limits be bypassed.
- If MAVSDK reports an arming or mode transition error, the error must be logged and handled upstream rather than overridden.

### 2.3 Deterministic Port Assignments
- Socket ports (`14550`-`14553` for DroneOS, `14650`-`14653` for Relay UDP inputs, `8081`-`8084` for WebSockets) must remain consistent across configuration files, startup scripts, and launch scripts to prevent silent communication failures.

---

## 3. Key Subsystems & Recent Fixes

### 3.1 Settle Gate & Multi-Shape Tracking (`core/coordination/manager.py`)
- **Problem:** When an operator cycled through shapes (e.g. `V` $\rightarrow$ `DIAMOND` $\rightarrow$ `COLUMN`), the slot assignments remained identical (`{0, 1, 2, 3}`), meaning `slot_assignments != self._last_slot_assignments` evaluated to false. This prevented `_last_slot_change_time` from resetting and left old memoized rejections intact.
- **Fix:** Settle gate now monitors `fp.get("type")` and `fp.get("spacing")` (`formation_changed`), resetting settle gates and clearing `_hold_logged` upon shape transition.

### 3.2 Time-Synchronized Pair Separation (`core/coordination/healing.py`)
- **Problem:** In single-file convoy shapes (`COLUMN`, `ECHELON`), follower death causes trailing drones to compact along the same line corridor ($s_1 = e_2$). A static segment-to-segment distance check evaluated to $0.00\text{ m}$ (because segments intersect at an endpoint), rejecting physically safe compactions.
- **Fix:** Implemented `_pair_separation()` in `healing.py`. It computes dynamic distance over normalized time for parallel/collinear movements, confirming drones remain at least `min_required_sep` apart during motion.

### 3.3 Hover Guard & Partial Delivery Check
- **Hover Guard:** Target-to-start clearance check is guarded with `is_hovering` (`math.hypot(s - e) < 0.5`), preventing advancing drones from being rejected when their predecessor is actively moving away.
- **Partial Delivery:** When `reshape_on_follower and is_follower_death`, the partial delivery check is bypassed because active follower reshape operates with directed unicast / resends.

---

## 4. How to Extend the Codebase

### 4.1 Adding a New Formation Shape
1. Open `core/formation_engine.py`.
2. Define the new shape offset calculation inside `_get_slot_offset(shape, slot, spacing, total_drones)`:
   ```python
   elif shape == "HEXAGON":
       # Compute Cartesian (x, y) relative to slot 0
       ...
   ```
3. Update `core/formation_manager.py` to add the new shape to `FormationType` enum.
4. Add the shape button to `mobile/src/views/SwarmView.jsx`.
5. Mirror the changes across `DroneOS1`, `DroneOS2`, and `DroneOS3`.
6. Add unit tests in `DroneOS/tests/test_formation_engine.py`.

### 4.2 Adding a New Message Type
1. Open `shared/protocol/messages.py`.
2. Add the enum value to `MessageType`.
3. Define the Pydantic schema extending `BaseMessage`.
4. Update `CommandHandler.handle_command` in `core/command_handler.py` to handle the new type.
5. Mirror across all four instances.

---

## 5. Outstanding Work & Future Roadmap

1. **RTK GPS / Visual Odometry Integration:** For tight formations ($< 5\text{ m}$ spacing), physical multi-path GPS error becomes significant. Integrating RTK GPS or optical flow sensors will enable millimeter-level formation precision.
2. **Dynamic Spline / Bezier Transitions:** Current formation transitions use linear velocity interpolation. Replacing these with polynomial minimum-snap trajectories will reduce aerodynamic drag and battery consumption during shape reshapes.
3. **Decentralized Multi-Hop Mesh:** Extending peer UDP communication to support ad-hoc mesh routing (e.g. BATMAN-adv) for swarms operating over long distances or around terrain obstructions.

