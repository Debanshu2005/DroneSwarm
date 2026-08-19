import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Activity, Server, Radio, Trash2 } from 'lucide-react';

export default function DiagnosticsView() {
  const { eventLog, wsManager, isConnected, drones } = useDroneContext();
  const [filter, setFilter] = useState('');

  const filteredLogs = eventLog.filter(log => log.msg.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100%', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px'}}>
         <h2>Diagnostics & Logs</h2>
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

      <div style={{display: 'flex', gap: '16px', flex: 1, minHeight: 0, flexWrap: 'wrap'}}>
         <div className="glass-panel" style={{flex: 1, minWidth: '300px', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px'}}>
               <h3>Event Log</h3>
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

         <div className="glass-panel" style={{flex: 1, minWidth: '300px', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <h3 style={{marginBottom: '16px'}}>Motor / Vehicle Diagnostics</h3>
            <div style={{flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px'}}>
               {Object.values(drones).length === 0 ? (
                  <div style={{color: 'var(--text-muted)'}}>No drones connected.</div>
               ) : (
                  Object.values(drones).map(drone => {
                     const tel = drone.telemetry || {};
                     return (
                        <div key={drone.id} style={{background: 'var(--bg-color)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border)'}}>
                           <h4 style={{marginBottom: '12px', fontSize: '14px'}}>{drone.id}</h4>
                           <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '13px'}}>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Actuator Status:</span> <span>{tel.actuator_status || 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>ESC RPM:</span> <span>{tel.esc_rpm != null ? tel.esc_rpm : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>ESC Voltage:</span> <span>{tel.esc_voltage != null ? `${tel.esc_voltage.toFixed(1)}V` : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>ESC Current:</span> <span>{tel.esc_current != null ? `${tel.esc_current.toFixed(1)}A` : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>ESC Temp:</span> <span>{tel.esc_temperature != null ? `${tel.esc_temperature.toFixed(1)}°C` : 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Sensor Health:</span> <span>{tel.sensor_health || 'N/A'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Errors:</span> <span className={tel.errors ? 'danger' : ''}>{tel.errors || 'None'}</span></div>
                              <div style={{display: 'flex', justifyContent: 'space-between'}}><span>Warnings:</span> <span className={tel.warnings ? 'warning' : ''}>{tel.warnings || 'None'}</span></div>
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
