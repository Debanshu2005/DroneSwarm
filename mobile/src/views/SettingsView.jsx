import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { Settings, RefreshCw, Network, Trash, Play, Activity } from 'lucide-react';

export default function SettingsView() {
  const { testMode, setTestMode, indoorMode, setIndoorMode, relayAuthToken, setRelayAuthToken, isConnected, wsManager, drones } = useDroneContext();
  const [newId, setNewId] = useState("drone1");
  const [newIp, setNewIp] = useState("192.168.1.100");
  const [newPort, setNewPort] = useState("8080");

  const handleConnectAll = () => {
     if (!wsManager) return;
     Object.values(wsManager.connections).forEach(c => c.connect());
  };

  const handleDisconnectAll = () => {
     if (!wsManager) return;
     Object.values(wsManager.connections).forEach(c => c.disconnect());
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px'}}>
         <h2>Professional Settings</h2>
      </div>

      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px'}}>
         
         {/* Network Settings */}
         <div className="glass-panel">
            <h3 style={{marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px'}}><Network size={18} /> MULTI-DRONE CONNECTIONS</h3>
            <p style={{fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px'}}>Configure connections to multiple PhoneOS Relays (Swarm).</p>

            <div style={{display: 'flex', gap: '8px', marginBottom: '16px'}}>
               <button className="primary-btn" style={{flex: 1}} onClick={handleConnectAll}>CONNECT ALL</button>
               <button className="secondary-btn" style={{flex: 1, borderColor: 'var(--danger)', color: 'var(--danger)'}} onClick={handleDisconnectAll}>DISCONNECT ALL</button>
            </div>

            <div style={{display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '16px'}}>
               <input
                  type="password"
                  placeholder="OPTIONAL RELAY AUTH TOKEN"
                  value={relayAuthToken}
                  onChange={e => setRelayAuthToken(e.target.value)}
                  style={{padding: '10px', borderRadius: '6px', border: '1px solid var(--border)'}}
               />
               <div style={{display: 'flex', gap: '8px'}}>
                  <input type="text" placeholder="DRONE ID" value={newId} onChange={e => setNewId(e.target.value)} style={{flex: 1, padding: '10px', borderRadius: '6px', border: '1px solid var(--border)'}} />
                  <input type="text" placeholder="IP ADDRESS" value={newIp} onChange={e => setNewIp(e.target.value)} style={{flex: 2, padding: '10px', borderRadius: '6px', border: '1px solid var(--border)'}} />
                  <input type="text" placeholder="PORT" value={newPort} onChange={e => setNewPort(e.target.value)} style={{flex: 1, padding: '10px', borderRadius: '6px', border: '1px solid var(--border)'}} />
               </div>
               <button className="primary-btn" onClick={() => { if(newIp && newPort && wsManager) wsManager.addConnection(newIp, parseInt(newPort)); }}>+ ADD DRONE</button>
            </div>

            <div style={{display: 'flex', flexDirection: 'column', gap: '12px'}}>
               {(!wsManager || Object.keys(wsManager.connections).length === 0) ? (
                  <div style={{fontSize: '12px', color: 'var(--text-muted)'}}>No active relay connections.</div>
               ) : (
                  Object.entries(wsManager.connections).map(([url, conn]) => {
                     const isOnline = conn.ws?.readyState === WebSocket.OPEN;
                     const isTesting = conn.connected && !isOnline;
                     return (
                        <div key={url} style={{background: 'var(--bg-color)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border)'}}>
                           <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px'}}>
                              <div>
                                 <div style={{fontSize: '14px', fontWeight: 'bold'}}>{url}</div>
                                 <div style={{fontSize: '11px', color: 'var(--text-muted)'}}>Protocol: WebSocket</div>
                              </div>
                              <div style={{display: 'flex', flexDirection: 'column', alignItems: 'flex-end'}}>
                                 <span style={{fontSize: '11px', fontWeight: 'bold', color: 'var(--text-muted)'}}>STATUS:</span>
                                 <span style={{fontSize: '13px', fontWeight: 'bold', color: isOnline ? 'var(--success)' : (isTesting ? 'var(--warning)' : 'var(--danger)')}}>
                                    ● {isOnline ? 'ONLINE' : (isTesting ? 'CONNECTING...' : 'OFFLINE')}
                                 </span>
                              </div>
                           </div>
                           <div style={{display: 'flex', gap: '8px'}}>
                              <button className="action-btn" style={{flex: 1, padding: '6px', fontSize: '11px', background: 'var(--primary)', color: '#fff'}} onClick={() => conn.connect()}><Play size={12}/> CONNECT</button>
                              <button className="action-btn" style={{flex: 1, padding: '6px', fontSize: '11px', border: '1px solid var(--border)'}} onClick={() => conn.connect()}><Activity size={12}/> TEST</button>
                              <button className="action-btn" style={{flex: 1, padding: '6px', fontSize: '11px', border: '1px solid var(--danger)', color: 'var(--danger)'}} onClick={() => wsManager.removeConnection(url)}><Trash size={12}/> REMOVE</button>
                           </div>
                        </div>
                     )
                  })
               )}
            </div>
         </div>

         {/* Advanced */}
         <div className="glass-panel">
            <h3 style={{marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px'}}><Settings size={18}/> Advanced & Developer</h3>
            <p style={{fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px'}}>Simulation and experimental features.</p>
            
            <div style={{background: 'var(--bg-color)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border)', marginBottom: '16px'}}>
               <div style={{fontWeight: 600, marginBottom: '8px'}}>System Mode</div>
               <div style={{display: 'flex', flexDirection: 'column', gap: '12px'}}>
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
