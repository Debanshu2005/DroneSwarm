import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Activity, Server, Radio, Trash2, Smartphone, Globe, Cpu, Zap, HardDrive } from 'lucide-react';

export default function DiagnosticsView() {
  const { eventLog, wsManager, isConnected, drones } = useDroneContext();
  const [filter, setFilter] = useState('');
  const [selectedDroneId, setSelectedDroneId] = useState(Object.keys(drones)[0] || null);

  const filteredLogs = eventLog.filter(log => log.msg.toLowerCase().includes(filter.toLowerCase()));
  const targetDrone = selectedDroneId ? drones[selectedDroneId] : null;

  return (
    <div className="view-container">
      <div className="view-header">
         <h2>SYSTEM DIAGNOSTICS</h2>
         <p className="text-muted">End-to-End Hardware & Network Telemetry</p>
      </div>

      {Object.keys(drones).length > 1 && (
         <div style={{ marginBottom: '16px' }}>
            <select className="input-field" value={selectedDroneId || ''} onChange={(e) => setSelectedDroneId(e.target.value)}>
               {Object.keys(drones).map(id => <option key={id} value={id}>{id}</option>)}
            </select>
         </div>
      )}

      <div style={{display: 'flex', gap: '24px', flexWrap: 'wrap'}}>
         <div className="card" style={{flex: 1, minWidth: '300px'}}>
            <h3 style={{marginBottom: '16px', fontSize: '14px', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px'}}><Smartphone size={16}/> PHONEOS (GCS)</h3>
            <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '13px'}}>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">WebSocket:</span> <span className={isConnected === 'CONNECTED' ? 'good' : 'danger'}>{isConnected}</span></div>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Latency:</span> <span>{wsManager?.latency || 0}ms</span></div>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">UI State:</span> <span>ACTIVE</span></div>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Known Drones:</span> <span>{Object.keys(drones).length}</span></div>
            </div>
         </div>

         <div className="card" style={{flex: 1, minWidth: '300px'}}>
            <h3 style={{marginBottom: '16px', fontSize: '14px', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px'}}><Globe size={16}/> RELAY (NETWORK)</h3>
            <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '13px'}}>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Connection:</span> <span>UDP BIND</span></div>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">WS Link:</span> <span className={isConnected === 'CONNECTED' ? 'good' : 'danger'}>{isConnected === 'CONNECTED' ? 'OK' : 'DOWN'}</span></div>
               <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Packets:</span> <span>--</span></div>
            </div>
         </div>
      </div>

      {targetDrone ? (
         <div style={{display: 'flex', gap: '24px', flexWrap: 'wrap', marginTop: '24px'}}>
            <div className="card" style={{flex: 1, minWidth: '300px'}}>
               <h3 style={{marginBottom: '16px', fontSize: '14px', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px'}}><Cpu size={16}/> DRONEOS (PI COMPANION)</h3>
               <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '13px'}}>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Process:</span> <span className={targetDrone.status === 'active' ? 'good' : 'danger'}>{targetDrone.status?.toUpperCase() || 'UNKNOWN'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">CPU:</span> <span>{targetDrone.diagnostics?.system?.cpu_percent ?? '--'}%</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">RAM:</span> <span>{targetDrone.diagnostics?.system?.memory_mb?.toFixed(0) ?? '--'}MB</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Temp:</span> <span>{targetDrone.diagnostics?.system?.temperature_c?.toFixed(1) ?? '--'}°C</span></div>
               </div>
               
               <h3 style={{margin: '16px 0', fontSize: '14px', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px'}}><Radio size={16}/> MAVSDK</h3>
               <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '13px'}}>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Telemetry:</span> <span>{targetDrone.telemetry ? 'ACTIVE' : 'STALE'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Connection:</span> <span>MAVLINK</span></div>
               </div>
            </div>

            <div className="card" style={{flex: 1, minWidth: '300px'}}>
               <h3 style={{marginBottom: '16px', fontSize: '14px', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px'}}><HardDrive size={16}/> PX4 (FLIGHT CONTROLLER)</h3>
               <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '13px'}}>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Firmware:</span> <span>{targetDrone.diagnostics?.px4?.firmware_version || 'N/A'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Vehicle:</span> <span>{targetDrone.diagnostics?.px4?.vehicle_type || 'N/A'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Mode:</span> <span>{targetDrone.telemetry?.flight_mode || '--'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Armed:</span> <span className={targetDrone.telemetry?.armed_state === 'ARMED' ? 'danger' : 'good'}>{targetDrone.telemetry?.armed_state || '--'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Health:</span> <span>{targetDrone.telemetry?.system_health || '--'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Estimator:</span> <span>{targetDrone.telemetry?.gps_valid ? '3D FIX' : 'NO FIX'}</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Battery:</span> <span>{targetDrone.telemetry?.battery_level ?? '--'}%</span></div>
                  <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">Failsafe:</span> <span>{targetDrone.status === 'failsafe' ? 'ACTIVE' : 'CLEAR'}</span></div>
               </div>
            </div>
         </div>
      ) : (
         <div style={{ marginTop: '24px', textAlign: 'center', padding: '24px', background: 'var(--bg-main)', borderRadius: '8px' }}>
            No drone connected to view hardware layer.
         </div>
      )}

      <div className="card" style={{marginTop: '24px', display: 'flex', flexDirection: 'column', height: '400px'}}>
         <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px'}}>
            <h3 style={{fontSize: '14px', fontWeight: 600, textTransform: 'uppercase'}}>END-TO-END EVENT TRACE</h3>
            <div style={{display: 'flex', gap: '10px'}}>
               <input type="text" placeholder="Filter traces..." value={filter} onChange={e => setFilter(e.target.value)} style={{padding: '6px 10px', borderRadius: '4px', background: 'var(--bg-color)', border: '1px solid var(--border)', color: 'var(--text-main)', fontSize: '13px'}} />
            </div>
         </div>
         
         <div style={{flex: 1, overflowY: 'auto', background: 'var(--bg-color)', padding: '12px', borderRadius: '8px', fontFamily: 'monospace', fontSize: '12px', border: '1px solid var(--border)'}}>
            {filteredLogs.length === 0 ? (
               <div style={{color: 'var(--text-muted)'}}>No events.</div>
            ) : (
               filteredLogs.map((log, i) => (
                  <div key={i} style={{marginBottom: '8px', borderBottom: '1px solid var(--border)', paddingBottom: '8px'}}>
                     <div style={{color: 'var(--text-muted)', marginBottom: '4px'}}>{new Date(log.time).toISOString().substring(11,23)}</div> 
                     <div style={{color: 'var(--text-main)', whiteSpace: 'pre-wrap', lineHeight: 1.4}}>{log.msg}</div>
                  </div>
               ))
            )}
         </div>
      </div>
    </div>
  );
}
