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
               <div key={drone.id} className={`drone-card ${isOffline ? 'offline-state' : ''}`} style={{cursor: 'default'}}>
                  <div className="drone-card-header">
                     <span>{drone.id}</span>
                     <div className={`status-badge ${headerClass}`}>{cardStatus}</div>
                  </div>
                  
                  <div style={{marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '0.85rem'}}>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <span style={{display: 'flex', alignItems: 'center', gap:'5px'}}><WifiOff size={14}/> Link Age:</span>
                        <span className={isStale ? 'danger' : 'good'}>{(ageMs/1000).toFixed(1)}s</span>
                     </div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <span style={{display: 'flex', alignItems: 'center', gap:'5px'}}><BatteryWarning size={14}/> Battery:</span>
                        <span className={lowBatt ? 'danger' : 'good'}>{batt}% {lowBatt ? '(LOW)' : ''}</span>
                     </div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <span style={{display: 'flex', alignItems: 'center', gap:'5px'}}><AlertOctagon size={14}/> PX4 Failsafe:</span>
                        <span className={isFailsafe ? 'danger' : 'good'}>{isFailsafe ? 'ACTIVE' : 'NONE'}</span>
                     </div>
                     <div style={{display: 'flex', justifyContent: 'space-between'}}>
                        <span style={{display: 'flex', alignItems: 'center', gap:'5px'}}><ShieldAlert size={14}/> GPS State:</span>
                        <span className={noGps ? 'danger' : 'good'}>{noGps ? 'NO FIX' : '3D FIX'}</span>
                     </div>
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
