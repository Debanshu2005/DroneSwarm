@echo off
setlocal

set DRONE_NUM=%1
if "%DRONE_NUM%"=="" (
    echo Usage: sim\kill_drone.bat ^<1^|2^|3^|4^>
    echo Example: sim\kill_drone.bat 3    (kills Drone 3)
    exit /b 1
)

if "%DRONE_NUM%"=="1" (
    set WS_PORT=8081
    set UDP_PORT=14550
    set RELAY_PORT=14650
    set VEHICLE_NAME=Drone1
) else if "%DRONE_NUM%"=="2" (
    set WS_PORT=8082
    set UDP_PORT=14551
    set RELAY_PORT=14651
    set VEHICLE_NAME=Drone2
) else if "%DRONE_NUM%"=="3" (
    set WS_PORT=8083
    set UDP_PORT=14552
    set RELAY_PORT=14652
    set VEHICLE_NAME=Drone3
) else if "%DRONE_NUM%"=="4" (
    set WS_PORT=8084
    set UDP_PORT=14553
    set RELAY_PORT=14653
    set VEHICLE_NAME=Drone4
) else (
    echo Invalid drone number: %DRONE_NUM%. Must be 1, 2, 3, or 4.
    exit /b 1
)

echo ======================================================================
echo Killing %VEHICLE_NAME% (disarming in AirSim and stopping DroneOS %DRONE_NUM% node)...
echo ======================================================================

rem 1. Disarm in AirSim (cuts motors)
python -c "import airsim; c = airsim.MultirotorClient(); c.confirmConnection(); c.armDisarm(False, '%VEHICLE_NAME%')" 2>nul

rem 2. Kill Python processes on that drone's ports (WS, UDP, Relay)
powershell -NoProfile -Command "$ports = %WS_PORT%, %UDP_PORT%, %RELAY_PORT%; foreach ($line in (netstat -ano)) { if ($line -notmatch '^\s*(TCP|UDP)\s+') { continue }; $cols = $line -split '\s+' | Where-Object { $_ }; foreach ($p in $ports) { if ($cols[1] -match \":$p$\") { $proc = Get-Process -Id ([int]$cols[-1]) -ErrorAction SilentlyContinue; if ($proc -and $proc.ProcessName -eq 'python') { Stop-Process -Id $proc.Id -Force; Write-Host ('Stopped ' + '%VEHICLE_NAME%' + ' process PID ' + $proc.Id) } } } }"

echo.
echo %VEHICLE_NAME% has been killed!
if "%DRONE_NUM%"=="1" (
    echo [ANCHOR LOSS] Drone 1 was the anchor. Watch the swarm elect a new anchor and re-form!
) else (
    echo [FOLLOWER LOSS] %VEHICLE_NAME% was a follower. Watch the anchor reshape the formation!
)
echo ======================================================================
