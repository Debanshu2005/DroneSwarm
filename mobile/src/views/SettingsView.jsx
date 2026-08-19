import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Settings, RefreshCw } from 'lucide-react';

export default function SettingsView() {
  const { wsUrl, setWsUrl, testMode, setTestMode, isConnected } = useDroneContext();
  const [localWsUrl, setLocalWsUrl] = useState(wsUrl);

  const handleSave = (e) => {
    e.preventDefault();
    setWsUrl(localWsUrl);
    window.location.reload(); // Force WS reconnect
  };

  return (
    <div className="view-container fade-in">
      <div className="view-header">
         <h2>Professional Settings</h2>
      </div>

      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px'}}>
         
         {/* Network Settings */}
         <div className="glass-panel">
            <h3><Settings size={18} style={{marginRight: '8px', verticalAlign: 'middle'}}/> Network Configuration</h3>
            <p style={{fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '15px'}}>Configure the connection to the PhoneOS Relay.</p>
            
            <form onSubmit={handleSave} className="settings-form" style={{flexDirection: 'column', alignItems: 'stretch'}}>
               <div className="input-group">
                  <label>Relay WebSocket URL</label>
                  <input type="text" value={localWsUrl} onChange={e => setLocalWsUrl(e.target.value)} />
               </div>
               <button type="submit" className="primary-btn mt-4">Save & Reconnect</button>
            </form>
         </div>

         {/* Advanced */}
         <div className="glass-panel">
            <h3>Advanced & Developer</h3>
            <p style={{fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '15px'}}>Simulation and experimental features.</p>
            
            <label style={{display: 'flex', alignItems: 'center', gap: '10px', background: 'rgba(0,0,0,0.3)', padding: '10px', borderRadius: '8px'}}>
               <input type="checkbox" checked={testMode} onChange={(e) => setTestMode(e.target.checked)} />
               <div>
                  <div style={{fontWeight: 'bold'}}>Enable Virtual Swarm Simulation</div>
                  <div style={{fontSize: '0.8rem', color: 'var(--text-muted)'}}>Spawns virtual drones to test UI layouts without hardware.</div>
               </div>
            </label>
            
            <div style={{marginTop: '20px'}}>
               <button className="secondary-btn" onClick={() => window.location.reload()}>
                  <RefreshCw size={16}/> Force App Reload
               </button>
            </div>
         </div>

      </div>
    </div>
  );
}
