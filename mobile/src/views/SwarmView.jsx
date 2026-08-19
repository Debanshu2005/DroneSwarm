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
               <select disabled style={{flex: 1, padding: '10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', opacity: 0.5}}>
                  <option>Diamond</option>
                  <option>V</option>
                  <option>Line</option>
                  <option>Square</option>
                  <option>Circle</option>
               </select>
               <button className="primary-btn" disabled style={{opacity: 0.5}}>Apply</button>
            </div>
            <div style={{color: 'var(--danger)', fontSize: '12px', marginTop: '10px', textAlign: 'center', fontWeight: 600}}>
               FORMATION CONTROL UNSUPPORTED BY BACKEND
            </div>
         </div>
      </div>
    </div>
  );
}
