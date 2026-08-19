import React, { useState, useRef } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { ShieldAlert, ShieldCheck, Navigation, ArrowUp, ArrowDown, AlertTriangle, CheckSquare, Square, Radio, Crosshair, ArrowLeft, ArrowRight, RotateCcw, RotateCw } from 'lucide-react';
import { CommandAction } from '../protocol/messages';

export default function FleetView() {
  const { drones, selectedDrones, toggleSelect, selectAll, selectNone, nowMs, sendCommand, isConnected } = useDroneContext();
  const [takeoffAltitude, setTakeoffAltitude] = useState(2.0);
  const [selectedFlightMode, setSelectedFlightMode] = useState("HOLD");
  const [showArmModal, setShowArmModal] = useState(false);
  const [showTakeoffModal, setShowTakeoffModal] = useState(false);
  
  const holdIntervalRef = useRef(null);
  const [holdProgress, setHoldProgress] = useState(0);

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

  return (
    <div className="view-container fade-in">
      <div className="view-header">
         <h2>Fleet Overview</h2>
         <div style={{display: 'flex', gap: '10px'}}>
            <button className="secondary-btn" onClick={selectAll}>Select All</button>
            <button className="secondary-btn" onClick={selectNone}>Clear</button>
         </div>
      </div>
      
      {Object.keys(drones).length === 0 ? (
         <div className="no-drone-msg glass-panel">Listening for drones...</div>
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
                        <div className={`status-badge ${drone.status === 'active' ? 'good-bg' : drone.status === 'OFFLINE' ? 'danger-bg' : 'warning-bg'}`}>
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

      {/* Group Controls */}
      <div className={`glass-panel controls-widget mt-4 ${selectedDrones.size === 0 ? 'disabled-panel' : ''}`}>
         <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
             <h2>Flight Controls <span className="subtitle">{selectedDrones.size} Selected</span></h2>
         </div>
         
         <div className="controls-grid">
            <button className="control-btn arm-btn" onClick={() => setShowArmModal(true)} disabled={selectedDrones.size === 0}>
               <ShieldAlert size={24}/>
               <span>ARM SELECTED</span>
            </button>
            <button className="control-btn disarm-btn" onClick={() => sendCommand(CommandAction.DISARM)} disabled={selectedDrones.size === 0}>
               <ShieldCheck size={24}/>
               <span>DISARM SELECTED</span>
            </button>
            
            <div className="flight-mode-config">
               <label>Flight Mode:</label>
               <div style={{display: 'flex', gap: '8px', width: '100%'}}>
                  <select style={{flex: 1}} value={selectedFlightMode} onChange={(e) => setSelectedFlightMode(e.target.value)}>
                     <option value="HOLD">HOLD</option>
                     <option value="LOITER">LOITER</option>
                     <option value="RTL">RTL</option>
                     <option value="LAND">LAND</option>
                  </select>
                  <button className="primary-btn" style={{padding: '5px 10px', fontSize: '0.8rem'}} onClick={() => sendCommand(CommandAction.SET_MODE, { mode: selectedFlightMode })}>SET</button>
               </div>
            </div>

            <div className="takeoff-config">
               <label>Target Altitude: {takeoffAltitude.toFixed(1)}m</label>
               <input type="range" min="1.0" max="10.0" step="0.5" value={takeoffAltitude} onChange={(e) => setTakeoffAltitude(parseFloat(e.target.value))} />
            </div>
            
            <button className="control-btn takeoff-btn" onClick={() => setShowTakeoffModal(true)} disabled={selectedDrones.size === 0}>
               <ArrowUp size={24}/>
               <span>TAKEOFF SELECTED</span>
            </button>
            
            <button className="control-btn land-btn" onClick={() => sendCommand(CommandAction.LAND)} disabled={selectedDrones.size === 0}>
               <ArrowDown size={24}/>
               <span>LAND SELECTED</span>
            </button>
            
            <button className="control-btn rtl-btn" onClick={() => sendCommand(CommandAction.RTL)} disabled={selectedDrones.size === 0}>
               <Navigation size={24}/>
               <span>RTL SELECTED</span>
            </button>
            
            <button className="control-btn emergency-btn" onDoubleClick={() => sendCommand(CommandAction.EMERGENCY, null, null, true)} disabled={selectedDrones.size === 0}>
               <AlertTriangle size={24}/>
               <span>EMERGENCY SELECTED<br/><small>(Double Tap)</small></span>
            </button>
         </div>
      </div>

      {/* Manual Movement */}
      <div className={`glass-panel movement-widget mt-4 ${selectedDrones.size === 0 ? 'disabled-panel' : ''}`}>
         <h2>Manual Movement <span className="subtitle">(Offboard)</span></h2>
         <div className="joystick-container">
            <div className="d-pad">
               <div></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vx: 2.0})}><ArrowUp/></button>
               <div></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vy: -2.0})}><ArrowLeft/></button>
               <div className="d-center"><Crosshair/></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vy: 2.0})}><ArrowRight/></button>
               <div></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vx: -2.0})}><ArrowDown/></button>
               <div></div>
            </div>
            <div className="z-pad">
               <button className="d-btn z-btn" onClick={() => sendCommand(CommandAction.MOVE, {vz: -1.0})}>Up</button>
               <button className="d-btn z-btn" onClick={() => sendCommand(CommandAction.MOVE, {vz: 1.0})}>Dn</button>
               <button className="d-btn z-btn rot" onClick={() => sendCommand(CommandAction.MOVE, {yaw_rate: -15.0})}><RotateCcw/></button>
               <button className="d-btn z-btn rot" onClick={() => sendCommand(CommandAction.MOVE, {yaw_rate: 15.0})}><RotateCw/></button>
            </div>
         </div>
      </div>

      {/* ARM MODAL */}
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
                       <button 
                          className="control-btn arm-btn press-hold"
                          onMouseDown={() => startHold(() => {setShowArmModal(false); sendCommand(CommandAction.ARM);})}
                          onMouseUp={cancelHold} onMouseLeave={cancelHold}
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
                       <button className="control-btn arm-btn" disabled>ARM DISABLED</button>
                    </>
                 )}
               </div>
               <button className="secondary-btn" style={{marginTop: '10px', width: '100%'}} onClick={() => setShowArmModal(false)}>CANCEL</button>
            </div>
         </div>
      )}

      {/* TAKEOFF MODAL */}
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
                       <button 
                          className="control-btn takeoff-btn press-hold"
                          onMouseDown={() => startHold(() => {setShowTakeoffModal(false); sendCommand(CommandAction.TAKEOFF, { altitude_m: takeoffAltitude });})}
                          onMouseUp={cancelHold} onMouseLeave={cancelHold}
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
