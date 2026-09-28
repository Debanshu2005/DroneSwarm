# AirSim Settings Guide

This directory contains the `settings.json` file needed to run the PhoneOS Swarm in simulation.

### What settings.json Does
The file configures Microsoft AirSim to spawn multiple multirotor vehicles. It defines the exact vehicle names (Drone1, Drone2, Drone3, Drone4), sets their type to SimpleFlight, defines their spawn coordinates to prevent them from crashing into each other on startup, and sets the API server port.

### Where to Put It
AirSim requires this file to be placed in a specific folder on your host machine:
*   **Windows**: `C:\Users\YourUsername\Documents\AirSim\settings.json`
*   **Linux**: `~/Documents/AirSim/settings.json`

If the folder does not exist, run AirSim once to create it, or create it manually. You must restart the AirSim environment whenever you change this file.

### Validating Your Settings
Before you launch the simulator, you should run the validator script to ensure the `settings.json` matches your codebase exactly.
Run the following from the root of the repo:
```bash
python sim/airsim/validate_settings.py
```
If the script prints all PASS and exits successfully, your settings are perfectly aligned with your `drone.yaml` and `flight.yaml` configs.

### What to Check on First Launch
When you open your AirSim environment (like Blocks or Neighborhood), check the on screen debug log that appears. You should see it successfully initialize Drone1, Drone2, Drone3, and Drone4. If any are missing, check the paths and try running the validator again.

### Coordinate Frame Note
Each vehicle spawns with its own local origin at its unique spawn point. The positions defined in `settings.json` are in NED metres relative to the map player start, and Z negative is up. 
This means that local NED commands are relative to that specific drone spawn location, while global lat/lon commands are converted dynamically based on each drone recorded home position.

### Simulator vs Hardware
Remember that simply updating `settings.json` has no effect on a real drone. Switching the DroneOS codebase between simulator mode and real hardware mode is handled by the `adapter_type` field in `configs/flight.yaml` (or by using the new `DRONEOS_PROFILE=sim` environment variable if your branch supports it).
