import React from 'react';
import { useDroneContext } from '../context/DroneContext';
import { FileText, AlertCircle } from 'lucide-react';

export default function LogsView() {
  const { drones, selectedDrones } = useDroneContext();
  const targetId = Array.from(selectedDrones)[0];

  return (
    <div className="view-container">
      <div className="view-header">
         <h2>SYSTEM LOGS</h2>
         <p className="text-muted">Target: {targetId || 'NONE'}</p>
      </div>
      
      <div className="card" style={{ padding: '40px', textAlign: 'center' }}>
         <FileText size={48} color="var(--text-muted)" style={{marginBottom: '16px'}} />
         <h3>Logs Interface</h3>
         <p className="text-muted" style={{maxWidth: '400px', margin: '0 auto'}}>
            Historical log storage is disabled in this runtime to preserve memory on the Android WebView. 
            Connect directly to DroneOS to retrieve PX4 ULog files.
         </p>
      </div>
    </div>
  );
}
