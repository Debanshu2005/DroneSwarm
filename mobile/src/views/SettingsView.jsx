import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Settings, RefreshCw } from 'lucide-react';

export default function SettingsView() {
  const { wsUrl, setWsUrl, testMode, setTestMode, indoorMode, setIndoorMode, isConnected } = useDroneContext();
  const [localWsUrl, setLocalWsUrl] = useState(wsUrl);

  const handleSave = (e) => {
    e.preventDefault();
    setWsUrl(localWsUrl);
    window.location.reload(); // Force WS reconnect
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px'}}>
         <h2>Professional Settings</h2>
      </div>

      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px'}}>
         
         {/* Network Settings */}
         <div className="glass-panel">
            <h3 style={{marginBottom: '8px'}}><Settings size={18} style={{marginRight: '8px', verticalAlign: 'middle'}}/> Network Configuration</h3>
            <p style={{fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px'}}>Configure the connection to the PhoneOS Relay.</p>
            
            <form onSubmit={handleSave} style={{display: 'flex', flexDirection: 'column', gap: '12px'}}>
               <div className="input-group">
                  <label style={{fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)'}}>Relay WebSocket URL</label>
                  <input type="text" value={localWsUrl} onChange={e => setLocalWsUrl(e.target.value)} style={{padding: '10px', borderRadius: '6px', border: '1px solid var(--border)', width: '100%'}} />
               </div>
               <button type="submit" className="primary-btn">Save & Reconnect</button>
            </form>
         </div>

         {/* Advanced */}
         <div className="glass-panel">
            <h3 style={{marginBottom: '8px'}}>Advanced & Developer</h3>
            <p style={{fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px'}}>Simulation and experimental features.</p>
            
            <div style={{background: 'var(--bg-color)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border)', marginBottom: '16px'}}>
               <div style={{fontWeight: 600, marginBottom: '8px'}}>System Mode</div>
               <div style={{display: 'flex', gap: '16px'}}>
                  <label style={{display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer'}}>
                     <input type="radio" name="systemMode" checked={!testMode} onChange={() => setTestMode(false)} />
                     <span>REAL HARDWARE (Production)</span>
                  </label>
                  <label style={{display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer'}}>
                     <input type="radio" name="systemMode" checked={testMode} onChange={() => setTestMode(true)} />
                     <span>SIMULATION / SITL</span>
                  </label>
               </div>
            </div>
            
            <label style={{display: 'flex', alignItems: 'center', gap: '12px', background: 'var(--bg-color)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border)', cursor: 'pointer'}}>
               <input type="checkbox" checked={indoorMode} onChange={(e) => setIndoorMode(e.target.checked)} style={{width: '20px', height: '20px'}} />
               <div>
                  <div style={{fontWeight: 600, color: indoorMode ? 'var(--warning)' : 'inherit'}}>Indoor / Bench Test Mode</div>
                  <div style={{fontSize: '12px', color: 'var(--text-muted)'}}>Visually indicates GPS flight is disabled. PX4 still enforces safety.</div>
               </div>
            </label>
            
            <div style={{marginTop: '24px'}}>
               <button className="secondary-btn" onClick={() => window.location.reload()} style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
                  <RefreshCw size={16}/> Force App Reload
               </button>
            </div>
         </div>

      </div>
    </div>
  );
}
