# PhoneOS Swarm - User Guide

Welcome to the PhoneOS Swarm operation guide. This guide covers the complete lifecycle of operating the drone swarm, from hardware setup and networking, to manual flight control, swarm formation, and natural language command execution. 

All documentation herein is strictly verified against the current system implementation.

---

## 1. System Overview

The system consists of a Ground Station (mobile/web UI) and multiple Drone Nodes. Each drone runs:
1. **DroneOS**: The main python application managing flight state, swarm behavior, safety, and MAVSDK connection to the PX4 flight controller.
2. **Relay Server**: A network bridge (`relay.py`) converting UDP telemetry (port `14550`) to WebSockets (port `8080`) for Ground Station communication.

**Communication Path:**  
`Mobile App (WebSocket: 8080)` <--> `Relay` <--> `DroneOS (UDP: 14550)` <--> `MAVSDK` <--> `PX4 Flight Controller (/dev/serial0)`

---

## 2. Prerequisites & Setup

### Drone Hardware Setup
The system is designed for Raspberry Pi companion computers running alongside PX4 flight controllers.
1. Connect your PX4 flight controller to the Raspberry Pi's serial port.
2. Ensure the code is placed in the expected directory on the Pi: `/home/pi/PhoneOS/`

### Installation & Autostart
There are two ways to deploy DroneOS as a background service on the Pi:

**Option A: Standard Swarm Node (Recommended)**
Use the `setup_pi.sh` script to configure a single `droneos.service`.
```bash
cd /home/pi/PhoneOS
sudo ./setup_pi.sh
```
This automatically registers and starts `droneos.service`, launching `DroneOS/main.py --config DroneOS/config.json`. Logs can be viewed with: `sudo journalctl -fu droneos.service`.

**Option B: Multi-Instance Deployment (Simulation/Advanced)**
For deploying multiple simulated drones on a single machine, use the `deploy/install.sh` script to launch specifically numbered drones.
```bash
./deploy/install.sh 1  # Installs phoneos-drone1.service
./deploy/install.sh 2  # Installs phoneos-drone2.service
```

### Configuration Files
- **`DroneOS/configs/flight.yaml`**: Contains flight controller connection info. The default PX4 connection string is `serial:///dev/serial0:115200`. The default takeoff altitude is `10.0m` and max velocity is `5.0m/s`.
- **`DroneOS/configs/safety.yaml`**: Configures battery triggers (`low_battery_threshold: 20.0`, `critical_battery_threshold: 10.0`) and the heartbeat timeout (`5.0` seconds).

---

## 3. Ground Station Application

The mobile web UI connects to the swarm via WebSockets.

### Connecting to Drones
1. Open the Ground Station app.
2. Navigate to **Settings (Professional Settings)**.
3. Under **MULTI-DRONE CONNECTIONS**, input the IP address of your drone(s) and port `8080` (default relay port).
4. Click **+ ADD DRONE** and then **CONNECT ALL**. 

*Note: The Ground Station stores its default WebSocket URL in local storage (`ws://swarmos-pi.local:8080`).*

### Targeting
In the HUD top bar, you can choose a **TARGET**.
- `ALL DRONES`: Commands are broadcast to the entire connected swarm.
- `SINGLE DRONE (e.g., drone1)`: Commands are isolated to the selected drone.

### Flight Modes
The UI provides an **INDOOR TEST** mode and an **OUTDOOR** mode.
- **OUTDOOR**: Enforces strict PX4 Preflight checks before ARMing is allowed. The checks require MAVSDK readiness, GPS FIX, Home Position validation, Estimator readiness, and Battery > 15%.
- **INDOOR TEST**: Disables the UI-level GPS lock enforcement. *Note: PX4's internal hardware-level safety checks remain active.*

---

## 4. Manual Flight Control

When in OUTDOOR mode and passing Preflight, the HUD unlocks bottom-bar commands:
- **ARM / DISARM**: Locks/unlocks the motors. 
- **TAKEOFF**: Climbs to the target altitude specified in the UI's ALT incrementer. (Default 1.0m, overrides flight.yaml default).
- **LAND**: Commands immediate vertical descent.
- **RTL (Return to Launch)**: Climbs to a safe return altitude (defined in PX4), flies back to the home coordinate, and lands.
- **HOLD**: Halts current movement and maintains position.

### D-Pad Movement (MOVE)
The UI provides an active D-Pad for manual control (Forward, Backward, Left, Right, Up, Down, Yaw).
Movement commands are streamed to the drone continually while the button is held.
**Safety Deadman Switch**: If the D-Pad is released, or network drops, DroneOS will automatically halt movement and enter HOVER if it doesn't receive a fresh command within `0.5` seconds. Max bounds: Velocity XY (5m/s), Z (3m/s), Yaw (90deg/s).

---

## 5. Swarm Formations

The swarm can autonomously assemble into geometric shapes relative to an anchor drone.

**Supported Shapes:** V, COLUMN, LINE, SQUARE, GRID, CIRCLE, DIAMOND, WEDGE, ECHELON_LEFT, ECHELON_RIGHT.

### Operation
1. Navigate to the **Swarm Topology** View or use the **FORM** panel in the HUD.
2. Select a Formation shape and spacing (e.g., 5 meters).
3. Ensure all targeted drones are online and ARMED.
4. Apply the formation. The drones will compute their required spatial offsets relative to the swarm anchor (Drone 0) and fly to their positions.

### `REQUIRE_PEERS_BEFORE_ARM`
For strict swarm choreography, set the environment variable `REQUIRE_PEERS_BEFORE_ARM=true` and specify `EXPECTED_PEER_IDS` or `EXPECTED_PEER_COUNT`. If this is active, DroneOS will reject any ARM command if the required peers are not connected and actively sending heartbeats within the timeout window.

---

## 6. Natural Language Terminal

The Ground Station includes an **NLP TERMINAL** view that parses natural language and compact text commands into complex flight trajectories.

Commands are processed by the Trajectory Engine. Example syntax:
- `take off to 3 meters, hover for two seconds, and land`
- `takeoff h=3 hover_s=2`
- `fly a figure eight size 5 at 3 meters`
- `go 10 meters north and 5 meters east at 3 meters altitude`
- `move 5 meters forward and 2 meters up`
- `hold position`
- `circle r=5 h=3 n=36`
- `grid size=10 h=3 passes=4`

**New Feature: Smart Return to Launch (SRTL)**
- You can command `srtl` or `smart rtl`.
- **Behavior:** `SRTL` commands the drone to hold its current altitude, fly directly home laterally, and then land. It *bypasses* the standard PX4 RTL climb sequence to save battery/time.
- **Safety Restriction:** `SRTL` will be rejected if the drone's current altitude is below `2.0m` (the minimum safe altitude). The operator is fully responsible for verifying the direct path home is clear of obstacles.

---

## 7. Safety & Failsafe Features

DroneOS utilizes an decoupled `SafetyModule` to handle critical faults.

1. **Emergency Stop (E-STOP / EMERGENCY)**: Sending an E-STOP command immediately zeroes the drone's velocity (`0.0 m/s`), sends a kill command to the Flight Controller, and aborts any active mission. It locks the system state until a physical reset or E-RESET is triggered.
2. **Heartbeat Timeout**: If Ground Station connection is lost (configurable timeout, default `5.0s`), the failsafe triggers. In outdoor mode, it initiates an RTL. In indoor mode, it initiates an immediate LAND.
3. **Battery Failsafes**: 
   - *Low Battery* (default 20%): Triggers RTL (outdoor) or LAND (indoor).
   - *Critical Battery* (default 10%): Triggers immediate LAND regardless of mode.
4. **GPS Degradation**: If GPS signal drops, the drone defaults to holding position (`hover`).
