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
Deploy DroneOS as a background service on the Pi using the `deploy/install.sh` script to launch specifically numbered drones.

```bash
cd /home/pi/PhoneOS
./deploy/install.sh 1  # Installs phoneos-drone1.service
./deploy/install.sh 2  # Installs phoneos-drone2.service (if running multiple)
```
Logs can be viewed with: `journalctl -u phoneos-drone1.service -f`.

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

## 6. Command Library (full reference, both layers)

This is the canonical reference for operating the system. It has two distinct layers that are genuinely different things: NLP Terminal Commands (what you type) and Protocol-Level Commands (the underlying system messages).

### 9a — NLP Terminal Commands

These commands are typed in the `TerminalView` and parsed by the `trajectory_engine.py` into a `TaskAction`. 

| Keyword(s) | Parameters (Syntax) | Example | Description |
|---|---|---|---|
| `arm`, `disarm` | None | `arm` | Locks/unlocks motors. |
| `takeoff` | `h` (altitude), `hover_s` (duration) | `takeoff h=5 hover_s=2` | Arms (if needed), climbs to altitude, hovers. |
| `land` | None | `land` | Descends immediately. Auto-disarms after landing. |
| `rtl`, `return` | None | `rtl` | Standard return to launch. Climbs to safe altitude, flies home, lands. |
| `srtl`, `smart rtl` | None | `srtl` | Same-Altitude Return-to-Launch. Holds current altitude, flies directly home, lands. **Safety Note:** This bypasses the standard RTL climb sequence and has no obstacle-clearance margin. The operator is responsible for confirming the direct path home is clear. |
| `hold`, `loiter` | None | `hold position` | Halts movement and holds current position. |
| `hover` | `hover_s` or `seconds` | `hover for 5s` | Halts movement and holds position for a duration. |
| `mode`, `switch mode` | `[mode_name]` | `switch mode to guided` | Changes PX4 flight mode. |
| `goto`, `move` | `x` (north), `y` (east), `h` (alt) | `goto north=10 east=5 h=3` | Navigates to a specific local or global offset. |
| `forward`, `backward`, `left`, `right`, `up`, `down` | None | `move forward` | Continuous directional nudge for 2 seconds. |
| `circle` | `r` (radius), `h` (alt), `n` (segments) | `circle r=5 h=3 n=36` | Flies a circular path around the current location. (Solo flight path) |
| `square` | `size`, `h` (alt), `passes` | `square size=10 h=3` | Flies a square search pattern. (Solo flight path) |
| `triangle` | `size`, `h` (alt) | `triangle size=6 h=3` | Flies a triangular path. (Solo flight path) |
| `spiral` | `size`, `h` (alt), `turns` | `spiral size=10 h=3 turns=3` | Flies a spiral outward. (Solo flight path) |
| `figure-8` | `size`, `h` (alt) | `figure-8 size=5 h=3` | Flies a figure-8 path. (Solo flight path) |
| `grid` | `size`, `h` (alt), `passes` | `grid size=10 h=3 passes=4` | Flies a grid search pattern. (Solo flight path) |

*Note: The solo flight-path shapes (`circle`, `square`, etc.) dictate a trajectory for individual drones. They are unrelated to Swarm Formations (which happen to share some shape names but dictate multi-drone topology).*

**Targeting Behavior (ALL vs Single Drone):**
In `TerminalView.jsx`, the TARGET dropdown determines if a command applies to a single drone or `ALL`. When `ALL` is selected, the Ground Station iterates through every connected drone and sends a discrete `TerminalCommandMessage` to each drone individually. Each drone then parses and executes the command independently through its own trajectory engine.

### 9c — How the two layers relate

The terminal parses natural-language text into a `TaskAction`, which `terminal_controller.py` then translates into one or more `ControlMessage`s carrying a `CommandAction`. Thus, every terminal command ultimately becomes a protocol-level command, but not every protocol-level command has a terminal keyword (for instance, `FORMATION_UPDATE` is only reachable via the UI in `SwarmView.jsx`). 

*End-to-End Example:* Typing `srtl` in the terminal parses into `TaskAction.SRTL`. The `terminal_controller.py` receives this and dispatches a `ControlMessage(action=CommandAction.SRTL)`. The `command_handler.py` receives this protocol message, validates the safety gates, and finally invokes `flight_manager.smart_rtl()`.

### 9b — Protocol-Level Command Library

This is the underlying messaging layer (`CommandAction` enum in `messages.py`). Each action is handled by a registered method in `command_handler.py`.

| CommandAction | Handler (`main.py`) | Params Shape | UI Surface | Description & Validation Gates |
|---|---|---|---|---|
| `ARM` | `flight_manager.arm` | `{}` | `DroneControlView` (HUD) | Unlocks motors. **Gates**: Heartbeat fresh, Telemetry fresh, No emergency. **Peer Gate**: Validates `REQUIRE_PEERS_BEFORE_ARM` peer count/presence. |
| `DISARM` | `flight_manager.disarm` | `{}` | `DroneControlView` (HUD) | Locks motors. |
| `TAKEOFF` | `flight_manager.takeoff` | `{altitude_m: float}` | `DroneControlView`, NLP Terminal | Climbs to altitude. **Gates**: Heartbeat fresh, Telemetry fresh, No emergency. |
| `LAND` | `flight_manager.land` | `{}` | `DroneControlView`, NLP Terminal | Descends immediately. |
| `RTL` | `flight_manager.rtl` | `{}` | `DroneControlView`, NLP Terminal | Returns to launch with standard climb. **Gates**: Heartbeat fresh, No emergency, `home_valid`. (If in manual mode, requires `gps_valid`). |
| `SRTL` | `flight_manager.smart_rtl` | `{}` | NLP Terminal only | Returns to launch without climbing. **Gates**: Checks `min_srtl_altitude_m` directly in handler. |
| `HOVER` | `flight_manager.hover` | `{}` | `DroneControlView` (HOLD), NLP | Halts movement and holds position. |
| `STOP` | `flight_manager.stop` | `{}` | `DroneControlView` (E-RESET) | Halts movement. **Gates**: Special behavior—resets active failsafe/emergency status. |
| `MOVE` | `flight_manager.move` | `{vx, vy, vz, yaw_rate}` | `DroneControlView` (D-Pad), NLP | Continuous velocity control. **Gates**: Heartbeat fresh, No emergency. |
| `FORMATION_UPDATE` | `flight_manager.formation_update` | `{type: str, spacing: float}` | `SwarmView` | Assigns offset for swarm formation. |
| `SET_MODE` | `flight_manager.set_mode` | `{mode: str}` | NLP Terminal only | Changes PX4 flight mode. |
| `GOTO` | `flight_manager.goto` | `{lat, lon, alt}` | NLP Terminal only | Navigates to a global coordinate. **Gates**: Heartbeat fresh, No emergency, `gps_valid`. |
| `GOTO_LOCAL` | `flight_manager.goto_local` | `{north, east, down}` | NLP Terminal only | Navigates to a local NED coordinate. **Gates**: Heartbeat fresh, No emergency, `local_pos_valid`. |

*Note: The protocol contains additional messages like `EMERGENCY` (triggers E-STOP, zeroes velocity, and locks system) and `PARAM_REQUEST`, which are separate `MessageType`s, not `CommandAction`s.*

---

## 7. Safety & Failsafe Features

DroneOS utilizes an decoupled `SafetyModule` to handle critical faults.

1. **Emergency Stop (E-STOP / EMERGENCY)**: Sending an E-STOP command immediately zeroes the drone's velocity (`0.0 m/s`), sends a kill command to the Flight Controller, and aborts any active mission. It locks the system state until a physical reset or E-RESET is triggered.
2. **Heartbeat Timeout**: If Ground Station connection is lost (configurable timeout, default `5.0s`), the failsafe triggers. In outdoor mode, it initiates an RTL. In indoor mode, it initiates an immediate LAND.
3. **Battery Failsafes**: 
   - *Low Battery* (default 20%): Triggers RTL (outdoor) or LAND (indoor).
   - *Critical Battery* (default 10%): Triggers immediate LAND regardless of mode.
4. **GPS Degradation**: If GPS signal drops, the drone defaults to holding position (`hover`).
