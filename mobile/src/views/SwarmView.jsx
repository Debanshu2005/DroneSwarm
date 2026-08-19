import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Network, ArrowRight } from 'lucide-react';

export default function SwarmView() {
  const { drones } = useDroneContext();
  
  const onlineDrones = Object.values(drones).filter(d => d.status === 'active');
  const warningDrones = Object.values(drones).filter(d => d.status === 'failsafe');
  const offlineDrones = Object.values(drones).filter(d => d.status === 'OFFLINE');
  const armedDrones = Object.values(drones).filter(d => d?.telemetry?.armed_state === 'ARMED');

  return (
    <div className="view-container fade-in" style={{display: 'flex', flexDirection: 'column'}}>
      <div className="view-header">
         <h2>Decentralized Swarm Topology</h2>
      </div>

      <div className="glass-panel diagnostics-panel" style={{marginBottom: '15px'}}>
         <div className="diag-item">
            <span className="diag-label">Swarm Size</span>
            <span className="diag-val">{Object.keys(drones).length}</span>
         </div>
         <div className="diag-item">
            <span className="diag-label">Online</span>
            <span className="diag-val good">{onlineDrones.length}</span>
         </div>
         <div className="diag-item">
            <span className="diag-label">Armed</span>
            <span className="diag-val danger">{armedDrones.length}</span>
         </div>
         <div className="diag-item">
            <span className="diag-label">Warning/Failsafe</span>
            <span className="diag-val warning">{warningDrones.length}</span>
         </div>
         <div className="diag-item">
            <span className="diag-label">Offline</span>
            <span className="diag-val">{offlineDrones.length}</span>
         </div>
      </div>

      <div className="glass-panel" style={{flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
         <Network size={64} style={{opacity: 0.2, marginBottom: '20px'}}/>
         <h3 style={{color: 'var(--warning)', marginBottom: '10px'}}>BACKEND SUPPORT REQUIRED</h3>
         <p style={{color: 'var(--text-muted)', textAlign: 'center', maxWidth: '400px'}}>
            True decentralized swarm topology mapping requires the DroneOS backend to emit `SWARM_STATE` or `PEER_STATE` packets with adjacency lists. 
            Currently, the Ground Station only receives independent telemetry streams.
         </p>
         
         <div style={{marginTop: '30px', padding: '15px', background: 'rgba(0,0,0,0.3)', borderRadius: '8px', width: '100%', maxWidth: '500px'}}>
            <h4 style={{marginBottom: '10px', fontSize: '0.9rem'}}>Simulated Topology Visualization</h4>
            <div style={{display: 'flex', gap: '20px', flexWrap: 'wrap', justifyContent: 'center'}}>
               {onlineDrones.map(d => (
                  <div key={d.id} style={{display: 'flex', alignItems: 'center', gap: '10px'}}>
                     <div className="status-badge good-bg">{d.id}</div>
                     <ArrowRight size={16} style={{opacity: 0.5}}/>
                  </div>
               ))}
               <div className="status-badge" style={{background: 'rgba(255,255,255,0.1)'}}>GCS</div>
            </div>
         </div>
         
         <div style={{marginTop: '20px', padding: '15px', background: 'rgba(0,0,0,0.3)', borderRadius: '8px', width: '100%', maxWidth: '500px'}}>
            <h4 style={{marginBottom: '10px', fontSize: '0.9rem'}}>Swarm Formation Control</h4>
            <div style={{display: 'flex', gap: '10px', alignItems: 'center'}}>
               <select disabled className="disabled-panel" style={{flex: 1, padding: '8px'}}>
                  <option>Diamond</option>
                  <option>V</option>
                  <option>Line</option>
                  <option>Square</option>
                  <option>Circle</option>
               </select>
               <button className="primary-btn disabled-panel" disabled>Apply</button>
            </div>
            <div style={{color: 'var(--danger)', fontSize: '0.8rem', marginTop: '10px', textAlign: 'center'}}>
               FORMATION CONTROL UNSUPPORTED BY BACKEND
            </div>
         </div>
      </div>
    </div>
  );
}
