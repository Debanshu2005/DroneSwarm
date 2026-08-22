import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Terminal } from 'lucide-react';

export default function LogsView() {
  const { eventLog } = useDroneContext();

  const getSeverityColor = (severity) => {
     if (severity === 'ERROR' || severity === 'CRITICAL') return 'var(--danger)';
     if (severity === 'WARNING') return 'var(--warning)';
     if (severity === 'SUCCESS') return 'var(--success)';
     return 'var(--text-main)';
  };

  return (
    <div className="view-container" style={{display: 'flex', flexDirection: 'column', height: '100%'}}>
      <div className="view-header" style={{flexShrink: 0}}>
         <h2>SYSTEM LOGS</h2>
         <p className="text-muted">Live event stream from PhoneOS, Relay, and DroneOS</p>
      </div>
      
      <div className="glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 0 }}>
         <div style={{background: '#1E293B', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: '8px', color: '#F8FAFC', borderTopLeftRadius: '8px', borderTopRightRadius: '8px', flexShrink: 0}}>
            <Terminal size={16}/> <span style={{fontSize: '12px', fontWeight: 'bold'}}>LIVE LOG STREAM</span>
         </div>
         <div style={{ flex: 1, overflowY: 'auto', background: '#0F172A', color: '#CBD5E1', padding: '16px', fontFamily: 'monospace', fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {eventLog.length === 0 ? (
               <div style={{color: '#64748B', fontStyle: 'italic'}}>No logs available yet...</div>
            ) : (
               eventLog.map((log, i) => {
                  const d = new Date(log.time);
                  const timeStr = `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
                  return (
                     <div key={i} style={{display: 'flex', gap: '12px', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '4px'}}>
                        <span style={{color: '#64748B', minWidth: '60px'}}>{timeStr}</span>
                        <span style={{color: '#94A3B8', minWidth: '70px', fontWeight: 'bold'}}>[{log.source || 'SYS'}]</span>
                        <span style={{color: '#38BDF8', minWidth: '70px'}}>{log.droneId || 'ALL'}</span>
                        <span style={{color: getSeverityColor(log.severity), flex: 1}}>{log.msg}</span>
                     </div>
                  );
               })
            )}
         </div>
      </div>
    </div>
  );
}
