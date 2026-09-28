@echo off
setlocal
set DRONEOS_PROFILE=sim

start "DroneOS 1 (AirSim)" python start_drone1.py --ws-port 8080
start "DroneOS 2 (AirSim)" python start_drone2.py --ws-port 8081
start "DroneOS 3 (AirSim)" python start_drone3.py --ws-port 8082
start "DroneOS 4 (AirSim)" python start_drone4.py --ws-port 8083

echo Launched 4 sim nodes.
