@echo off
setlocal
set DRONEOS_PROFILE=sim

rem Each starter selects its simulation-only relay/network endpoint.
start "DroneOS 1 (AirSim)" python start_drone1.py
start "DroneOS 2 (AirSim)" python start_drone2.py
start "DroneOS 3 (AirSim)" python start_drone3.py
start "DroneOS 4 (AirSim)" python start_drone4.py

echo Launched 4 sim nodes.
