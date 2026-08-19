import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Activity, Server, Radio, Trash2 } from 'lucide-react';

export default function DiagnosticsView() {
  const { eventLog, wsManager, isConnected, drones } = useDroneContext();
  const [filter, setFilter] = useState('');

  const filteredLogs = eventLog.filter(log => log.msg.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="view-container fade-in" style={{display: 'flex', flexDirection: 'column', height: '100%'}}>
      <div className="view-header">
         <h2>Diagnostics & Logs</h2>
      </div>

      <div className="glass-panel diagnostics-panel" style={{marginBottom: '15px'}}>
         <div className="diag-item">
            <span className="diag-label"><Server size={14}/> WebSocket</span>
            <span className={`diag-val ${isConnected === 'CONNECTED' ? 'good' : 'danger'}`}>{isConnected}</span>
         </div>
         <div className="diag-item">
            <span className="diag-label"><Radio size={14}/> Known Drones</span>
            <span className="diag-val">{Object.keys(drones).length}</span>
         </div>
         <div className="diag-item">
            <span className="diag-label"><Activity size={14}/> WS ReadyState</span>
            <span className="diag-val">{wsManager?.ws?.readyState ?? 'NULL'}</span>
         </div>
      </div>

      <div className="glass-panel" style={{flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
         <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px'}}>
            <h3>Event Log</h3>
            <div style={{display: 'flex', gap: '10px'}}>
               <input type="text" placeholder="Filter logs..." value={filter} onChange={e => setFilter(e.target.value)} style={{padding: '4px 8px', borderRadius: '4px', background: 'rgba(0,0,0,0.5)', border: '1px solid var(--glass-border)', color: 'white'}} />
               {/* Note: clear logs would require a method in context, omitted for brevity */}
            </div>
         </div>
         
         <div style={{flex: 1, overflowY: 'auto', background: 'rgba(0,0,0,0.3)', padding: '10px', borderRadius: '8px', fontFamily: 'monospace', fontSize: '0.85rem'}}>
            {filteredLogs.length === 0 ? (
               <div style={{color: 'var(--text-muted)'}}>No events.</div>
            ) : (
               filteredLogs.map((log, i) => (
                  <div key={i} style={{marginBottom: '4px', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '2px'}}>
                     <span style={{color: 'var(--text-muted)'}}>{new Date(log.time).toISOString().substring(11,23)}</span> 
                     <span style={{marginLeft: '10px'}}>{log.msg}</span>
                  </div>
               ))
            )}
         </div>
      </div>
    </div>
  );
}
