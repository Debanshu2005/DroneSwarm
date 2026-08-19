import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { ShieldAlert, BatteryWarning, WifiOff, AlertOctagon } from 'lucide-react';

export default function SafetyView() {
  const { drones, nowMs } = useDroneContext();

  return (
    <div className="view-container fade-in" style={{display: 'flex', flexDirection: 'column'}}>
      <div className="view-header">
         <h2>Safety Center</h2>
      </div>

      <div className="fleet-grid">
         {Object.values(drones).map(drone => {
            const tel = drone.telemetry || {};
            const ageMs = nowMs - drone.lastSeen;
            const isStale = ageMs > 3000;
            const isOffline = drone.status === 'OFFLINE';
            const batt = tel.battery_level || 0;
            const lowBatt = batt < 20;
            const noGps = !tel.gps_valid;
            const isFailsafe = drone.status === 'failsafe';
            
            let cardStatus = "NORMAL";
            let headerClass = "good-bg";
            if (isOffline) { cardStatus = "OFFLINE"; headerClass = "danger-bg"; }
            else if (isFailsafe || isStale || lowBatt || noGps) { cardStatus = "WARNING"; headerClass = "warning-bg"; }
            if (isFailsafe && lowBatt) { cardStatus = "CRITICAL"; headerClass = "danger-bg"; }

            return (
               <div key={drone.id} className={`drone-card ${isOffline ? 'offline-state' : ''}`} style={{cursor: 'default', minWidth: '300px'}}>
                  <div className="drone-card-header">
                     <span>{drone.id}</span>
                     <div className={`status-badge ${headerClass}`}>{cardStatus}</div>
                  </div>
                  
                  <div style={{marginTop: '10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '0.8rem'}}>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Link Age:</span> <span className={isStale ? 'danger' : 'good'}>{(ageMs/1000).toFixed(1)}s</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Battery:</span> <span className={lowBatt ? 'danger' : 'good'}>{batt}%</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Voltage:</span> <span>{tel.battery_voltage ? `${tel.battery_voltage.toFixed(1)}V` : 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Current:</span> <span>{tel.battery_current ? `${tel.battery_current.toFixed(1)}A` : 'N/A'}</span></div>
                     
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Failsafe:</span> <span className={isFailsafe ? 'danger' : 'good'}>{isFailsafe ? 'ACTIVE' : 'NONE'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>GPS:</span> <span className={noGps ? 'danger' : 'good'}>{noGps ? 'NO FIX' : '3D FIX'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Sats:</span> <span>{tel.satellites ?? 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>HDOP/VDOP:</span> <span>{tel.hdop ?? 'N/A'} / {tel.vdop ?? 'N/A'}</span></div>
                     
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Alt (Rel):</span> <span>{tel.altitude != null ? `${tel.altitude.toFixed(1)}m` : 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>V-Speed:</span> <span>{tel.vertical_speed != null ? `${tel.vertical_speed.toFixed(1)}m/s` : 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>G-Speed:</span> <span>{tel.ground_speed != null ? `${tel.ground_speed.toFixed(1)}m/s` : 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>A-Speed:</span> <span>{tel.air_speed != null ? `${tel.air_speed.toFixed(1)}m/s` : 'N/A'}</span></div>
                     
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Roll:</span> <span>{tel.roll != null ? `${tel.roll.toFixed(1)}°` : 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Pitch:</span> <span>{tel.pitch != null ? `${tel.pitch.toFixed(1)}°` : 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Yaw (Hdg):</span> <span>{tel.heading != null ? `${tel.heading.toFixed(1)}°` : 'N/A'}</span></div>
                     
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Sys Health:</span> <span>{tel.system_health || 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Estimator:</span> <span>{tel.estimator_status || 'N/A'}</span></div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}><span>RC Status:</span> <span>{tel.rc_status || 'N/A'}</span></div>
                  </div>
               </div>
            );
         })}
         {Object.keys(drones).length === 0 && (
            <div className="no-drone-msg glass-panel" style={{gridColumn: '1 / -1'}}>No drones connected.</div>
         )}
      </div>
    </div>
  );
}
