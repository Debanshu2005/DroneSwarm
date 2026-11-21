# DroneOS Swarm Launch Recipe (1–4 Drones)

This guide documents the unified operational launch recipe for PhoneOS/DroneOS across 1 to 4 drones in both simulation and physical Raspberry Pi hardware deployments.

---

## 1. Drone Instance Architecture & Mapping

Each drone runs an isolated, dedicated instance package with its own configurations, network ports, and telemetry bindings:

| Drone ID | Vehicle Name | Package Root | Start Script | Systemd Unit | Relay WS Port | UDP Listen Port |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `drone1` | `Drone1` | `DroneOS` | `start_drone1.py` | `phoneos-drone1.service` | `8081` | `14550` |
| `drone2` | `Drone2` | `DroneOS1` | `start_drone2.py` | `phoneos-drone2.service` | `8082` | `14551` |
| `drone3` | `Drone3` | `DroneOS2` | `start_drone3.py` | `phoneos-drone3.service` | `8083` | `14552` |
| `drone4` | `Drone4` | `DroneOS3` | `start_drone4.py` | `phoneos-drone4.service` | `8084` | `14553` |

> [!IMPORTANT]
> `DroneOS/` is the single source of truth for code and perception configs. `DroneOS1`, `DroneOS2`, and `DroneOS3` are maintained in exact sync via `python scripts/sync_perception.py`.

---

## 2. ML Perception Flags & Environment Overrides

Visual ML garbage detection is **enabled by default** in `perception.yaml` across all four drones.

- **Default Launch** (perception active):
  ```bash
  python start_drone1.py
  ```
- **Opt-Out via Command-Line Flag**:
  ```bash
  python start_drone1.py --no-perception
  ```
- **Explicit Opt-In via Flag**:
  ```bash
  python start_drone1.py --perception
  ```
- **Environment Variable Override**:
  ```bash
  DRONEOS_PERCEPTION=0 python start_drone1.py  # Disable perception
  DRONEOS_PERCEPTION=1 python start_drone1.py  # Enable perception
  ```

---

## 3. Simulation Launch (AirSim / SITL)

### 3.1 Launching a Single Drone (Drone 1)
To run Drone 1 against AirSim or MAVSDK SITL:
```bash
# Set simulation profile
export DRONEOS_PROFILE=sim

# Start Drone 1 (spawns relay on ws://127.0.0.1:8081 and binds UDP 14550)
python start_drone1.py
```

### 3.2 Launching the Full 4-Drone Swarm
Open four separate terminal sessions (or launch via background processes):
```bash
# Terminal 1: Drone 1
DRONEOS_PROFILE=sim python start_drone1.py

# Terminal 2: Drone 2
DRONEOS_PROFILE=sim python start_drone2.py

# Terminal 3: Drone 3
DRONEOS_PROFILE=sim python start_drone3.py

# Terminal 4: Drone 4
DRONEOS_PROFILE=sim python start_drone4.py
```

Startup is staggered automatically by `drone_idx * 0.25s` to prevent simultaneous inference spikes during initial YOLO model load.

---

## 4. Physical Raspberry Pi Deployment (systemd)

On each companion Raspberry Pi:
1. Clone the repository into `/home/pi/DroneSwarm`.
2. Run the deployment script with the drone index:
   ```bash
   # On Drone 1 Raspberry Pi:
   ./deploy/install.sh 1

   # On Drone 2 Raspberry Pi:
   ./deploy/install.sh 2

   # On Drone 3 Raspberry Pi:
   ./deploy/install.sh 3

   # On Drone 4 Raspberry Pi:
   ./deploy/install.sh 4
   ```
3. Verify live service status and journal logs:
   ```bash
   sudo systemctl status phoneos-drone1.service
   sudo journalctl -u phoneos-drone1.service -f
   ```

---

## 5. Perception Diagnostics & Visible Failure Contract

When perception is enabled:
- Successful startup logs:
  `INFO - Perception pipeline initialized on drone1 (Drone1).`
- Diagnostics reporting:
  Every drone periodically broadcasts `DiagnosticsMessage` and `TelemetryMessage` reporting `perception_status`:
  - `status: "OK"` when the YOLO detector and frame source are fully operational.
  - `status: "DEGRADED"` with exact actionable reason if dependencies or model weights are missing or if camera acquisition fails.
  - `status: "DISABLED"` if perception was disabled via `--no-perception` or config.
- The Mobile App / GCS aggregates these statuses into a swarm-level indicator: **"ML: 4/4 OK"**.
