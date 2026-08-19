import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';

export default function DronesView({ setView }) {
  const { drones, selectNone, toggleSelect } = useDroneContext();

  const handleDroneSelect = (id) => {
    selectNone();
    toggleSelect(id);
    setView('DRONE_CONTROL');
  };

  const renderStatusIcon = (status) => {
    if (status === 'active') return <CheckCircle2 size={16} className="good" />;
    if (status === 'failsafe') return <AlertTriangle size={16} className="warning" />;
    return <XCircle size={16} className="danger" />;
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <h2>Fleet List</h2>
        <span className="status-badge">{Object.keys(drones).length} Drones</span>
      </div>

      {Object.keys(drones).length === 0 ? (
        <div className="glass-panel" style={{textAlign: 'center', padding: '40px'}}>
           <h3 style={{color: 'var(--text-muted)'}}>NO DRONES CONNECTED</h3>
        </div>
      ) : (
        <div style={{display: 'flex', flexDirection: 'column'}}>
          {Object.values(drones).map(drone => {
             const tel = drone.telemetry || {};
             const batt = tel.battery_level ?? '--';
             const armed = tel.armed_state === "ARMED";
             
             return (
               <div key={drone.id} className="drone-list-item" onClick={() => handleDroneSelect(drone.id)}>
                 <div className="drone-list-left">
                    <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
                       {renderStatusIcon(drone.status)}
                       <span style={{fontWeight: 700, fontSize: '16px'}}>{drone.id}</span>
                    </div>
                    <span className="text-small">Mode: {tel.flight_mode || 'UNK'}</span>
                 </div>
                 <div className="drone-list-right">
                    <span className={`status-badge ${armed ? 'badge-danger' : 'badge-good'}`}>{armed ? 'ARMED' : 'DISARMED'}</span>
                    <span className="text-small">Bat: {batt}% | {tel.gps_valid ? 'GPS FIX' : 'NO FIX'}</span>
                 </div>
               </div>
             );
          })}
        </div>
      )}
    </div>
  );
}
