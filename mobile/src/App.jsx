import React, { useState, useEffect, useRef } from 'react';
import { WebSocketManager } from './networking/WebSocketManager';
import { MessageType, CommandAction, ControlMessage, HeartbeatMessage } from './protocol/messages';
import { ShieldAlert, ShieldCheck, Settings, Navigation, AlertTriangle, BatteryWarning, BatteryFull, Crosshair, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, RotateCcw, RotateCw, Activity, CheckSquare, Square, Radio } from 'lucide-react';
import './App.css';

const GS_ID = "gs_mobile_01";

function App() {
  const [wsManager, setWsManager] = useState(null);
  const [isConnected, setIsConnected] = useState("DISCONNECTED");
  const [drones, setDrones] = useState({});
  const [selectedDrones, setSelectedDrones] = useState(new Set());
  const [settingsOpen, setSettingsOpen] = useState(false);
  
  const [wsUrl, setWsUrl] = useState(() => localStorage.getItem("PhoneOS_WsUrl") || "ws://swarmos-pi.local:8080");
  const [testResult, setTestResult] = useState("");
  const [takeoffAltitude, setTakeoffAltitude] = useState(2.0);
  const [selectedFlightMode, setSelectedFlightMode] = useState("HOLD");
  const [testMode, setTestMode] = useState(() => localStorage.getItem("PhoneOS_TestMode") === "true");
  
  const [showArmModal, setShowArmModal] = useState(false);
  const [showTakeoffModal, setShowTakeoffModal] = useState(false);
  
  const holdIntervalRef = useRef(null);
  const [holdProgress, setHoldProgress] = useState(0);

  // Time tracker for stale checks
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 100);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    localStorage.setItem("PhoneOS_WsUrl", wsUrl);
    localStorage.setItem("PhoneOS_TestMode", testMode);
  }, [wsUrl, testMode]);
  
  // Drone cleanup task & demo mode
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      
      if (testMode) {
        setDrones(prev => ({
          ...prev,
          "drone_test_01": {
            id: "drone_test_01", status: "active", lastSeen: now,
            telemetry: { armed_state: "DISARMED", flight_mode: "HOLD", battery_level: 85, gps_valid: true, altitude: 0.0, ground_speed: 0.0, satellites: 12, hdop: 0.8 },
            commandState: prev["drone_test_01"]?.commandState || { action: null, state: 'IDLE', cmd_id: null }
          },
          "drone_test_02": {
            id: "drone_test_02", status: "active", lastSeen: now,
            telemetry: { armed_state: "ARMED", flight_mode: "LOITER", battery_level: 45, gps_valid: true, altitude: 2.5, ground_speed: 1.2, satellites: 14, hdop: 0.7 },
            commandState: prev["drone_test_02"]?.commandState || { action: null, state: 'IDLE', cmd_id: null }
          },
          "drone_test_03": {
            id: "drone_test_03", status: "failsafe", lastSeen: now - 3000,
            telemetry: { armed_state: "ARMED", flight_mode: "RTL", battery_level: 15, gps_valid: false, altitude: 15.0, ground_speed: 5.0, satellites: 3, hdop: 3.5 },
            commandState: prev["drone_test_03"]?.commandState || { action: null, state: 'IDLE', cmd_id: null }
          }
        }));
        if (isConnected !== "CONNECTED") setIsConnected("CONNECTED");
      }
      
      setDrones(prev => {
        let changed = false;
        const newDrones = { ...prev };
        for (const [id, drone] of Object.entries(newDrones)) {
          if (now - drone.lastSeen > 5000 && drone.status !== "OFFLINE" && !id.startsWith("drone_test")) {
            newDrones[id] = { ...drone, status: "OFFLINE" };
            changed = true;
          }
        }
        return changed ? newDrones : prev;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, [testMode, isConnected]);

  // Network Initialization
  useEffect(() => {
    const manager = new WebSocketManager(wsUrl);
    manager.onConnectionChange = (status) => setIsConnected(status);
    
    manager.subscribe(MessageType.HEARTBEAT, (msg) => {
      if (msg.sender_id && msg.sender_id.startsWith("drone")) {
        setDrones(prev => ({
          ...prev,
          [msg.sender_id]: {
            ...prev[msg.sender_id],
            id: msg.sender_id,
            status: msg.status,
            lastSeen: Date.now(),
            commandState: prev[msg.sender_id]?.commandState || { action: null, state: 'IDLE', cmd_id: null }
          }
        }));
      }
    });

    manager.subscribe(MessageType.TELEMETRY, (msg) => {
      if (msg.sender_id && msg.sender_id.startsWith("drone")) {
        setDrones(prev => ({
          ...prev,
          [msg.sender_id]: {
            ...prev[msg.sender_id],
            id: msg.sender_id,
            telemetry: msg.telemetry,
            lastSeen: Date.now(),
            commandState: prev[msg.sender_id]?.commandState || { action: null, state: 'IDLE', cmd_id: null }
          }
        }));
      }
    });

    manager.subscribe(MessageType.STATUS, (msg) => {
       console.log(`STATUS from ${msg.sender_id}: ${msg.status_text}`);
       // Match by command_id if possible, or fallback to sender_id
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                commandState: { ...drone.commandState, state: 'ACCEPTED' }
             }
          };
       });
    });

    manager.subscribe(MessageType.ERROR, (msg) => {
       console.error(`ERROR from ${msg.sender_id}: ${msg.error_msg}`);
       setDrones(prev => {
          const drone = prev[msg.sender_id];
          if (!drone) return prev;
          return {
             ...prev,
             [msg.sender_id]: {
                ...drone,
                commandState: { ...drone.commandState, state: 'REJECTED' }
             }
          };
       });
    });

    manager.connect();
    setWsManager(manager);

    const hbInterval = setInterval(() => {
      if (manager.connected) manager.send(new HeartbeatMessage(GS_ID, null, "active"));
    }, 1000);

    return () => {
      clearInterval(hbInterval);
      if (manager.ws) manager.ws.close();
    };
  }, [wsUrl]);

  const sendCommand = (action, params = null, isEmergency = false) => {
    if (!wsManager || isConnected !== "CONNECTED") return;
    if (selectedDrones.size === 0) return;
    
    selectedDrones.forEach(id => {
       const cmd_id = `cmd_${Date.now()}_${id}`;
       setDrones(prev => ({
          ...prev,
          [id]: {
             ...prev[id],
             commandState: { action, state: 'SENDING', cmd_id }
          }
       }));
       
       if (isEmergency) {
          // Send specific emergency stop command based on action name
          wsManager.send(new ControlMessage(GS_ID, CommandAction.EMERGENCY, params, id, cmd_id));
       } else {
          wsManager.send(new ControlMessage(GS_ID, action, params, id, cmd_id));
       }
    });
  };

  const handleReconnect = (e) => {
    e.preventDefault();
    if (wsManager) wsManager.disconnect();
    window.location.reload();
  };

  const testConnection = () => {
    setTestResult("CONNECTING...");
    let testWs = null;
    try { testWs = new WebSocket(wsUrl); } catch (e) { setTestResult("CONNECTION ERROR: " + e.message); return; }
    
    const timeout = setTimeout(() => {
      if (testWs.readyState !== WebSocket.OPEN) { testWs.close(); setTestResult("CONNECTION ERROR: Timeout"); }
    }, 5000);

    testWs.onopen = () => { clearTimeout(timeout); setTestResult("CONNECTED: Relay reached successfully!"); testWs.close(); };
    testWs.onerror = () => { clearTimeout(timeout); setTestResult("CONNECTION ERROR: Failed to reach Relay"); };
  };

  const toggleSelect = (id) => {
     const newSet = new Set(selectedDrones);
     if (newSet.has(id)) newSet.delete(id);
     else newSet.add(id);
     setSelectedDrones(newSet);
  };
  
  const selectAll = () => {
     setSelectedDrones(new Set(Object.keys(drones)));
  };
  
  const selectNone = () => {
     setSelectedDrones(new Set());
  };

  const startHold = (action) => {
     setHoldProgress(0);
     let progress = 0;
     holdIntervalRef.current = setInterval(() => {
        progress += 5;
        setHoldProgress(progress);
        if (progress >= 100) {
           clearInterval(holdIntervalRef.current);
           action();
           setHoldProgress(0);
        }
     }, 50);
  };
  
  const cancelHold = () => {
     if (holdIntervalRef.current) clearInterval(holdIntervalRef.current);
     setHoldProgress(0);
  };

  // Pre-flight validation logic
  const validateDroneSafety = (drone) => {
     const tel = drone.telemetry || {};
     const isHeartbeatHealthy = (nowMs - drone.lastSeen) < 2000;
     const isTelemetryHealthy = isHeartbeatHealthy && isConnected === "CONNECTED";
     const isPx4Connected = tel.flight_mode && tel.flight_mode !== "disconnected";
     const isBatteryAcceptable = (tel.battery_level || 0) >= 20;
     const isGpsValid = tel.gps_valid === true;
     const isModeValid = tel.flight_mode && tel.flight_mode !== "UNKNOWN";
     
     const armPass = isPx4Connected && isTelemetryHealthy && isBatteryAcceptable && isGpsValid && isModeValid && isHeartbeatHealthy;
     const takeoffPass = armPass && tel.armed_state === "ARMED";
     
     return { armPass, takeoffPass, reason: armPass ? "OK" : "Safety checks failed" };
  };

  const selectedSafetyStates = Array.from(selectedDrones).map(id => {
     const drone = drones[id];
     if (!drone) return { id, armPass: false, takeoffPass: false, reason: "Unknown Drone" };
     return { id, ...validateDroneSafety(drone) };
  });
  
  const groupArmPass = selectedSafetyStates.length > 0 && selectedSafetyStates.every(s => s.armPass);
  const groupTakeoffPass = selectedSafetyStates.length > 0 && selectedSafetyStates.every(s => s.takeoffPass);

  const numOnline = Object.values(drones).filter(d => d.status === "active").length;
  const numWarning = Object.values(drones).filter(d => d.status === "failsafe").length;
  const numOffline = Object.values(drones).filter(d => d.status === "OFFLINE").length;
  
  return (
    <div className="app-container">
      <div className="bg-blob blob-1"></div>
      <div className="bg-blob blob-2"></div>
      
      <header className="glass-panel top-bar">
        <div className="logo-section">
          <h1>SwarmOS <span className="highlight">Fleet</span></h1>
        </div>
        <div className="status-section">
          <div className="connection-pill">
            <div className={`indicator ${isConnected === "CONNECTED" ? "connected" : "disconnected"}`}></div>
            <span>{isConnected}</span>
          </div>
          <button className="icon-btn" onClick={() => setSettingsOpen(!settingsOpen)}>
            <Settings size={20} />
          </button>
        </div>
      </header>

      {testMode && <div className="test-mode-banner">[ DEMO / TEST MODE ]</div>}

      <div className="glass-panel diagnostics-panel">
          <div className="diag-item">
             <span className="diag-label">Relay</span> 
             <span className={`diag-val ${isConnected === 'CONNECTED' ? 'good' : 'danger'}`}>{isConnected}</span>
          </div>
          <div className="diag-item">
             <span className="diag-label">Drones</span> 
             <span className="diag-val">
               {numOnline} <span className="good">ON</span> | {numWarning} <span className="warning">WARN</span> | {numOffline} <span className="danger">OFF</span>
             </span>
          </div>
          <div className="diag-item">
             <span className="diag-label">Selected</span> 
             <span className="diag-val">{selectedDrones.size} / {Object.keys(drones).length}</span>
          </div>
      </div>

      {settingsOpen && (
        <div className="glass-panel settings-panel slide-down">
          <h3>Network Settings</h3>
          <form onSubmit={handleReconnect} className="settings-form">
            <div className="input-group">
              <label>Relay WebSocket URL</label>
              <input type="text" value={wsUrl} onChange={e => setWsUrl(e.target.value)} />
            </div>
            <button type="submit" className="primary-btn">Save & Reconnect</button>
          </form>
          <div className="test-conn-section mt-4">
             <button type="button" className="secondary-btn" onClick={testConnection}>
                <Activity size={16}/> Test Connection
             </button>
             {testResult && <div className={`test-result mt-2 ${testResult.includes('ERROR') ? 'danger' : 'good'}`}>{testResult}</div>}
          </div>
          <div className="test-mode-toggle mt-4">
            <label style={{display: 'flex', alignItems: 'center', gap: '10px'}}>
              <input type="checkbox" checked={testMode} onChange={(e) => setTestMode(e.target.checked)} />
              Enable Demo/Test Mode
            </label>
          </div>
        </div>
      )}

      <main className="main-content">
        
        {/* DRONE FLEET PANEL */}
        <section className="glass-panel dashboard-widget fleet-widget fade-in">
           <div className="fleet-header" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px'}}>
              <h2>Fleet Overview</h2>
              <div style={{display: 'flex', gap: '10px'}}>
                 <button className="secondary-btn" onClick={selectAll} style={{padding: '5px 10px', fontSize: '0.8rem'}}>Select All</button>
                 <button className="secondary-btn" onClick={selectNone} style={{padding: '5px 10px', fontSize: '0.8rem'}}>Clear</button>
              </div>
           </div>
           
           {Object.keys(drones).length === 0 ? (
              <div className="no-drone-msg">Listening for drones...</div>
           ) : (
              <div className="fleet-grid">
                 {Object.values(drones).map(drone => {
                    const tel = drone.telemetry || {};
                    const batt = tel.battery_level || 0;
                    const armed = tel.armed_state === "ARMED";
                    const isSelected = selectedDrones.has(drone.id);
                    const age = ((nowMs - drone.lastSeen) / 1000).toFixed(1);
                    
                    let cardClass = "drone-card";
                    if (isSelected) cardClass += " selected";
                    if (drone.status === "failsafe") cardClass += " warning-state";
                    if (drone.status === "OFFLINE") cardClass += " offline-state";
                    
                    return (
                       <div key={drone.id} className={cardClass} onClick={() => toggleSelect(drone.id)}>
                          <div className="drone-card-header">
                             <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
                                {isSelected ? <CheckSquare size={18} className="primary-text"/> : <Square size={18}/>}
                                <span>{drone.id}</span>
                             </div>
                             <div className={`status-badge ${drone.status === 'active' ? 'good' : drone.status === 'OFFLINE' ? 'danger' : 'warning'}`}>
                                <Radio size={14}/> {drone.status.toUpperCase()}
                             </div>
                          </div>
                          
                          <div className="drone-card-telemetry">
                             <div className="tc-row">
                                <span className={armed ? 'danger' : 'good'}>{armed ? 'ARMED' : 'DISARMED'}</span>
                                <span>{tel.flight_mode || 'UNK'}</span>
                             </div>
                             <div className="tc-row">
                                <span className={batt < 20 ? 'danger' : 'good'}>BAT: {batt.toFixed(0)}%</span>
                                <span className={tel.gps_valid ? 'good' : 'danger'}>{tel.gps_valid ? `3D FIX (${tel.satellites})` : 'NO FIX'}</span>
                             </div>
                             <div className="tc-row">
                                <span>ALT: {tel.altitude != null ? tel.altitude.toFixed(1) : '--'}m</span>
                                <span>SPD: {tel.ground_speed != null ? tel.ground_speed.toFixed(1) : '--'}m/s</span>
                             </div>
                          </div>
                          
                          {drone.commandState && drone.commandState.state !== 'IDLE' && (
                             <div className={`cmd-status ${drone.commandState.state.toLowerCase()}`}>
                                {drone.commandState.action}: {drone.commandState.state}
                             </div>
                          )}
                          <div style={{fontSize: '0.65rem', color: '#888', textAlign: 'right'}}>
                             Age: {age}s
                          </div>
                       </div>
                    );
                 })}
              </div>
           )}
        </section>

        {/* GROUP FLIGHT CONTROLS */}
        <section className="glass-panel dashboard-widget controls-widget slide-up" style={{opacity: selectedDrones.size > 0 ? 1 : 0.4, pointerEvents: selectedDrones.size > 0 ? 'auto' : 'none'}}>
           <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
             <h2>Group Controls <span className="subtitle">{selectedDrones.size} Selected</span></h2>
           </div>
           
          <div className="controls-grid">
             <button className="control-btn arm-btn" onClick={() => setShowArmModal(true)}>
               <ShieldAlert size={24}/>
               <span>ARM SELECTED</span>
             </button>
             <button className="control-btn disarm-btn" onClick={() => sendCommand(CommandAction.DISARM)}>
               <ShieldCheck size={24}/>
               <span>DISARM SELECTED</span>
             </button>
             
             <div className="flight-mode-config">
                <label>Group Flight Mode:</label>
                <div style={{display: 'flex', gap: '8px', width: '100%'}}>
                   <select style={{flex: 1}} value={selectedFlightMode} onChange={(e) => setSelectedFlightMode(e.target.value)}>
                       <option value="HOLD">HOLD</option>
                       <option value="LOITER">LOITER</option>
                       <option value="RTL">RTL</option>
                       <option value="LAND">LAND</option>
                   </select>
                   <button className="primary-btn" style={{padding: '5px 10px', fontSize: '0.8rem'}} onClick={() => {
                       sendCommand(CommandAction.SET_MODE, { mode: selectedFlightMode });
                   }}>SET</button>
                </div>
             </div>

             <div className="takeoff-config">
                <label>Target Altitude: {takeoffAltitude.toFixed(1)}m</label>
                <input type="range" min="1.0" max="5.0" step="0.5" value={takeoffAltitude} onChange={(e) => setTakeoffAltitude(parseFloat(e.target.value))} />
             </div>
             
             <button className="control-btn takeoff-btn" onClick={() => setShowTakeoffModal(true)}>
               <ArrowUp size={24}/>
               <span>TAKEOFF SELECTED</span>
             </button>
             
             <button className="control-btn land-btn" onClick={() => sendCommand(CommandAction.LAND)}>
               <ArrowDown size={24}/>
               <span>LAND SELECTED</span>
             </button>
             
             <button className="control-btn rtl-btn" onClick={() => sendCommand(CommandAction.RTL)}>
               <Navigation size={24}/>
               <span>RTL SELECTED</span>
             </button>
             
             <button className="control-btn emergency-btn" onDoubleClick={() => sendCommand(CommandAction.EMERGENCY, null, true)}>
               <AlertTriangle size={24}/>
               <span>EMERGENCY SELECTED<br/><small>(Double Tap)</small></span>
             </button>
          </div>
        </section>

      </main>

      {/* ARM SAFETY GATE MODAL */}
      {showArmModal && (
         <div className="modal-overlay">
            <div className="modal-content glass-panel">
               <h2>ARMING SAFETY GATE</h2>
               <div className="checklist" style={{maxHeight: '40vh', overflowY: 'auto'}}>
                  {selectedSafetyStates.map(s => (
                     <div key={s.id} className="check-item" style={{borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '5px', marginBottom: '5px'}}>
                        <span>{s.id}:</span> 
                        {s.armPass ? <span className="good">✓ PASS</span> : <span className="danger">✗ FAIL</span>}
                     </div>
                  ))}
               </div>
               
               <div style={{marginTop: '20px', textAlign: 'center'}}>
                 {groupArmPass ? (
                    <>
                       <h3 className="good">READY TO ARM</h3>
                       <p style={{fontSize: '0.8rem'}}>Press and hold to ARM all selected</p>
                       <button 
                          className="control-btn arm-btn press-hold"
                          onMouseDown={() => startHold(() => {setShowArmModal(false); sendCommand(CommandAction.ARM);})}
                          onMouseUp={cancelHold}
                          onMouseLeave={cancelHold}
                          onTouchStart={(e) => { e.preventDefault(); startHold(() => {setShowArmModal(false); sendCommand(CommandAction.ARM);});}}
                          onTouchEnd={(e) => { e.preventDefault(); cancelHold();}}
                       >
                          <div className="progress-bg" style={{width: `${holdProgress}%`}}></div>
                          <span style={{position: 'relative', zIndex: 2}}>HOLD TO ARM SELECTED</span>
                       </button>
                    </>
                 ) : (
                    <>
                       <h3 className="danger">ARMING REJECTED</h3>
                       <p style={{fontSize: '0.8rem'}}>Not all selected drones pass safety checks.</p>
                       <button className="control-btn arm-btn" disabled>ARM DISABLED</button>
                    </>
                 )}
               </div>
               <button className="secondary-btn" style={{marginTop: '10px', width: '100%'}} onClick={() => setShowArmModal(false)}>CANCEL</button>
            </div>
         </div>
      )}

      {/* TAKEOFF SAFETY MODAL */}
      {showTakeoffModal && (
         <div className="modal-overlay">
            <div className="modal-content glass-panel">
               <h2>TAKEOFF CONFIRMATION</h2>
               <div className="checklist" style={{maxHeight: '40vh', overflowY: 'auto'}}>
                  <div className="check-item" style={{marginBottom: '10px', fontWeight: 'bold'}}>
                     <span>Target Altitude:</span> <span>{takeoffAltitude.toFixed(1)} m</span>
                  </div>
                  {selectedSafetyStates.map(s => (
                     <div key={s.id} className="check-item" style={{borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '5px', marginBottom: '5px'}}>
                        <span>{s.id}:</span> 
                        {s.takeoffPass ? <span className="good">✓ READY</span> : <span className="danger">✗ NOT READY (Must be armed)</span>}
                     </div>
                  ))}
               </div>
               
               <div style={{marginTop: '20px', textAlign: 'center'}}>
                 {groupTakeoffPass ? (
                    <>
                       <h3 className="good">READY FOR TAKEOFF</h3>
                       <p style={{fontSize: '0.8rem'}}>Press and hold to TAKEOFF all selected</p>
                       <button 
                          className="control-btn takeoff-btn press-hold"
                          onMouseDown={() => startHold(() => {setShowTakeoffModal(false); sendCommand(CommandAction.TAKEOFF, { altitude_m: takeoffAltitude });})}
                          onMouseUp={cancelHold}
                          onMouseLeave={cancelHold}
                          onTouchStart={(e) => { e.preventDefault(); startHold(() => {setShowTakeoffModal(false); sendCommand(CommandAction.TAKEOFF, { altitude_m: takeoffAltitude });});}}
                          onTouchEnd={(e) => { e.preventDefault(); cancelHold();}}
                       >
                          <div className="progress-bg" style={{width: `${holdProgress}%`}}></div>
                          <span style={{position: 'relative', zIndex: 2}}>HOLD TO TAKEOFF SELECTED</span>
                       </button>
                    </>
                 ) : (
                    <>
                       <h3 className="danger">TAKEOFF REJECTED</h3>
                       <p style={{fontSize: '0.8rem'}}>All selected drones must be ARMED.</p>
                       <button className="control-btn takeoff-btn" disabled>TAKEOFF DISABLED</button>
                    </>
                 )}
               </div>
               <button className="secondary-btn" style={{marginTop: '10px', width: '100%'}} onClick={() => setShowTakeoffModal(false)}>CANCEL</button>
            </div>
         </div>
      )}

    </div>
  );
}

export default App;
