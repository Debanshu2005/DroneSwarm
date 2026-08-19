import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { ShieldAlert, AlertOctagon, CheckCircle2, AlertTriangle, XCircle } from 'lucide-react';

export default function SafetyView() {
  const { drones, nowMs } = useDroneContext();

  const renderStatus = (isGood, isWarning, label, goodText, warningText, badText) => {
    let icon, color, text;
    if (isGood) {
      icon = <CheckCircle2 size={16} className="good" />;
      color = 'var(--text-main)';
      text = goodText;
    } else if (isWarning) {
      icon = <AlertTriangle size={16} className="warning" />;
      color = 'var(--warning)';
      text = warningText;
    } else {
      icon = <XCircle size={16} className="danger" />;
      color = 'var(--danger)';
      text = badText;
    }

    return (
      <div style={{display: 'flex', alignItems: 'center', gap: '12px', padding: '8px 0', borderBottom: '1px solid var(--border)'}}>
        {icon}
        <span style={{fontWeight: 600, width: '120px', color: 'var(--text-muted)'}}>{label}</span>
        <span style={{fontWeight: 600, color}}>{text}</span>
      </div>
    );
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px'}}>
         <h2>Safety Center</h2>
      </div>

      <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
         {Object.values(drones).map(drone => {
            const tel = drone.telemetry || {};
            const ageMs = nowMs - drone.lastSeen;
            const isStale = ageMs > 3000;
            const isOffline = drone.status === 'OFFLINE';
            const batt = tel.battery_level || 0;
            const lowBatt = batt < 20;
            const noGps = !tel.gps_valid;
            const isFailsafe = drone.status === 'failsafe';
            const px4Connected = tel.flight_mode && tel.flight_mode !== 'disconnected' && tel.flight_mode !== 'UNKNOWN';

            return (
               <div key={drone.id} className="glass-panel" style={{display: 'flex', flexDirection: 'column', gap: '8px', opacity: isOffline ? 0.6 : 1}}>
                  <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px'}}>
                     <h3 style={{fontSize: '18px'}}>{drone.id}</h3>
                     <span className={`status-badge ${isOffline ? 'badge-danger' : isFailsafe ? 'badge-warning' : 'badge-good'}`}>
                       {isOffline ? 'OFFLINE' : isFailsafe ? 'FAILSAFE' : 'NORMAL'}
                     </span>
                  </div>
                  
                  <div style={{display: 'flex', flexDirection: 'column'}}>
                     {renderStatus(!noGps, false, 'GPS', '3D FIX', '', 'NO FIX')}
                     {renderStatus(!lowBatt, false, 'BATTERY', `${batt}% (NORMAL)`, '', `${batt}% (LOW)`)}
                     {renderStatus(!isStale, false, 'TELEMETRY', 'FRESH', '', 'STALE')}
                     {renderStatus(px4Connected, false, 'PX4', 'CONNECTED', '', 'DISCONNECTED')}
                     {renderStatus(!isFailsafe, false, 'FAILSAFE', 'CLEAR', '', 'ACTIVE')}
                     {renderStatus(tel.system_health === 'OK', tel.system_health == null, 'FCU HEALTH', 'HEALTHY', 'UNKNOWN', 'ERROR')}
                     {renderStatus(tel.rc_status !== 'disconnected', tel.rc_status === 'weak', 'RC SIGNAL', 'ACTIVE', 'WEAK', 'DISCONNECTED')}
                  </div>
               </div>
            );
         })}
         {Object.keys(drones).length === 0 && (
            <div className="glass-panel" style={{textAlign: 'center', padding: '40px', color: 'var(--text-muted)'}}>
               <h3>No drones connected.</h3>
            </div>
         )}
      </div>
    </div>
  );
}
