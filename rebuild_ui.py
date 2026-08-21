import re

with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

# Find the start of the return statement
return_index = content.find('  return (')

if return_index == -1:
    print("Could not find return statement")
    exit(1)

head = content[:return_index]

new_return = """  return (
    <div className="drone-control-view" style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', backgroundColor: 'var(--bg-color)', position: 'relative', overflow: 'hidden' }}>

      {/* BACKGROUND MAP LAYER (Z: 0) */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0, backgroundColor: '#E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
          <Map size={48} color="#94A3B8" style={{opacity: 0.5, marginBottom: '8px'}} />
          <div style={{color: '#94A3B8', fontSize: '14px', fontWeight: 'bold', letterSpacing: '2px'}}>INTEGRATED LIVE MAP</div>
          {isGpsValid ? <div style={{color: 'var(--success)', fontSize: '12px', marginTop: '4px', fontWeight: 'bold'}}>3D FIX</div> : <div style={{color: 'var(--danger)', fontSize: '12px', marginTop: '4px', fontWeight: 'bold'}}>NO FIX</div>}
      </div>

      {/* HEADER & TELEMETRY STRIP (Z: 20) */}
      <div style={{ zIndex: 20, display: 'flex', flexDirection: 'column', backgroundColor: 'var(--surface)', borderBottom: '1px solid var(--border)', boxShadow: 'var(--shadow-sm)' }}>
         
         {/* Top Header */}
         <div style={{display: 'flex', padding: '12px 24px', alignItems: 'center', justifyContent: 'space-between'}}>
             <div style={{display: 'flex', alignItems: 'center', gap: '16px'}}>
                 <h2 style={{margin: 0, display: 'flex', alignItems: 'center', gap: '8px'}}><Navigation size={20}/> PhoneOS GCS</h2>
                 
                 {/* TARGET DROPDOWN */}
                 <div style={{display: 'flex', alignItems: 'center', gap: '8px', background: 'var(--bg-color)', padding: '6px 12px', borderRadius: '6px', border: '1px solid var(--border)'}}>
                    <span style={{fontSize: '11px', fontWeight: 'bold', color: 'var(--text-muted)'}}>TARGET:</span>
                    <select value={targetMode === 'ALL' ? 'ALL' : targetDroneId || ''} onChange={handleTargetChange} style={{border: 'none', background: 'transparent', fontSize: '13px', fontWeight: 'bold', outline: 'none', cursor: 'pointer', color: 'var(--primary)'}}>
                       <option value="ALL">ALL DRONES</option>
                       {droneIds.map(id => <option key={id} value={id}>{id}</option>)}
                    </select>
                 </div>
             </div>
             
             {/* Status Badges */}
             <div style={{display: 'flex', alignItems: 'center', gap: '16px'}}>
                <div className="status-indicator">
                   <div className={`status-dot ${isHeartbeatHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">DRONE</span>
                      <span className="val">{isHeartbeatHealthy ? 'ONLINE' : 'OFFLINE'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${isTelemetryHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">LINK</span>
                      <span className="val">{isTelemetryHealthy ? 'GOOD' : 'POOR'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${isHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">SYSTEM</span>
                      <span className="val">{isHealthy ? 'OK' : 'WARN'}</span>
                   </div>
                </div>
                
                <button className="action-btn" onClick={() => setView('SETTINGS')} style={{marginLeft: '16px', display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-color)', cursor: 'pointer'}}>
                   <Settings size={16}/> SETTINGS
                </button>
             </div>
         </div>

         {/* Telemetry Strip */}
         <div style={{display: 'flex', gap: '12px', padding: '12px 24px', backgroundColor: 'var(--bg-color)'}}>
           <div className="telemetry-card" style={{flex: 1}}>
              <div className="t-header"><Battery size={14}/> BATTERY</div>
              <div className={`t-main ${tel.battery_level > 20 ? 'good' : 'danger'}`}>{tel.battery_level != null ? `${tel.battery_level.toFixed(0)}%` : '--'}</div>
              <div className="t-sub">{tel.voltage ? `${tel.voltage.toFixed(1)} V` : '--'}</div>
           </div>
           <div className="telemetry-card" style={{flex: 1}}>
              <div className="t-header"><Compass size={14}/> GPS</div>
              <div className={`t-main ${isGpsValid ? 'good' : 'danger'}`}>{isGpsValid ? '3D FIX' : 'NO FIX'}</div>
              <div className="t-sub">{tel.satellites || 0} Sats</div>
           </div>
           <div className="telemetry-card" style={{flex: 1}}>
              <div className="t-header"><Navigation size={14}/> HOME</div>
              <div className={`t-main ${isHomeValid ? 'good' : 'danger'}`}>{isHomeValid ? 'VALID' : 'INVALID'}</div>
              <div className="t-sub">Dist: {tel.distance_to_home != null ? `${tel.distance_to_home.toFixed(1)}m` : '--'}</div>
           </div>
           <div className="telemetry-card" style={{flex: 1}}>
              <div className="t-header"><ArrowUp size={14}/> ALTITUDE</div>
              <div className="t-main">{tel.altitude != null ? `${tel.altitude.toFixed(1)} m` : '--'}</div>
              <div className="t-sub">AGL</div>
           </div>
           <div className="telemetry-card" style={{flex: 1}}>
              <div className="t-header"><Gauge size={14}/> SPEED</div>
              <div className="t-main">{tel.ground_speed != null ? `${tel.ground_speed.toFixed(1)} m/s` : '--'}</div>
              <div className="t-sub">Z: {tel.vertical_speed != null ? tel.vertical_speed.toFixed(1) : '--'}</div>
           </div>
           <div className="telemetry-card" style={{flex: 1}}>
              <div className="t-header"><RotateCw size={14}/> HEADING</div>
              <div className="t-main">{tel.heading != null ? `${tel.heading.toFixed(0)}°` : '--'}</div>
           </div>
         </div>
      </div>

      {/* LEFT OVERLAY: HORIZONTAL MOVEMENT (Z: 10) */}
      <div style={{ position: 'absolute', bottom: '130px', left: '24px', zIndex: 10 }}>
         <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(8px)'}}>
            <div className="panel-header" style={{textAlign: 'center', marginBottom: '12px'}}>MANUAL HORIZONTAL</div>
            <div className="d-pad">
              <div></div>
              <button className={`d-btn ${activeMoveParams?.vx > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vx: movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <ArrowUp size={24}/><span className="d-label">FORWARD</span>
              </button>
              <div></div>
              <button className={`d-btn ${activeMoveParams?.vy < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vy: -movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <ArrowLeft size={24}/><span className="d-label">LEFT</span>
              </button>
              <div className="d-center" onPointerDown={(e) => { e.preventDefault(); stopMove(); }}>
                <Square size={24}/>
              </div>
              <button className={`d-btn ${activeMoveParams?.vy > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vy: movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <ArrowRight size={24}/><span className="d-label">RIGHT</span>
              </button>
              <div></div>
              <button className={`d-btn ${activeMoveParams?.vx < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vx: -movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <ArrowDown size={24}/><span className="d-label">BACK</span>
              </button>
              <div></div>
            </div>
         </div>
      </div>

      {/* RIGHT OVERLAY: VERTICAL & YAW (Z: 10) */}
      <div style={{ position: 'absolute', bottom: '130px', right: '24px', zIndex: 10, display: 'flex', gap: '16px' }}>
         
         {/* Speed Control floating panel */}
         <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
             <div className="panel-header" style={{textAlign: 'center', marginBottom: '12px'}}>SPEED</div>
             <button className="action-btn" style={{padding: '8px', marginBottom: '8px'}} onClick={() => setMovementSpeed(Math.min(5.0, movementSpeed + 0.05))}><Plus size={20}/></button>
             <div style={{fontSize: '16px', fontWeight: 'bold', color: 'var(--primary)', margin: '8px 0'}}>{movementSpeed.toFixed(2)}</div>
             <div style={{fontSize: '10px', color: 'var(--text-muted)', marginBottom: '8px'}}>m/s</div>
             <button className="action-btn" style={{padding: '8px'}} onClick={() => setMovementSpeed(Math.max(0.05, movementSpeed - 0.05))}><Minus size={20}/></button>
         </div>

         {/* Vertical / Yaw */}
         <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(8px)'}}>
            <div className="panel-header" style={{textAlign: 'center', marginBottom: '12px'}}>VERTICAL & YAW</div>
            <div className="d-pad">
              <button className={`d-btn h-btn ${activeMoveParams?.yaw_rate < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({yaw_rate: -yawRate}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <RotateCcw size={20}/><span className="d-label">YAW L</span>
              </button>
              <button className={`d-btn h-btn ${activeMoveParams?.vz > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vz: verticalSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <ArrowDown size={20}/><span className="d-label">DOWN</span>
              </button>
              <button className={`d-btn h-btn ${activeMoveParams?.yaw_rate > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({yaw_rate: yawRate}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <RotateCw size={20}/><span className="d-label">YAW R</span>
              </button>
              <div></div>
              <button className={`d-btn h-btn ${activeMoveParams?.vz < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vz: -verticalSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                <ArrowUp size={20}/><span className="d-label">UP</span>
              </button>
              <div></div>
            </div>
         </div>
      </div>

      {/* BOTTOM COMMAND BAR (Z: 30) */}
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 30, display: 'flex', padding: '16px 24px', backgroundColor: 'var(--surface)', borderTop: '1px solid var(--border)', gap: '12px', boxShadow: '0 -4px 6px -1px rgba(0,0,0,0.1)' }}>
         <button className="command-btn btn-arm" onClick={() => requestCommand(CommandAction.ARM, null, true)}>
            <Lock size={20}/> ARM
         </button>
         <button className="command-btn btn-disarm" onClick={() => requestCommand(CommandAction.DISARM, null, true)}>
            <Unlock size={20}/> DISARM
         </button>
         <button className="command-btn btn-hold" onClick={() => requestCommand(CommandAction.HOVER, null, false)}>
            <Square size={20}/> HOLD
         </button>
         <button className="command-btn btn-takeoff" onClick={() => requestCommand(CommandAction.TAKEOFF, null, true)}>
            <ArrowUp size={20}/> TAKEOFF
         </button>
         <button className="command-btn btn-land" onClick={() => requestCommand(CommandAction.LAND, null, true)}>
            <ArrowDown size={20}/> LAND
         </button>
         <button className="command-btn btn-rtl" onClick={() => requestCommand(CommandAction.RTL, null, true)}>
            <Navigation size={20}/> RTL
         </button>
         <button className="command-btn btn-emergency" onClick={() => requestCommand(CommandAction.EMERGENCY, null, true)}>
            <AlertTriangle size={20}/> EMERGENCY STOP
         </button>
      </div>

      {/* CONFIRMATION MODAL */}
      {showConfirmModal && (
         <div className="modal-overlay">
            <div className="modal-content" style={{maxWidth: '400px'}}>
               <h2 style={{marginTop: 0}}>Confirm Action</h2>
               <p>{showConfirmModal.message}</p>
               <div style={{display: 'flex', gap: '12px', marginTop: '24px'}}>
                  <button className="action-btn" style={{flex: 1}} onClick={() => setShowConfirmModal(null)}>CANCEL</button>
                  <button className="action-btn btn-emergency" style={{flex: 1}} onClick={() => executeCommand(showConfirmModal.action, showConfirmModal.params)}>CONFIRM</button>
               </div>
            </div>
         </div>
      )}

      {/* STYLES */}
      <style dangerouslySetInnerHTML={{__html: `
        :root {
           --bg-color: #F6F7F9;
           --surface: #FFFFFF;
           --border: #E5E7EB;
           --text-main: #111827;
           --text-muted: #6B7280;
           --primary: #2563EB;
           --success: #10B981;
           --warning: #F59E0B;
           --danger: #EF4444;
           --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
        }

        .status-indicator { display: flex; flex-direction: column; align-items: flex-start; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; margin-bottom: 4px; }
        .status-dot.good { background-color: var(--success); box-shadow: 0 0 4px var(--success); }
        .status-dot.danger { background-color: var(--danger); }
        .status-text { display: flex; flex-direction: column; }
        .status-text .label { font-size: 9px; color: var(--text-muted); font-weight: bold; }
        .status-text .val { font-size: 11px; font-weight: bold; }

        .telemetry-card {
           background: var(--surface);
           border: 1px solid var(--border);
           border-radius: 8px;
           padding: 8px 12px;
           display: flex;
           flex-direction: column;
           box-shadow: var(--shadow-sm);
        }
        .t-header { font-size: 10px; color: var(--text-muted); font-weight: bold; display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
        .t-main { font-size: 16px; font-weight: 800; font-family: monospace; }
        .t-main.good { color: var(--success); }
        .t-main.danger { color: var(--danger); }
        .t-sub { font-size: 10px; color: var(--text-muted); margin-top: 2px; }

        .control-panel {
           background: var(--surface);
           border: 1px solid var(--border);
           border-radius: 12px;
           padding: 16px;
           box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
        .panel-header { font-size: 11px; font-weight: bold; color: var(--text-muted); letter-spacing: 0.5px; margin-bottom: 12px; }

        .d-pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
        .d-btn {
           background: var(--bg-color); border: 1px solid var(--border); border-radius: 8px;
           display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
           width: 64px; height: 64px; cursor: pointer; color: var(--text-main); user-select: none; touch-action: none;
           transition: all 0.1s;
        }
        .h-btn { width: 56px; height: 56px; }
        .d-btn .d-label { font-size: 9px; font-weight: bold; }
        .d-btn:active, .d-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); transform: scale(0.95); }
        .d-center { display: flex; align-items: center; justify-content: center; color: var(--text-muted); cursor: pointer; }

        .command-btn {
           flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
           height: 64px; border-radius: 8px; border: none; font-weight: bold; font-size: 13px;
           cursor: pointer; transition: transform 0.1s, opacity 0.2s; color: #fff;
        }
        .command-btn:active { transform: scale(0.95); }
        .command-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

        .btn-arm { background: var(--success); }
        .btn-disarm { background: #991B1B; }
        .btn-hold { background: #4B5563; }
        .btn-takeoff { background: var(--primary); }
        .btn-land { background: var(--warning); }
        .btn-rtl { background: #7C3AED; }
        .btn-emergency { background: var(--danger); flex: 1.5; font-size: 15px; }

        .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .modal-content { background: var(--surface); padding: 32px; border-radius: 12px; width: 100%; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
      `}} />
    </div>
  );
}
"""

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(head + new_return)

print("Rewrote DroneControlView.jsx successfully.")
