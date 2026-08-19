import React, { useState, useRef } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { ShieldAlert, ShieldCheck, Navigation, ArrowUp, ArrowDown, Activity, ArrowLeft, Square, RotateCcw, RotateCw, ArrowRight } from 'lucide-react';
import { CommandAction } from '../protocol/messages';

export default function DroneControlView({ setView }) {
  const { drones, selectedDrones, nowMs, sendCommand, isConnected, indoorMode } = useDroneContext();
  
  const [takeoffAltitude, setTakeoffAltitude] = useState(2.0);
  const [movementSpeed, setMovementSpeed] = useState(2.0);
  const [verticalSpeed, setVerticalSpeed] = useState(1.0);
  const [yawRate, setYawRate] = useState(15.0);
  
  const [showArmModal, setShowArmModal] = useState(false);
  const [showTakeoffModal, setShowTakeoffModal] = useState(false);
  const holdIntervalRef = useRef(null);
  const [holdProgress, setHoldProgress] = useState(0);

  const activeDroneId = selectedDrones.size > 0 ? Array.from(selectedDrones)[0] : null;
  const drone = drones[activeDroneId];

  if (!drone) {
    return (
      <div className="view-container">
         <div className="card" style={{textAlign: 'center', padding: '40px'}}>
            <h3 style={{color: 'var(--text-muted)'}}>NO DRONE SELECTED</h3>
            <button className="btn btn-primary" style={{marginTop: '16px'}} onClick={() => setView('DRONES')}>Back to Fleet</button>
         </div>
      </div>
    );
  }

  const tel = drone.telemetry || {};

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

  const validateDroneSafety = () => {
     const isHeartbeatHealthy = (nowMs - drone.lastSeen) < 2000;
     const isTelemetryHealthy = isHeartbeatHealthy && isConnected === "CONNECTED";
     const isPx4Connected = tel.flight_mode && tel.flight_mode !== "disconnected" && tel.flight_mode !== "UNKNOWN";
     const isBatteryAcceptable = (tel.battery_level || 0) >= 20;
     const isGpsValid = tel.gps_valid === true;
     const isFailsafe = drone.status === 'failsafe';
     const isHealthy = tel.system_health === "OK" || tel.system_health == null;
     
     let reason = "OK";
     let armPass = true;
     
     if (!isTelemetryHealthy) { armPass = false; reason = "LINK DOWN"; }
     else if (!isPx4Connected) { armPass = false; reason = "PX4 DISCONNECTED"; }
     else if (isFailsafe) { armPass = false; reason = "FAILSAFE ACTIVE"; }
     else if (!isHealthy) { armPass = false; reason = "PX4 HEALTH NOT READY"; }
     else if (!isBatteryAcceptable) { armPass = false; reason = "BATTERY LOW"; }
     else if (!indoorMode && !isGpsValid) { armPass = false; reason = "NO GPS FIX (OUTDOOR MODE)"; }
     
     const takeoffPass = armPass && tel.armed_state === "ARMED";
     const takeoffReason = !armPass ? reason : (tel.armed_state !== "ARMED" ? "NOT ARMED" : "OK");
     
     return { armPass, takeoffPass, reason, takeoffReason };
  };

  const safety = validateDroneSafety();

  const renderModals = () => {
    return (
      <>
        {showArmModal && (
           <div className="modal-overlay">
              <div className="modal-content">
                 <h2>ARMING SAFETY GATE</h2>
                 <div className="checklist">
                    <div className="check-item" style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '8px'}}>
                       <span>{drone.id}:</span> 
                       {safety.armPass ? <span className="good">✓ PASS</span> : <span className="danger">✗ {safety.reason}</span>}
                    </div>
                 </div>
                 <div style={{marginTop: '20px', textAlign: 'center'}}>
                   {safety.armPass ? (
                      <>
                         <h3 className="good" style={{marginBottom: '10px'}}>READY TO ARM</h3>
                         <button 
                            className="action-btn action-arm press-hold" style={{width: '100%'}}
                            onMouseDown={() => startHold(() => {setShowArmModal(false); sendCommand(CommandAction.ARM);})}
                            onMouseUp={cancelHold} onMouseLeave={cancelHold}
                            onTouchStart={(e) => { e.preventDefault(); startHold(() => {setShowArmModal(false); sendCommand(CommandAction.ARM);});}}
                            onTouchEnd={(e) => { e.preventDefault(); cancelHold();}}
                         >
                            <div className="progress-bg" style={{width: `${holdProgress}%`}}></div>
                            <span style={{position: 'relative', zIndex: 2}}>HOLD TO ARM</span>
                         </button>
                      </>
                   ) : (
                      <>
                         <h3 className="danger" style={{marginBottom: '10px'}}>ARMING REJECTED</h3>
                         <button className="action-btn" style={{width: '100%'}} disabled>ARM DISABLED</button>
                      </>
                   )}
                 </div>
                 <button className="secondary-btn" style={{marginTop: '10px', width: '100%'}} onClick={() => setShowArmModal(false)}>CANCEL</button>
              </div>
           </div>
        )}

        {showTakeoffModal && (
           <div className="modal-overlay">
              <div className="modal-content">
                 <h2>TAKEOFF CONFIRMATION</h2>
                 <div className="checklist">
                    <div className="check-item" style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '8px', marginBottom: '8px'}}>
                       <span>Target Altitude:</span> <span>{takeoffAltitude.toFixed(1)} m</span>
                    </div>
                    <div className="check-item" style={{display: 'flex', justifyContent: 'space-between'}}>
                       <span>{drone.id}:</span> 
                       {safety.takeoffPass ? <span className="good">✓ READY</span> : <span className="danger">✗ {safety.takeoffReason}</span>}
                    </div>
                 </div>
                 <div style={{marginTop: '20px', textAlign: 'center'}}>
                   {safety.takeoffPass ? (
                      <>
                         <h3 className="good" style={{marginBottom: '10px'}}>READY FOR TAKEOFF</h3>
                         <button 
                            className="action-btn action-arm press-hold" style={{width: '100%', borderColor: 'var(--primary)', color: '#fff', background: 'var(--primary)'}}
                            onMouseDown={() => startHold(() => {setShowTakeoffModal(false); sendCommand(CommandAction.TAKEOFF, { altitude_m: takeoffAltitude });})}
                            onMouseUp={cancelHold} onMouseLeave={cancelHold}
                            onTouchStart={(e) => { e.preventDefault(); startHold(() => {setShowTakeoffModal(false); sendCommand(CommandAction.TAKEOFF, { altitude_m: takeoffAltitude });});}}
                            onTouchEnd={(e) => { e.preventDefault(); cancelHold();}}
                         >
                            <div className="progress-bg" style={{width: `${holdProgress}%`}}></div>
                            <span style={{position: 'relative', zIndex: 2}}>HOLD TO TAKEOFF</span>
                         </button>
                      </>
                   ) : (
                      <>
                         <h3 className="danger" style={{marginBottom: '10px'}}>TAKEOFF REJECTED</h3>
                         <button className="action-btn" style={{width: '100%'}} disabled>TAKEOFF DISABLED</button>
                      </>
                   )}
                 </div>
                 <button className="secondary-btn" style={{marginTop: '10px', width: '100%'}} onClick={() => setShowTakeoffModal(false)}>CANCEL</button>
              </div>
           </div>
        )}
      </>
    );
  };

  return (
    <div className="view-container">
      
      {indoorMode && (
         <div className="card" style={{ background: 'var(--primary)', color: 'white', padding: '12px 16px', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Activity size={20} />
            <div>
               <h4 style={{ margin: 0, fontSize: '14px' }}>INDOOR MODE ACTIVE</h4>
               <p style={{ margin: 0, fontSize: '12px', opacity: 0.9 }}>GPS: NOT REQUIRED FOR SELECTED CONTROL MODE</p>
            </div>
         </div>
      )}

      <div className="view-header" style={{display: 'flex', alignItems: 'center', gap: '12px'}}>
         <button className="btn btn-secondary" style={{padding: '8px'}} onClick={() => setView('DRONES')}>
           <ArrowLeft size={20} />
         </button>
         <div>
            <h2>Control: {drone.id}</h2>
            <div style={{display: 'flex', gap: '12px', marginTop: '8px'}}>
               <button className="btn btn-secondary text-sm" onClick={() => setView('PARAMETERS')} style={{padding: '4px 8px'}}>Parameters</button>
               <button className="btn btn-secondary text-sm" onClick={() => setView('SENSOR_CALIBRATION')} style={{padding: '4px 8px'}}>Sensors</button>
            </div>
         </div>
      </div>

      <div className="metrics-row" style={{marginBottom: 0}}>
         <div className="metric-card">
            <span className="metric-label">Mode</span>
            <span className="metric-value">{tel.flight_mode || 'UNK'}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">Armed</span>
            <span className={`metric-value ${tel.armed_state === 'ARMED' ? 'danger' : 'good'}`}>{tel.armed_state || 'DISARMED'}</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">Battery</span>
            <span className="metric-value">{tel.battery_level ?? '--'}%</span>
         </div>
         <div className="metric-card">
            <span className="metric-label">GPS Fix</span>
            <span className={`metric-value ${tel.gps_valid ? 'good' : 'danger'}`}>{tel.gps_valid ? '3D FIX' : 'NO FIX'}</span>
         </div>
      </div>

      <div className="card" style={{ padding: '20px' }}>
        <h3 style={{marginBottom: '16px', fontSize: '14px', color: 'var(--text-muted)', textTransform: 'uppercase'}}>Quick Actions</h3>
        <div className="quick-actions" style={{gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))'}}>
           <button className="btn btn-primary" onClick={() => setShowArmModal(true)} disabled={drone.status !== 'active'} style={{background: '#ef4444', borderColor: '#ef4444', color: 'white'}}>
              <ShieldAlert size={16}/> ARM
           </button>
           <button className="btn btn-secondary" onClick={() => sendCommand(CommandAction.DISARM)} disabled={drone.status !== 'active'}>
              <ShieldCheck size={16}/> DISARM
           </button>
           <button className="btn btn-secondary" onClick={() => setShowTakeoffModal(true)} disabled={drone.status !== 'active'}>
              <ArrowUp size={16}/> TAKEOFF
           </button>
           <button className="btn btn-secondary" onClick={() => sendCommand(CommandAction.LAND)} disabled={drone.status !== 'active'}>
              <ArrowDown size={16}/> LAND
           </button>
           <button className="btn btn-secondary" onClick={() => sendCommand(CommandAction.RTL)} disabled={drone.status !== 'active'}>
              <Navigation size={16}/> RTL
           </button>
           <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <select className="input-field" style={{ width: '100%', padding: '8px', fontSize: '12px' }} onChange={(e) => sendCommand(CommandAction.SET_MODE, {mode: e.target.value})}>
                 <option value="">Set Mode...</option>
                 <option value="HOLD">HOLD</option>
                 <option value="LOITER">LOITER</option>
                 <option value="OFFBOARD">OFFBOARD</option>
                 <option value="MANUAL">MANUAL</option>
              </select>
           </div>
        </div>
      </div>

      <div className="card" style={{ padding: '20px' }}>
         <h3 style={{marginBottom: '16px', fontSize: '14px', color: 'var(--text-muted)', textTransform: 'uppercase'}}>Manual Control (RC)</h3>
         
         <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginBottom: '24px'}}>
            <div className="input-group">
               <label style={{fontSize: '12px', color: 'var(--text-muted)'}}>XY Speed: {movementSpeed.toFixed(1)}m/s</label>
               <input type="range" min="0.5" max="10.0" step="0.5" value={movementSpeed} onChange={(e) => setMovementSpeed(parseFloat(e.target.value))} />
            </div>
            <div className="input-group">
               <label style={{fontSize: '12px', color: 'var(--text-muted)'}}>Z Speed: {verticalSpeed.toFixed(1)}m/s</label>
               <input type="range" min="0.5" max="5.0" step="0.5" value={verticalSpeed} onChange={(e) => setVerticalSpeed(parseFloat(e.target.value))} />
            </div>
            <div className="input-group">
               <label style={{fontSize: '12px', color: 'var(--text-muted)'}}>Yaw Rate: {yawRate.toFixed(1)}°/s</label>
               <input type="range" min="5" max="90" step="5" value={yawRate} onChange={(e) => setYawRate(parseFloat(e.target.value))} />
            </div>
         </div>

         <div className="joystick-container" style={{background: 'var(--bg-main)', padding: '24px', borderRadius: '12px', display: 'flex', justifyContent: 'center', gap: '40px'}}>
            <div className="d-pad">
               <div></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vx: movementSpeed})}><ArrowUp/></button>
               <div></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vy: -movementSpeed})}><ArrowLeft/></button>
               <div className="d-center" onClick={() => sendCommand(CommandAction.HOVER)} title="Hover/Stop"><Square size={20}/></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vy: movementSpeed})}><ArrowRight/></button>
               <div></div>
               <button className="d-btn" onClick={() => sendCommand(CommandAction.MOVE, {vx: -movementSpeed})}><ArrowDown/></button>
               <div></div>
            </div>
            <div className="z-pad">
               <button className="d-btn z-btn" onClick={() => sendCommand(CommandAction.MOVE, {vz: -verticalSpeed})}>Up</button>
               <button className="d-btn z-btn" onClick={() => sendCommand(CommandAction.MOVE, {vz: verticalSpeed})}>Dn</button>
               <button className="d-btn z-btn rot" onClick={() => sendCommand(CommandAction.MOVE, {yaw_rate: -yawRate})}><RotateCcw/></button>
               <button className="d-btn z-btn rot" onClick={() => sendCommand(CommandAction.MOVE, {yaw_rate: yawRate})}><RotateCw/></button>
            </div>
         </div>
      </div>
      
      {renderModals()}
    </div>
  );
}
