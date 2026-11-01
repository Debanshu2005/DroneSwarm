# Guide: AirSim Simulation Setup & Execution

## 1. Overview

The Microsoft AirSim simulation environment enables full Software-In-The-Loop (SITL) testing of the complete multi-drone swarm in high-fidelity 3D physics using Unreal Engine. You can test autonomous takeoff, multi-drone formation flight, dynamic obstacle avoidance, and mid-air self-healing without risking physical hardware.

---

## 2. AirSim Environment Setup

### 2.1 Installing the Environment Binary
1. Download a pre-compiled AirSim Unreal Engine environment (such as `Blocks.zip` or `Neighborhood.zip`) from the official [AirSim GitHub Releases](https://github.com/microsoft/AirSim/releases).
2. Extract the archive to a convenient directory (e.g. `C:\AirSimEnvironments\Blocks\`).

### 2.2 Installing `settings.json`
AirSim requires a configuration file to define vehicle names, vehicle types, spawn coordinates, and network endpoints.

This repository includes a tested multi-drone configuration at `sim/airsim/settings.json`. Copy this file to your user documents directory:
- **Windows:** `C:\Users\<YourUsername>\Documents\AirSim\settings.json`
- **Linux:** `~/Documents/AirSim/settings.json`

> [!IMPORTANT]
> In `settings.json`, four multirotor vehicles (`Drone1`, `Drone2`, `Drone3`, `Drone4`) are spawned in a line 5.0 meters apart along the Y-axis. This guarantees that aircraft do not physically collide upon spawning.

### 2.3 Validating the Settings File
Run the validation script to confirm that your `settings.json` matches repository expectations:

```bash
python sim/airsim/validate_settings.py
```

Expected output:
```text
[OK] settings.json found and verified.
[OK] 4 vehicles defined: Drone1, Drone2, Drone3, Drone4.
[OK] AirSim RPC endpoint: 127.0.0.1:41451.
```

---

## 3. Python Simulation Dependencies

Install the AirSim Python client:

```bash
pip install msgpack-rpc-python airsim
```

---

## 4. Launching the Swarm in Simulation

### 4.1 Step 1: Start the Unreal Engine Environment
1. Launch your Unreal executable (e.g. `Blocks.exe`).
2. When prompted:
   - Select **"No"** to using the car simulation (choose Multirotor).
3. Confirm that the simulator displays four quadcopters on screen (`Drone1`, `Drone2`, `Drone3`, `Drone4`).

### 4.2 Step 2: Run the Launch Script
Run the automated multi-node launch script from the root of the repository:

- **On Windows:**
  ```cmd
  sim\launch_sim.bat
  ```
- **On Linux:**
  ```bash
  bash sim/launch_sim.sh
  ```

### What `launch_sim.bat` Does Automatically:
1. **Zombie Cleanup:** Scans for and forcefully terminates any stale `relay.py` or `start_drone*.py` processes that may still be holding ports `14550`-`14553` or `8081`-`8084`.
2. **Sets Environment Variables:** Sets `DRONEOS_PROFILE=test`, activating active coordination with follower-loss reshaping enabled.
3. **Spawns All 4 Drones & Relays:**
   - Drone 1 (`DroneOS1`): UDP `14550`, Relay WS `8081`.
   - Drone 2 (`DroneOS2`): UDP `14551`, Relay WS `8082`.
   - Drone 3 (`DroneOS3`): UDP `14552`, Relay WS `8083`.
   - Drone 4 (`DroneOS`):  UDP `14553`, Relay WS `8084`.

---

## 5. Controlling the Simulated Swarm

### 5.1 Using the Mobile GCS
1. Open your browser to `http://localhost:5173` (with `cd mobile && npm run dev` running).
2. Go to **Settings > Multi-Drone Connections**.
3. Connect to the four simulated WebSocket endpoints:
   - Drone 1: `ws://127.0.0.1:8081`
   - Drone 2: `ws://127.0.0.1:8082`
   - Drone 3: `ws://127.0.0.1:8083`
   - Drone 4: `ws://127.0.0.1:8084`
4. Navigate to **Swarm View** to arm the swarm, take off, and select formations.

### 5.2 Using the Formation Script
Alternatively, assemble a formation directly from the CLI:

```cmd
sim\start_formation.bat
```
or:
```bash
python sim/start_formation.py --shape V --spacing 15.0
```

Supported shapes: `V`, `DIAMOND`, `COLUMN`, `LINE`, `CIRCLE`, `SQUARE`, `GRID`, `ECHELON_LEFT`, `ECHELON_RIGHT`.

---

## 6. Testing Self-Healing & Drone Failures

To simulate an in-flight motor or power failure on a specific drone, use the kill scripts:

```cmd
# Kill Drone 3 (follower drone)
sim\kill_drone.bat 3

# Or kill Drone 4
sim\kill_drone4.bat
```

### What to Observe:
1. The killed drone process terminates immediately.
2. In the terminal logs of surviving drones:
   - After 2 missed heartbeats: `[coord] Peer drone3 transition ALIVE -> SUSPECT`.
   - After 4 missed heartbeats: `[coord] Peer drone3 transition SUSPECT -> DEAD`.
   - The anchor drone evaluates plans: `[coord] Elected plan: compaction`.
   - A `FORMATION_UPDATE` message is broadcast.
3. In AirSim, the remaining 3 drones smoothly maneuver to close the gap and reconstitute the formation without colliding.

---

## 7. Clean Shutdown

To stop all background simulation processes cleanly and release all network sockets:

```powershell
powershell -ExecutionPolicy Bypass -File sim/stop_sim.ps1
```
This guarantees that no zombie process locks ports for the next run.

