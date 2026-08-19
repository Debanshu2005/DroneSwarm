export function evaluateDroneHealth(drone) {
    if (!drone || !drone.telemetry) return 'UNKNOWN';
    
    // Evaluate if we are LIVE, STALE or OFFLINE
    const now = Date.now();
    const lastSeen = drone.lastSeen || 0;
    const isStale = (now - lastSeen) > 2000;
    const isOffline = drone.status === 'OFFLINE' || (now - lastSeen) > 5000;
    
    if (isOffline) return 'UNKNOWN';

    let health = 'HEALTHY';
    const t = drone.telemetry;
    const d = drone.diagnostics;

    // Critical conditions
    if (t.battery_level < 15) health = 'CRITICAL';
    if (drone.status === 'failsafe') health = 'CRITICAL';
    if (t.system_health === 'ERROR') health = 'CRITICAL';
    
    // Warning conditions
    if (health !== 'CRITICAL') {
        if (t.battery_level >= 15 && t.battery_level < 30) health = 'WARNING';
        if (isStale) health = 'WARNING';
        if (t.system_health !== 'OK' && t.system_health !== 'ERROR') health = 'WARNING';
    }

    return health;
}

export function evaluatePreflightChecklist(drone) {
    if (!drone) return {};
    
    const now = Date.now();
    const lastSeen = drone.lastSeen || 0;
    const isOffline = drone.status === 'OFFLINE' || (now - lastSeen) > 5000;
    
    const t = drone.telemetry || {};
    
    return {
        CONNECTION: !isOffline ? 'PASS' : 'FAIL',
        HEARTBEAT: !isOffline ? 'PASS' : 'FAIL',
        TELEMETRY: (now - lastSeen <= 2000) ? 'PASS' : (isOffline ? 'FAIL' : 'WARNING'),
        BATTERY: (t.battery_level >= 20) ? 'PASS' : (t.battery_level ? 'FAIL' : 'UNKNOWN'),
        PX4_HEALTH: (t.system_health === 'OK') ? 'PASS' : (t.system_health ? 'FAIL' : 'UNKNOWN'),
        ESTIMATOR: (t.gps_valid || t.optical_flow_valid || t.rangefinder_valid) ? 'PASS' : 'FAIL',
        POSITIONING: (t.gps_valid || t.optical_flow_valid || t.rangefinder_valid) ? 'PASS' : 'FAIL',
        FAILSAFE: (drone.status !== 'failsafe') ? 'PASS' : 'FAIL',
        SENSOR_HEALTH: (t.sensor_health !== 'ERROR') ? 'PASS' : (t.sensor_health ? 'FAIL' : 'UNKNOWN'),
        FLIGHT_MODE: (t.flight_mode) ? 'PASS' : 'FAIL'
    };
}

export function evaluateTelemetryFreshness(drone) {
    if (!drone) return 'OFFLINE';
    const now = Date.now();
    const lastSeen = drone.lastSeen || 0;
    
    if (drone.status === 'OFFLINE' || (now - lastSeen) > 5000) return 'OFFLINE';
    if ((now - lastSeen) > 2000) return 'STALE';
    return 'LIVE';
}
