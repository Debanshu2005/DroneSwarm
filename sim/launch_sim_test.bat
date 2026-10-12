@echo off
setlocal
set DRONEOS_PROFILE=test

rem Stop stale processes first (reuses the same stop script)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_sim.ps1"

rem Launch all 4 drones with the test profile (coordination active + armed)
start "DroneOS 1 (AirSim TEST)" python start_drone1.py
ping 127.0.0.1 -n 2 > nul
start "DroneOS 2 (AirSim TEST)" python start_drone2.py
ping 127.0.0.1 -n 2 > nul
start "DroneOS 3 (AirSim TEST)" python start_drone3.py
ping 127.0.0.1 -n 2 > nul
start "DroneOS 4 (AirSim TEST)" python start_drone4.py

echo.
echo ======================================================================
echo Launched 4 sim nodes in TEST profile (coordination active + armed).
echo Waiting 5s for relays and nodes to initialize...
echo ======================================================================
ping 127.0.0.1 -n 6 > nul

if "%~1"=="--nodes-only" goto NODES_ONLY

if "%AIRSIM_HOST%"=="" set AIRSIM_HOST=127.0.0.1

echo.
echo ======================================================================
echo Starting AirSim Follower-Loss Healing Test (single-command run)...
echo ======================================================================
python -m pytest DroneOS/tests/test_heal_airsim.py -m integration -v -s %*
goto END

:NODES_ONLY
echo Nodes are running. Skipping automated test because --nodes-only was specified.

:END
