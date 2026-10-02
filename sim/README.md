# AirSim Simulation Setup for DroneSwarm

This directory contains everything you need to run the PhoneOS Swarm in Microsoft AirSim.

### 1. AirSim Environment Setup
The file `airsim/settings.json` configures Microsoft AirSim to spawn four multirotor vehicles (Drone1 to Drone4) in a line, 5 metres apart, to prevent them from crashing into each other on startup.

AirSim requires this file to be placed in a specific folder on your host machine:
*   **Windows**: `C:\Users\YourUsername\Documents\AirSim\settings.json`
*   **Linux**: `~/Documents/AirSim/settings.json`

If the folder does not exist, run AirSim once to create it, or create it manually. You must restart the AirSim environment whenever you change this file.

To launch the AirSim environment, open your packaged Unreal Engine environment (like Blocks or Neighborhood) executable. You should see the on screen debug log initialize Drone1, Drone2, Drone3, and Drone4.

### 2. Python Dependencies
The AirSim Python client is required to run the simulation backend. Install it on your system with:
```bash
pip install "msgpack-rpc-python" "airsim"
```

### 3. Launching the Swarm in Simulation
We have provided launch scripts (`launch_sim.bat` for Windows and `launch_sim.sh` for Linux) that automatically set the required environment variables and launch all four drones concurrently.

These scripts set `DRONEOS_PROFILE=sim`, which instructs the DroneOS codebase to load the simulator backend without requiring any changes to your committed `flight.yaml` files. They also pass distinct WebSocket ports to each relay to avoid TCP port conflicts on a single PC.

The simulation-only `network.sim.yaml` overlays provide deterministic loopback routing:

| Drone | DroneOS UDP | Relay UDP input | WebSocket |
| --- | --- | --- | --- |
| Drone1 | `127.0.0.1:14550` | `127.0.0.1:14650` | `ws://<Windows-LAN-IP>:8081` |
| Drone2 | `127.0.0.1:14551` | `127.0.0.1:14651` | `ws://<Windows-LAN-IP>:8082` |
| Drone3 | `127.0.0.1:14552` | `127.0.0.1:14652` | `ws://<Windows-LAN-IP>:8083` |
| Drone4 | `127.0.0.1:14553` | `127.0.0.1:14653` | `ws://<Windows-LAN-IP>:8084` |

Each node sends its heartbeat/telemetry to its three peers and its own relay. In simulation, every relay is also given deterministic routes to all four DroneOS endpoints, so commands work even if the GCS is connected to only one relay. AirSim remains at `127.0.0.1:41451`.

`launch_sim.bat` clears old simulation processes before starting the swarm. Use it rather than launching individual starter scripts after an interrupted run; an old relay or DroneOS process can otherwise keep a UDP port and make telemetry appear healthy while commands are delivered to the stale process.

Run the launch script from the root of the repository:
*   **Windows**: `sim\launch_sim.bat`
*   **Linux**: `bash sim/launch_sim.sh`

### 4. Returning to Hardware
To return to running on real hardware (the PX4 default), simply stop the simulator scripts and ensure the `DRONEOS_PROFILE` environment variable is unset in your terminal. You can run `start_drone*.py` directly as usual. The codebase will automatically revert to reading the default PX4 configurations.

### Validating Your Settings
Before you launch the simulator for the first time, you should run the validator script to ensure the `settings.json` matches your codebase exactly.
```bash
python sim/airsim/validate_settings.py
```

### Coordinate Frame Note
Each vehicle spawns with its own local origin at its unique spawn point. The positions defined in `settings.json` are in NED metres relative to the map player start, and Z negative is up. 
This means that local NED commands are relative to that specific drone spawn location, while global lat/lon commands are converted dynamically based on each drone recorded home position.
