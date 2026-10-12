@echo off
setlocal
echo ======================================================================
echo Killing Drone 4 (disarming in AirSim and stopping DroneOS 4 node)...
echo ======================================================================

rem 1. Disarm in AirSim (cuts motors)
python -c "import airsim; c = airsim.MultirotorClient(); c.confirmConnection(); c.armDisarm(False, 'Drone4')" 2>nul

rem 2. Kill Python processes on Drone 4 ports (WS 8084, UDP 14553, Relay 14653)
powershell -NoProfile -Command "$ports = 8084, 14553, 14653; foreach ($line in (netstat -ano)) { if ($line -notmatch '^\s*(TCP|UDP)\s+') { continue }; $cols = $line -split '\s+' | Where-Object { $_ }; foreach ($p in $ports) { if ($cols[1] -match \":$p$\") { $proc = Get-Process -Id ([int]$cols[-1]) -ErrorAction SilentlyContinue; if ($proc -and $proc.ProcessName -eq 'python') { Stop-Process -Id $proc.Id -Force; Write-Host ('Stopped Drone 4 process PID ' + $proc.Id) } } } }"

echo.
echo Drone 4 has been killed.
echo Watch the remaining 3 drones (Drone 1, Drone 2, Drone 3) detect the failure and self-heal!
echo ======================================================================
