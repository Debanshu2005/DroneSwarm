import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Plus, Server, Activity, CheckCircle2, AlertTriangle } from 'lucide-react';

export default function AddDroneView() {
  const { wsUrl, setWsUrl, isConnected } = useDroneContext();
  const [inputUrl, setInputUrl] = useState(wsUrl);
  const [statusMsg, setStatusMsg] = useState('');
  
  const handleConnect = () => {
    setStatusMsg('Connecting...');
    setWsUrl(inputUrl);
    // Connection status will be reflected via isConnected from context
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h2>Add Swarm Relay</h2>
        <p className="text-muted">Connect to a DroneOS Relay Server via WebSocket</p>
      </div>

      <div className="card" style={{ maxWidth: '500px', margin: '0 auto', padding: '32px' }}>
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
           <Server size={48} color="var(--primary)" style={{ marginBottom: '16px' }} />
           <h3>Connection Details</h3>
        </div>
        
        <div style={{ marginBottom: '24px' }}>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500, fontSize: '14px' }}>WebSocket URL</label>
          <input 
            type="text" 
            value={inputUrl} 
            onChange={(e) => setInputUrl(e.target.value)}
            placeholder="ws://192.168.1.100:8080"
            className="input-field"
            style={{ width: '100%', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--bg-main)' }}
          />
        </div>

        <button 
           className="btn btn-primary" 
           style={{ width: '100%', padding: '12px', fontSize: '16px', display: 'flex', justifyContent: 'center', gap: '8px' }}
           onClick={handleConnect}
        >
          <Plus size={20} /> Connect Relay
        </button>

        <div style={{ marginTop: '32px', padding: '16px', borderRadius: '8px', background: 'var(--bg-main)', border: '1px solid var(--border-color)' }}>
           <h4 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', fontSize: '14px' }}>
             <Activity size={16} /> Status
           </h4>
           
           {isConnected === "CONNECTED" ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#10b981' }}>
                 <CheckCircle2 size={16} />
                 <span>Successfully connected to {wsUrl}</span>
              </div>
           ) : isConnected === "CONNECTING" ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#f59e0b' }}>
                 <Activity size={16} className="spin" />
                 <span>Attempting to connect to {wsUrl}...</span>
              </div>
           ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ef4444' }}>
                 <AlertTriangle size={16} />
                 <span>Disconnected. Please check the URL and ensure the relay is running.</span>
              </div>
           )}
        </div>
      </div>
    </div>
  );
}
