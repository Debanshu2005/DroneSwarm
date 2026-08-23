import React, { useState, useRef } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { ShieldAlert, ShieldCheck, Navigation, ArrowUp, ArrowDown, Activity, Signal } from 'lucide-react';
import { CommandAction } from '../protocol/messages';
import { evaluatePreflightChecklist } from '../utils/DroneHealth';

export default function DashboardView() {
  const { drones, selectedDrones, nowMs, sendCommand, isConnected, indoorMode } = useDroneContext();
  const [takeoffAltitude, setTakeoffAltitude] = useState(2.0);
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
     const tel = drone?.telemetry || {};
     const isHeartbeatHealthy = drone && (nowMs - drone.lastSeen) < 4000;
     const isTelemetryHealthy = isHeartbeatHealthy && isConnected === "CONNECTED";
     const isPx4Connected = tel.flight_mode && tel.flight_mode !== "disconnected" && tel.flight_mode !== "UNKNOWN";
     const isFailsafe = drone?.status === 'failsafe';
     const isTelemetryStale = tel.heartbeat_age != null && tel.heartbeat_age > 2.0;
     
     let reason = "OK";
     let armPass = true;
     
     if (!isTelemetryHealthy) { armPass = false; reason = "LINK DOWN"; }
     else if (!isPx4Connected) { armPass = false; reason = "PX4 DISCONNECTED"; }
     else if (isTelemetryStale) { armPass = false; reason = "TELEMETRY STALE"; }
     else if (isFailsafe) { armPass = false; reason = "FAILSAFE ACTIVE"; }

     
     const takeoffPass = armPass && tel.armed_state === "ARMED";
     const takeoffReason = !armPass ? reason : (tel.armed_state !== "ARMED" ? "NOT ARMED" : "OK");
     
     return { armPass, takeoffPass, reason, takeoffReason };
  };

  const droneCount = Object.keys(drones).length;
  const activeDrones = Object.values(drones).filter(d => d.status === 'CONNECTED').length;
  const armedDrones = Object.values(drones).filter(d => d.telemetry?.armed_state === 'ARMED').length;
  const warningDrones = Object.values(drones).filter(d => d.status === 'DEGRADED' || d.healthScore === 'WARNING' || d.healthScore === 'CRITICAL').length;

  // For the dashboard, we look at the first selected drone, or the first available drone
  const activeDroneId = selectedDrones.size > 0 ? Array.from(selectedDrones)[0] : Object.keys(drones)[0];
  const drone = drones[activeDroneId];
  const tel = drone?.telemetry || {};
  const safety = validateDroneSafety(drone);

  const renderModals = () => {
    if (!drone) return null;
    return (
      <>
        {/* ARM MODAL */}
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

        {/* TAKEOFF MODAL */}
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
    <div style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
      
      {/* Fleet Summary */}
      <div className="metrics-row">
        <div className="metric-card">
          <span className="metric-label">Total Fleet</span>
          <span className="metric-value">{droneCount}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Online</span>
          <span className="metric-value good">{activeDrones}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Armed</span>
          <span className="metric-value danger">{armedDrones}</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Warnings</span>
          <span className="metric-value warning">{warningDrones}</span>
        </div>
      </div>

      {!drone ? (
        <div className="glass-panel" style={{textAlign: 'center', padding: '40px'}}>
           <h3 style={{color: 'var(--text-muted)'}}>NO DRONES CONNECTED</h3>
           <p style={{marginTop: '10px', color: 'var(--text-muted)'}}>Waiting for heartbeat...</p>
        </div>
      ) : (
        <>
          {/* Selected Drone Status */}
          <div className="glass-panel" style={{display: 'flex', flexDirection: 'column', gap: '16px'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
               <div style={{display: 'flex', alignItems: 'center', gap: '12px'}}>
                 <h2 style={{fontSize: '20px'}}>{drone.id}</h2>
                 <span className={`status-badge badge-${drone.healthScore === 'HEALTHY' ? 'good' : drone.healthScore === 'WARNING' ? 'warning' : drone.healthScore === 'CRITICAL' ? 'danger' : 'neutral'}`}>
                   ● {drone.healthScore || 'UNKNOWN'}
                 </span>
                 <span className={`status-badge badge-${drone.freshness === 'LIVE' ? 'good' : drone.freshness === 'STALE' ? 'warning' : 'danger'}`}>
                   <Signal size={12} style={{marginRight: 4}}/> {drone.freshness || 'OFFLINE'}
                 </span>
                 <span className={`status-badge badge-${drone.status === 'CONNECTED' ? 'good' : drone.status === 'DEGRADED' ? 'warning' : 'danger'}`}>
                   ● {(drone.status || 'UNKNOWN').toUpperCase()}
                 </span>
               </div>
               {drone.commandState && drone.commandState.state !== 'IDLE' && (
                 <span className="text-small" style={{fontWeight: 600, color: 'var(--warning)'}}>
                   CMD: {drone.commandState.action} ({drone.commandState.state})
                 </span>
               )}
            </div>

            <div className="metrics-row" style={{marginBottom: 0, gap: '8px'}}>
               <div className="metric-card" style={{padding: '8px', background: 'var(--bg-color)', border: 'none'}}>
                  <span className="metric-label">Mode</span>
                  <span className="metric-value" style={{fontSize: '16px'}}>{tel.flight_mode || 'UNK'}</span>
               </div>
               <div className="metric-card" style={{padding: '8px', background: 'var(--bg-color)', border: 'none'}}>
                  <span className="metric-label">Armed</span>
                  <span className={`metric-value ${tel.armed_state === 'ARMED' ? 'danger' : 'good'}`} style={{fontSize: '16px'}}>{tel.armed_state || 'DISARMED'}</span>
               </div>
               <div className="metric-card" style={{padding: '8px', background: 'var(--bg-color)', border: 'none'}}>
                  <span className="metric-label">Battery</span>
                  <span className="metric-value" style={{fontSize: '16px'}}>{tel.battery_level ?? '--'}%</span>
               </div>
               <div className="metric-card" style={{padding: '8px', background: 'var(--bg-color)', border: 'none'}}>
                  <span className="metric-label">GPS</span>
                  <span className="metric-value" style={{fontSize: '16px'}}>{tel.gps_valid ? '3D FIX' : 'NO FIX'}</span>
               </div>
               <div className="metric-card" style={{padding: '8px', background: 'var(--bg-color)', border: 'none'}}>
                  <span className="metric-label">Alt</span>
                  <span className="metric-value" style={{fontSize: '16px'}}>{tel.altitude != null ? tel.altitude.toFixed(1) : '--'}m</span>
               </div>
               <div className="metric-card" style={{padding: '8px', background: 'var(--bg-color)', border: 'none'}}>
                  <span className="metric-label">Speed</span>
                  <span className="metric-value" style={{fontSize: '16px'}}>{tel.ground_speed != null ? tel.ground_speed.toFixed(1) : '--'}m/s</span>
               </div>
            </div>
          </div>

          {/* Quick Actions */}
          <div className="glass-panel">
            <h3 style={{marginBottom: '16px'}}>QUICK ACTIONS</h3>
            
            <div style={{display: 'flex', gap: '8px', marginBottom: '12px', alignItems: 'center'}}>
               <span style={{fontSize: '13px', fontWeight: 600}}>Alt: {takeoffAltitude.toFixed(1)}m</span>
               <input type="range" min="1.0" max="10.0" step="0.5" value={takeoffAltitude} onChange={(e) => setTakeoffAltitude(parseFloat(e.target.value))} style={{flex: 1}} />
            </div>

            <div className="quick-actions">
               <button className="action-btn action-arm" onClick={() => setShowArmModal(true)} disabled={drone.status !== 'CONNECTED' && drone.status !== 'DEGRADED'}>
                  <ShieldAlert size={16}/> ARM
               </button>
               <button className="action-btn action-disarm" onClick={() => sendCommand(CommandAction.DISARM)} disabled={drone.status !== 'CONNECTED' && drone.status !== 'DEGRADED'}>
                  <ShieldCheck size={16}/> DISARM
               </button>
               
               <button className="action-btn" onClick={() => setShowTakeoffModal(true)} disabled={drone.status !== 'CONNECTED' && drone.status !== 'DEGRADED'}>
                  <ArrowUp size={16}/> TAKEOFF
               </button>
               <button className="action-btn" onClick={() => sendCommand(CommandAction.LAND)} disabled={drone.status !== 'CONNECTED' && drone.status !== 'DEGRADED'}>
                  <ArrowDown size={16}/> LAND
               </button>
               
               <button className="action-btn" onClick={() => sendCommand(CommandAction.RTL)} disabled={drone.status !== 'CONNECTED' && drone.status !== 'DEGRADED'}>
                  <Navigation size={16}/> RTL
               </button>
               <button className="action-btn" onClick={() => sendCommand(CommandAction.SET_MODE, {mode: 'HOLD'})} disabled={drone.status !== 'CONNECTED' && drone.status !== 'DEGRADED'}>
                  <Activity size={16}/> HOLD
               </button>
            </div>
          </div>

          {/* Telemetry & Health Container */}
          <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px'}}>
             <div className="glass-panel">
                <h3 style={{marginBottom: '16px'}}>TELEMETRY</h3>
                <div className="kv-list">
                   <div className="kv-row"><span className="kv-label">Altitude</span><span className="kv-value">{tel.altitude != null ? `${tel.altitude.toFixed(1)} m` : '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">Rel Alt</span><span className="kv-value">--</span></div>
                   <div className="kv-row"><span className="kv-label">Gnd Speed</span><span className="kv-value">{tel.ground_speed != null ? `${tel.ground_speed.toFixed(1)} m/s` : '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">V-Speed</span><span className="kv-value">{tel.vertical_speed != null ? `${tel.vertical_speed.toFixed(1)} m/s` : '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">Heading</span><span className="kv-value">{tel.heading != null ? `${tel.heading.toFixed(1)}°` : '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">Air Speed</span><span className="kv-value">{tel.air_speed != null ? `${tel.air_speed.toFixed(1)} m/s` : '--'}</span></div>
                   <div className="kv-row" style={{gridColumn: '1 / -1', height: '8px', border: 'none'}}></div>
                   <div className="kv-row"><span className="kv-label">Battery</span><span className="kv-value">{tel.battery_level ?? '--'}%</span></div>
                   <div className="kv-row"><span className="kv-label">Voltage</span><span className="kv-value">{tel.battery_voltage != null ? `${tel.battery_voltage.toFixed(1)} V` : '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">Current</span><span className="kv-value">{tel.battery_current != null ? `${tel.battery_current.toFixed(1)} A` : '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">Satellites</span><span className="kv-value">{tel.satellites ?? '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">HDOP</span><span className="kv-value">{tel.hdop ?? '--'}</span></div>
                   <div className="kv-row"><span className="kv-label">VDOP</span><span className="kv-value">{tel.vdop ?? '--'}</span></div>
                </div>
             </div>

             <div className="glass-panel">
                <h3 style={{marginBottom: '16px'}}>SYSTEM STATUS</h3>
                <div className="kv-list" style={{display: 'flex', flexDirection: 'column'}}>
                   <div className="kv-row"><span className="kv-label">FCU Health</span><span className={`kv-value ${tel.system_health === 'OK' ? 'good' : 'danger'}`}>{tel.system_health || 'UNK'}</span></div>
                   <div className="kv-row"><span className="kv-label">Estimator</span><span className="kv-value">{tel.estimator_status || 'UNK'}</span></div>
                   <div className="kv-row"><span className="kv-label">GPS Fix</span><span className={`kv-value ${tel.gps_valid ? 'good' : 'danger'}`}>{tel.gps_valid ? '3D FIX' : 'NO FIX'}</span></div>
                   <div className="kv-row"><span className="kv-label">RC Link</span><span className="kv-value">{tel.rc_status || 'UNK'}</span></div>
                   <div className="kv-row"><span className="kv-label">Failsafe</span><span className={`kv-value ${drone.status === 'failsafe' ? 'danger' : 'good'}`}>{drone.status === 'failsafe' ? 'ACTIVE' : 'NONE'}</span></div>
                   <div className="kv-row"><span className="kv-label">Link Age</span><span className={`kv-value ${safety.reason === 'LINK DOWN' ? 'danger' : 'good'}`}>{((nowMs - drone.lastSeen)/1000).toFixed(1)}s</span></div>
                </div>
             </div>
          </div>
        </>
      )}
      
      {renderModals()}
    </div>
  );
}
