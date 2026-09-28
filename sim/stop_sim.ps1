# Stop only Python processes occupying simulation-reserved relay or DroneOS UDP
# ports. This prevents stale relay children from a terminated previous launcher
# from sharing a UDP endpoint through SO_REUSEADDR.
$simulationPorts = 8081, 8082, 8083, 8084, 14550, 14551, 14552, 14553, 14650, 14651, 14652, 14653
$pidSet = [System.Collections.Generic.HashSet[int]]::new()

foreach ($line in (netstat -ano)) {
    if ($line -notmatch '^\s*(TCP|UDP)\s+') { continue }
    $columns = $line -split '\s+' | Where-Object { $_ }
    $localAddress = $columns[1]
    $pidValue = $columns[-1]
    foreach ($port in $simulationPorts) {
        if ($localAddress -match ":$port$") {
            [void]$pidSet.Add([int]$pidValue)
            break
        }
    }
}

foreach ($processId in $pidSet) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq 'python') {
        Write-Host "Stopping stale simulation Python process $processId on a reserved port."
        Stop-Process -Id $processId -Force
    }
}

Start-Sleep -Seconds 1
