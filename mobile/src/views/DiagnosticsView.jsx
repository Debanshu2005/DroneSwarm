import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Activity, Server, Radio, Trash2 } from 'lucide-react';

export default function DiagnosticsView() {
  const { eventLog, wsManager, isConnected, drones } = useDroneContext();
  const [filter, setFilter] = useState('');

  const filteredLogs = eventLog.filter(log => log.msg.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="view-container">
      <div className="view-header">
         <h2>Diagnostics & Logs</h2>
         <p className="text-muted">System Health and Network Events</p>
      </div>

      <div className="metrics-row">
         <div className="metric-card">
            <span className="metric-label"><Server size={14} style={{display: 'inline', verticalAlign: 'middle'}}/> WebSocket</span>
            <span className={`metric-value ${isConnected === 'CONNECTED' ? 'good' : 'danger'}`}>{isConnected}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label"><Radio size={14} style={{display: 'inline', verticalAlign: 'middle'}}/> Known Drones</span>
            <span className="metric-value">{Object.keys(drones).length}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label"><Activity size={14} style={{display: 'inline', verticalAlign: 'middle'}}/> WS ReadyState</span>
            <span className="metric-value">{wsManager?.ws?.readyState ?? 'NULL'}</span>
         </div>
      </div>

      <div style={{display: 'flex', gap: '24px', flex: 1, minHeight: 0, flexWrap: 'wrap'}}>
         <div className="card" style={{flex: 1, minWidth: '350px', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px'}}>
               <h3 style={{fontSize: '16px', fontWeight: 600}}>Event Log</h3>
               <div style={{display: 'flex', gap: '10px'}}>
                  <input type="text" placeholder="Filter logs..." value={filter} onChange={e => setFilter(e.target.value)} style={{padding: '6px 10px', borderRadius: '4px', background: 'var(--bg-color)', border: '1px solid var(--border)', color: 'var(--text-main)', fontSize: '13px'}} />
               </div>
            </div>
            
            <div style={{flex: 1, overflowY: 'auto', background: 'var(--bg-color)', padding: '12px', borderRadius: '8px', fontFamily: 'monospace', fontSize: '12px', border: '1px solid var(--border)'}}>
               {filteredLogs.length === 0 ? (
                  <div style={{color: 'var(--text-muted)'}}>No events.</div>
               ) : (
                  filteredLogs.map((log, i) => (
                     <div key={i} style={{marginBottom: '4px', borderBottom: '1px solid var(--border)', paddingBottom: '4px'}}>
                        <span style={{color: 'var(--text-muted)'}}>{new Date(log.time).toISOString().substring(11,23)}</span> 
                        <span style={{marginLeft: '12px', color: 'var(--text-main)'}}>{log.msg}</span>
                     </div>
                  ))
               )}
            </div>
         </div>

         <div className="card" style={{flex: 1, minWidth: '350px', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <h3 style={{marginBottom: '16px', fontSize: '16px', fontWeight: 600}}>DroneOS Hardware Diagnostics</h3>
            <div style={{flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '16px'}}>
               {Object.values(drones).length === 0 ? (
                  <div style={{color: 'var(--text-muted)'}}>No drones connected.</div>
               ) : (
                  Object.values(drones).map(drone => {
                     const tel = drone.telemetry || {};
                     const diag = drone.diagnostics || {};
                     const sys = diag.system || {};
                     
                     return (
                        <div key={drone.id} style={{background: 'var(--bg-main)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border-color)'}}>
                           <h4 style={{marginBottom: '16px', fontSize: '14px', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px'}}>{drone.id} - Raspberry Pi</h4>
                           
                           <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', fontSize: '13px', marginBottom: '16px'}}>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                 <span className="text-muted">CPU Usage:</span> 
                                 <span style={{fontWeight: 500}}>{sys.cpu_percent != null ? `${sys.cpu_percent}%` : 'N/A'}</span>
                              </div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                 <span className="text-muted">RAM Usage:</span> 
                                 <span style={{fontWeight: 500}}>{sys.memory_mb != null ? `${sys.memory_mb.toFixed(0)} MB` : 'N/A'}</span>
                              </div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                 <span className="text-muted">Pi Temp:</span> 
                                 <span style={{fontWeight: 500, color: sys.temperature_c > 75 ? '#ef4444' : 'inherit'}}>{sys.temperature_c != null ? `${sys.temperature_c.toFixed(1)}°C` : 'N/A'}</span>
                              </div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}>
                                 <span className="text-muted">Async Tasks:</span> 
                                 <span style={{fontWeight: 500}}>{sys.async_tasks || 'N/A'}</span>
                              </div>
                           </div>

                           <h4 style={{marginBottom: '16px', fontSize: '14px', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px'}}>PX4 Motors & ESC</h4>
                           <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', fontSize: '13px'}}>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">ESC RPM:</span> <span style={{fontWeight: 500}}>{tel.esc_rpm != null ? tel.esc_rpm : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">ESC Voltage:</span> <span style={{fontWeight: 500}}>{tel.esc_voltage != null ? `${tel.esc_voltage.toFixed(1)}V` : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">ESC Current:</span> <span style={{fontWeight: 500}}>{tel.esc_current != null ? `${tel.esc_current.toFixed(1)}A` : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span className="text-muted">ESC Temp:</span> <span style={{fontWeight: 500}}>{tel.esc_temperature != null ? `${tel.esc_temperature.toFixed(1)}°C` : 'N/A'}</span></div>
                           </div>
                        </div>
                     )
                  })
               )}
            </div>
         </div>
      </div>
    </div>
  );
}
