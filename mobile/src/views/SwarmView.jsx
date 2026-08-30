import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Network, ArrowRight } from 'lucide-react';

export default function SwarmView() {
  const { drones } = useDroneContext();
  
  const onlineDrones = Object.values(drones).filter(d => d.status === 'CONNECTED' || d.status === 'DEGRADED');
  const warningDrones = Object.values(drones).filter(d => d.status === 'failsafe');
  const offlineDrones = Object.values(drones).filter(d => d.status === 'OFFLINE');
  const armedDrones = Object.values(drones).filter(d => d?.telemetry?.armed_state === 'ARMED');

  const [selectedShape, setSelectedShape] = useState('Diamond');
  const [spacingValue, setSpacingValue] = useState('5');
  const { sendCommand } = useDroneContext();

  const handleApply = () => {
    if (onlineDrones.length === 0) {
      alert("No online drones available for formation.");
      return;
    }
    
    const isAnyArmed = onlineDrones.some(d => d?.telemetry?.armed_state === 'ARMED');
    if (!isAnyArmed) {
      alert("No targeted drones are armed. Please arm at least one drone first.");
      return;
    }
    
    const targetIds = onlineDrones.map(d => d.id);
    sendCommand(
      'formation_update', // Assuming CommandAction.FORMATION_UPDATE resolves to 'formation_update'
      { type: selectedShape.toUpperCase(), spacing: Number(spacingValue) || 5 },
      targetIds
    );
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px'}}>
         <h2>Decentralized Swarm Topology</h2>
      </div>

      <div className="metrics-row">
         <div className="metric-card">
            <span className="metric-label">Swarm Size</span>
            <span className="metric-value">{Object.keys(drones).length}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">Online</span>
            <span className="metric-value good">{onlineDrones.length}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">Armed</span>
            <span className="metric-value danger">{armedDrones.length}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">Warning/Failsafe</span>
            <span className="metric-value warning">{warningDrones.length}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">Offline</span>
            <span className="metric-value">{offlineDrones.length}</span>
         </div>
      </div>

      <div className="glass-panel" style={{flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px 20px'}}>
         <Network size={64} color="var(--border)" style={{marginBottom: '20px'}}/>
         <h3 style={{color: 'var(--warning)', marginBottom: '10px'}}>BACKEND SUPPORT REQUIRED</h3>
         <p style={{color: 'var(--text-muted)', textAlign: 'center', maxWidth: '400px'}}>
            True decentralized swarm topology mapping requires the DroneOS backend to emit `SWARM_STATE` or `PEER_STATE` packets with adjacency lists. 
            Currently, the Ground Station only receives independent telemetry streams.
         </p>
         
         <div style={{marginTop: '30px', padding: '15px', background: 'var(--bg-color)', border: '1px solid var(--border)', borderRadius: '8px', width: '100%', maxWidth: '500px'}}>
            <h4 style={{marginBottom: '10px', fontSize: '14px'}}>Simulated Topology Visualization</h4>
            <div style={{display: 'flex', gap: '20px', flexWrap: 'wrap', justifyContent: 'center'}}>
               {onlineDrones.map(d => (
                  <div key={d.id} style={{display: 'flex', alignItems: 'center', gap: '10px'}}>
                     <div className="status-badge badge-good">{d.id}</div>
                     <ArrowRight size={16} color="var(--border)"/>
                  </div>
               ))}
               <div className="status-badge" style={{background: 'var(--border)'}}>GCS</div>
            </div>
         </div>
         
         <div style={{marginTop: '20px', padding: '15px', background: 'var(--bg-color)', border: '1px solid var(--border)', borderRadius: '8px', width: '100%', maxWidth: '500px'}}>
            <h4 style={{marginBottom: '10px', fontSize: '14px'}}>Swarm Formation Control</h4>
            <div style={{display: 'flex', gap: '10px', alignItems: 'center'}}>
               <select 
                  value={selectedShape}
                  onChange={(e) => setSelectedShape(e.target.value)}
                  style={{flex: 1, padding: '10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px'}}
               >
                  <option>Diamond</option>
                  <option>V</option>
                  <option>Line</option>
                  <option>Column</option>
                  <option>Square</option>
                  <option>Circle</option>
                  <option>Grid</option>
                  <option>Wedge</option>
                  <option>Echelon_Left</option>
                  <option>Echelon_Right</option>
               </select>
               <input
                  type="number"
                  value={spacingValue}
                  onChange={(e) => setSpacingValue(e.target.value)}
                  placeholder="Spacing (m)"
                  style={{width: '100px', padding: '10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', color: 'var(--text)'}}
               />
               <button className="primary-btn" onClick={handleApply}>Apply</button>
            </div>
            
            <div style={{marginTop: '15px'}}>
               <h5 style={{fontSize: '12px', color: 'var(--text-muted)', marginBottom: '5px'}}>Formation Command Status</h5>
               {onlineDrones.length > 0 ? (
                  <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '8px'}}>
                     {onlineDrones.map(d => {
                        const isFormCmd = d.commandState?.action === 'formation_update';
                        const stateColor = isFormCmd && d.commandState?.state === 'ACCEPTED' ? 'var(--success)' :
                                           isFormCmd && d.commandState?.state === 'REJECTED' ? 'var(--danger)' :
                                           isFormCmd && d.commandState?.state === 'SENDING' ? 'var(--warning)' : 'var(--text-muted)';
                        
                        return (
                           <div key={d.id} style={{fontSize: '11px', display: 'flex', justifyContent: 'space-between', padding: '4px 8px', background: 'var(--surface)', borderRadius: '4px'}}>
                              <span>{d.id}</span>
                              <span style={{color: stateColor, fontWeight: 600}}>
                                 {isFormCmd ? d.commandState.state : 'IDLE'}
                              </span>
                           </div>
                        );
                     })}
                  </div>
               ) : (
                  <div style={{fontSize: '12px', color: 'var(--text-muted)'}}>No online drones.</div>
               )}
            </div>
         </div>
      </div>
    </div>
  );
}
