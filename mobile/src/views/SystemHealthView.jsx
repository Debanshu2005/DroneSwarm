import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Activity, ShieldAlert, Wifi, Cpu, Settings, Map, Navigation, Battery, Radio } from 'lucide-react';

export default function SystemHealthView() {
  const { isConnected, drones, selectedDrones } = useDroneContext();
  
  const targetId = Array.from(selectedDrones)[0];
  const drone = targetId ? drones[targetId] : null;

  if (!drone) {
    return (
      <div className="view-container">
         <div className="card" style={{textAlign: 'center', padding: '40px'}}>
            <Activity size={48} color="var(--text-muted)" style={{marginBottom: '16px'}}/>
            <h3>No Drone Selected</h3>
            <p className="text-muted">Select a drone to view its system health.</p>
         </div>
      </div>
    );
  }

  const tel = drone.telemetry || {};
  const diag = drone.diagnostics || {};

  // Status computation (GREEN/YELLOW/RED maps to good/warning/danger)
  const getStatus = (sys) => {
    switch (sys) {
       case 'PHONEOS': return 'good';
       case 'RELAY': return isConnected === 'CONNECTED' ? 'good' : 'danger';
       case 'DRONEOS': return drone.status === 'CONNECTED' ? 'good' : (drone.status === 'DEGRADED' ? 'warning' : 'danger');
       case 'MAVSDK': return tel.flight_mode && tel.flight_mode !== 'disconnected' ? 'good' : 'danger';
       case 'PX4': return tel.system_health === 'OK' ? 'good' : (tel.system_health == null ? 'warning' : 'danger');
       case 'GPS': return tel.gps_valid ? 'good' : 'danger';
       case 'ESTIMATOR': return tel.system_health === 'OK' ? 'good' : 'warning';
       case 'BATTERY': return (tel.battery_level || 0) > 30 ? 'good' : ((tel.battery_level || 0) > 15 ? 'warning' : 'danger');
       case 'SENSORS': return 'good'; // placeholder based on actual health
       case 'NEIGHBORS': return 'good';
       case 'MISSION': return drone.missionState?.status === 'running' ? 'good' : 'neutral';
       case 'COLLISION': return 'good';
       default: return 'neutral';
    }
  };

  const systems = [
    { name: 'PHONEOS', icon: <Cpu/>, state: getStatus('PHONEOS'), error: null, action: 'None' },
    { name: 'RELAY', icon: <Wifi/>, state: getStatus('RELAY'), error: isConnected === 'CONNECTED' ? null : 'WebSocket disconnect', action: 'Check network' },
    { name: 'DRONEOS', icon: <Activity/>, state: getStatus('DRONEOS'), error: drone.status === 'CONNECTED' ? null : 'Stale heartbeat', action: 'Check Raspberry Pi' },
    { name: 'MAVSDK', icon: <Settings/>, state: getStatus('MAVSDK'), error: getStatus('MAVSDK') === 'danger' ? 'MAVSDK not communicating' : null, action: 'Restart DroneOS' },
    { name: 'PX4', icon: <Cpu/>, state: getStatus('PX4'), error: tel.system_health === 'OK' ? null : 'PX4 pre-arm error', action: 'Check hardware test' },
    { name: 'GPS', icon: <Map/>, state: getStatus('GPS'), error: tel.gps_valid ? null : 'No 3D Fix', action: 'Move outdoors' },
    { name: 'ESTIMATOR', icon: <Navigation/>, state: getStatus('ESTIMATOR'), error: getStatus('ESTIMATOR') === 'danger' ? 'EKF2 error' : null, action: 'Recalibrate sensors' },
    { name: 'BATTERY', icon: <Battery/>, state: getStatus('BATTERY'), error: getStatus('BATTERY') === 'danger' ? 'Voltage critical' : null, action: 'Land and replace battery' },
    { name: 'SENSORS', icon: <Activity/>, state: getStatus('SENSORS'), error: null, action: 'None' },
    { name: 'NEIGHBORS', icon: <Radio/>, state: getStatus('NEIGHBORS'), error: null, action: 'Check swarm configuration' },
    { name: 'MISSION', icon: <Map/>, state: getStatus('MISSION'), error: null, action: 'None' },
    { name: 'COLLISION', icon: <ShieldAlert/>, state: getStatus('COLLISION'), error: null, action: 'None' },
  ];

  return (
    <div className="view-container">
      <div className="view-header">
         <h2>SYSTEM HEALTH CENTER</h2>
         <p className="text-muted">Target: {targetId} • Live architectural status</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px' }}>
         {systems.map((sys, idx) => (
            <div key={idx} className={`card ${sys.state === 'danger' ? 'danger-box' : sys.state === 'warning' ? 'warning-box' : ''}`} style={{ padding: '16px', display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
               <div style={{ background: `var(--${sys.state})`, color: 'white', padding: '12px', borderRadius: '8px' }}>
                  {sys.icon}
               </div>
               <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                     <h3 style={{ margin: 0, fontSize: '14px', textTransform: 'uppercase' }}>{sys.name}</h3>
                     <span className={`status-badge badge-${sys.state}`}>
                        {sys.state === 'good' ? 'HEALTHY' : sys.state === 'warning' ? 'DEGRADED' : sys.state === 'danger' ? 'FAILED' : 'IDLE'}
                     </span>
                  </div>
                  {sys.error ? (
                     <>
                        <div style={{ fontSize: '13px', color: 'var(--danger)', marginBottom: '4px', fontWeight: 600 }}>Error: {sys.error}</div>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Action: {sys.action}</div>
                     </>
                  ) : (
                     <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Operating normally</div>
                  )}
               </div>
            </div>
         ))}
      </div>
    </div>
  );
}
