import re

with open("mobile/src/views/DroneControlView.jsx", "r") as f:
    content = f.read()

match = re.search(r'(\s*return \(\s*<div className="drone-control-view".*?)\Z', content, re.DOTALL)
if not match:
    print("Could not find return statement")
    exit(1)

pre_return = content[:match.start(1)]

new_return = """
  return (
    <div className="drone-control-view">
      {/* BACKGROUND MAP LAYER */}
      <div className="map-layer">
         <ErrorBoundary fallback={
            <div className="map-fallback">
                <div className="map-fallback-title">MAP OFFLINE</div>
                <div className="map-fallback-text">Flight controls remain fully active.</div>
            </div>
         }>
             <MapContainer center={mapCenter} zoom={18} style={{ height: '100%', width: '100%' }} zoomControl={false}>
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="" />
            
            {droneIds.map(id => {
               const d = drones[id];
               const t = d?.telemetry;
               if (!t || t.latitude == null || t.longitude == null || isNaN(t.latitude) || isNaN(t.longitude) || t.latitude === 0) return null;
               
               const isTargeted = targetMode === 'ALL' || targetDroneId === id;
               const color = isTargeted ? '#10B981' : '#3B82F6';
               const icon = createDroneIcon(color, t.heading);
               
               return (
                  <Marker key={id} position={[t.latitude, t.longitude]} icon={icon}>
                     <Popup>
                        <div style={{color: '#000', fontWeight: 'bold'}}>{id}</div>
                     </Popup>
                  </Marker>
               );
            })}
         </MapContainer>
         </ErrorBoundary>
      </div>

      {/* HUD OVERLAY */}
      <div className="hud-overlay">
         {/* TOP BAR */}
         <div className="hud-top-bar">
             <div className="hud-top-left">
                 <button className="hud-btn" onClick={() => setView('DASHBOARD')}>
                    <Menu size={14}/> ← BACK
                 </button>
                 
                 <div className="hud-target-selector">
                    <span>TARGET:</span>
                    <select value={targetMode === 'ALL' ? 'ALL' : targetDroneId || ''} onChange={handleTargetChange}>
                       <option value="ALL">ALL DRONES</option>
                       {droneIds.map(id => <option key={id} value={id}>{id}</option>)}
                    </select>
                 </div>
             </div>
             
             <div className="hud-top-right">
                <div className="hud-status-item">
                   <div className={`status-dot ${isConnected === 'CONNECTED' ? 'good' : 'danger'}`}></div>
                   <span>{isConnected === 'CONNECTED' ? 'ONLINE' : 'OFFLINE'}</span>
                </div>
                <div className="hud-status-item">
                   <div className={`status-dot ${connectedDronesCount > 0 ? 'good' : 'danger'}`}></div>
                   <span>{connectedDronesCount} DRN</span>
                </div>
                <div className="hud-status-item">
                   <div className={`status-dot ${isPx4Connected ? 'good' : 'danger'}`}></div>
                   <span>PX4</span>
                </div>
                <div className={`hud-status-text ${tel.armed_state === 'ARMED' ? 'danger-text' : 'good-text'}`}>
                   {tel.armed_state || 'DISARMED'}
                </div>
                <div className="hud-status-text muted">
                   {tel.flight_mode || '---'}
                </div>
                <div className={`hud-mode-pill ${indoorMode ? 'warning-bg' : 'primary-bg'}`}>
                   {indoorMode ? 'INDOOR' : 'OUTDOOR'}
                </div>
                <button className="hud-btn" onClick={() => setView('SETTINGS')}>
                   <Settings size={12}/> SET
                </button>
             </div>
         </div>
         
         {/* STATUS CARDS */}
         <div className="hud-status-cards">
            <div className="telemetry-card">
               <div className="t-header"><Battery size={12}/> BAT</div>
               <div className={`t-main ${tel.battery_level > 20 ? 'good' : 'danger'}`}>{tel.battery_level != null ? `${tel.battery_level.toFixed(0)}%` : '--'}</div>
               <div className="t-sub">{tel.voltage ? `${tel.voltage.toFixed(1)}V` : '--'}</div>
            </div>
            <div className="telemetry-card">
               <div className="t-header"><Compass size={12}/> GPS</div>
               <div className={`t-main ${isGpsValid ? 'good' : 'danger'}`}>{isGpsValid ? 'FIX' : 'NO FIX'}</div>
               <div className="t-sub">{tel.satellites || 0} Sat</div>
            </div>
            <div className="telemetry-card">
               <div className="t-header"><Navigation size={12}/> HOME</div>
               <div className={`t-main ${isHomeValid ? 'good' : 'danger'}`}>{isHomeValid ? 'OK' : 'N/A'}</div>
               <div className="t-sub">{tel.distance_to_home != null ? `${tel.distance_to_home.toFixed(0)}m` : '--'}</div>
            </div>
            <div className="telemetry-card">
               <div className="t-header"><ArrowUp size={12}/> ALT</div>
               <div className="t-main">{tel.altitude != null ? `${tel.altitude.toFixed(1)}` : '--'}</div>
               <div className="t-sub">m AGL</div>
            </div>
            <div className="telemetry-card">
               <div className="t-header"><Gauge size={12}/> SPD</div>
               <div className="t-main">{tel.ground_speed != null ? `${tel.ground_speed.toFixed(1)}` : '--'}</div>
               <div className="t-sub">m/s</div>
            </div>
            <div className="telemetry-card">
               <div className="t-header"><RotateCw size={12}/> HDG</div>
               <div className="t-main">{tel.heading != null ? `${tel.heading.toFixed(0)}°` : '--'}</div>
            </div>
         </div>
         
         {/* E-STOP WARNING */}
         {activeDrone?.commandState?.action === CommandAction.EMERGENCY && activeDrone?.commandState?.state === 'ACCEPTED' && (
             <div className="estop-warning">
                <div className="estop-title"><AlertTriangle size={20}/> EMERGENCY STOP ACTIVE</div>
                <div className="estop-desc">System locked. Physical reset required.</div>
             </div>
         )}
         
         {/* MIDDLE FLEX AREA */}
         <div className="hud-middle">
            {/* LEFT COLUMN */}
            <div className="hud-left">
               <div className="control-panel">
                  <div className="panel-header">FLIGHT MODE</div>
                  <div className="mode-toggle">
                     <button className={!indoorMode ? 'active primary' : ''} onClick={() => setIndoorMode(false)}>OUTDOOR</button>
                     <button className={indoorMode ? 'active warning' : ''} onClick={() => setIndoorMode(true)}>INDOOR TEST</button>
                  </div>
                  {indoorMode && <div className="indoor-warning">NOT FOR OUTDOOR FLIGHT</div>}
               </div>
               
               <div className="control-panel preflight-panel">
                  <div className="panel-header">PX4 PREFLIGHT</div>
                  <div className="preflight-list">
                     <div className={`pf-row ${isPx4Connected ? 'good' : 'danger'}`}>
                        <span>MAVSDK / PX4</span> <span>{isPx4Connected ? '✓' : '✗'}</span>
                     </div>
                     <div className={`pf-row ${isGpsValid ? 'good' : 'danger'}`}>
                        <span>GPS FIX</span> <span>{isGpsValid ? '✓' : '✗'}</span>
                     </div>
                     <div className={`pf-row ${isHomeValid ? 'good' : 'danger'}`}>
                        <span>HOME POSITION</span> <span>{isHomeValid ? '✓' : '✗'}</span>
                     </div>
                     <div className={`pf-row ${isHealthy ? 'good' : 'danger'}`}>
                        <span>ESTIMATOR</span> <span>{isHealthy ? '✓' : '✗'}</span>
                     </div>
                     <div className={`pf-row ${isBatteryAcceptable ? 'good' : 'danger'}`}>
                        <span>BATTERY</span> <span>{isBatteryAcceptable ? '✓' : '✗'}</span>
                     </div>
                     <div className={`pf-row ${isArmable ? 'good' : 'danger'}`}>
                        <span>ARMABLE</span> <span>{isArmable ? '✓' : '✗'}</span>
                     </div>
                  </div>
                  {!isArmable && (
                     <div className="pf-reason">
                        Reason: {tel.status_text || 'WAITING'}
                     </div>
                  )}
               </div>
               
               <div className="hud-spacer"></div>
               
               <div className="control-panel move-panel">
                  <div className="panel-header">MOVE</div>
                  <div className="d-pad">
                    <div></div>
                    <button className={`d-btn ${activeMoveParams?.vx > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vx: movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                      <ArrowUp size={16}/><span className="d-label">FWD</span>
                    </button>
                    <div></div>
                    <button className={`d-btn ${activeMoveParams?.vy < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vy: -movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                      <ArrowLeft size={16}/><span className="d-label">L</span>
                    </button>
                    <div className="d-center" onPointerDown={(e) => { e.preventDefault(); stopMove(); }}>
                      <Square size={14}/>
                    </div>
                    <button className={`d-btn ${activeMoveParams?.vy > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vy: movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                      <ArrowRight size={16}/><span className="d-label">R</span>
                    </button>
                    <div></div>
                    <button className={`d-btn ${activeMoveParams?.vx < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vx: -movementSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                      <ArrowDown size={16}/><span className="d-label">BCK</span>
                    </button>
                    <div></div>
                  </div>
               </div>
            </div>
            
            {/* CENTER EMPTY AREA FOR MAP */}
            <div className="hud-center"></div>
            
            {/* RIGHT COLUMN */}
            <div className="hud-right">
                <div className="hud-spacer"></div>
                
                <div className="right-controls-group">
                   {/* Speed Control */}
                   <div className="control-panel mini-panel">
                       <div className="panel-header">SPD</div>
                       <button className="d-btn h-btn" onClick={() => setMovementSpeed(Math.min(5.0, movementSpeed + 0.05))}><Plus size={12}/></button>
                       <div className="mini-val">{movementSpeed.toFixed(2)}</div>
                       <div className="mini-unit">m/s</div>
                       <button className="d-btn h-btn" onClick={() => setMovementSpeed(Math.max(0.05, movementSpeed - 0.05))}><Minus size={12}/></button>
                   </div>
                   
                   {/* Altitude Hold */}
                   <div className="control-panel mini-panel">
                       <div className="panel-header">ALT</div>
                       <button className="d-btn h-btn" onClick={() => setTargetAltitude(targetAltitude + 0.5)}><Plus size={12}/></button>
                       <div className="mini-val">{targetAltitude.toFixed(1)}</div>
                       <div className="mini-unit">m</div>
                       <button className="d-btn h-btn" onClick={() => setTargetAltitude(Math.max(0.5, targetAltitude - 0.5))}><Minus size={12}/></button>
                   </div>
        
                   {/* Formation Control */}
                   <div className="control-panel mini-panel form-panel">
                       <div className="panel-header">FORM</div>
                       <select value={formationType} onChange={e => setFormationType(e.target.value)}>
                          <option value="V">V</option>
                          <option value="COLUMN">COL</option>
                          <option value="LINE">LINE</option>
                          <option value="SQUARE">SQ</option>
                          <option value="GRID">GRID</option>
                          <option value="CIRCLE">CIR</option>
                       </select>
                       <button className="d-btn h-btn text-btn" onClick={() => requestCommand(CommandAction.FORMATION_UPDATE, { type: formationType, spacing: formationSpacing })}>APPLY</button>
                   </div>
        
                   {/* Vertical / Yaw D-Pad */}
                   <div className="control-panel vert-yaw-panel">
                      <div className="panel-header">VERT/YAW</div>
                      <div className="d-pad">
                        <button className={`d-btn h-btn ${activeMoveParams?.yaw_rate < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({yaw_rate: -yawRate}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                          <RotateCcw size={14}/><span className="d-label">YL</span>
                        </button>
                        <button className={`d-btn h-btn ${activeMoveParams?.vz < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vz: -verticalSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                          <ArrowUp size={14}/><span className="d-label">UP</span>
                        </button>
                        <button className={`d-btn h-btn ${activeMoveParams?.yaw_rate > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({yaw_rate: yawRate}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                          <RotateCw size={14}/><span className="d-label">YR</span>
                        </button>
                        <div></div>
                        <button className={`d-btn h-btn ${activeMoveParams?.vz > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vz: verticalSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                          <ArrowDown size={14}/><span className="d-label">DN</span>
                        </button>
                        <div></div>
                      </div>
                   </div>
                </div>
            </div>
         </div>
         
         {/* BOTTOM COMMAND BAR */}
         <div className="hud-bottom-bar">
            <button className={`command-btn btn-arm ${!isArmable ? 'disabled' : ''}`} disabled={!isArmable} onClick={() => requestCommand(CommandAction.ARM, null, true)}>
               <div className="cmd-main"><Lock size={14}/> {isArmable ? 'ARM' : 'ARM NOT READY'}</div>
               {!isArmable && <div className="cmd-sub">{tel.status_text || 'CHECK PREFLIGHT'}</div>}
            </button>
            
            <button className="command-btn btn-disarm" onClick={() => requestCommand(CommandAction.DISARM, null, true)}>
               <div className="cmd-main"><Unlock size={14}/> DISARM</div>
            </button>
            
            <button className="command-btn btn-hold" onClick={() => requestCommand(CommandAction.HOVER, null, false)}>
               <div className="cmd-main"><Square size={14}/> HOLD</div>
            </button>
            
            <button className={`command-btn btn-takeoff ${tel.armed_state !== 'ARMED' ? 'disabled' : ''}`} disabled={tel.armed_state !== 'ARMED'} onClick={() => requestCommand(CommandAction.TAKEOFF, { altitude_m: targetAltitude }, true)}>
               <div className="cmd-main"><ArrowUp size={14}/> TAKEOFF</div>
               {tel.armed_state !== 'ARMED' && <div className="cmd-sub">NOT ARMED</div>}
            </button>
            
            {/* LAND & RTL = SUPER KEYS */}
            <button className="command-btn btn-land super-key" onClick={() => requestCommand(CommandAction.LAND)}>
               <div className="cmd-main"><ArrowDown size={16}/> LAND</div>
            </button>
            <button className="command-btn btn-rtl super-key" onClick={() => requestCommand(CommandAction.RTL)}>
               <div className="cmd-main"><Navigation size={16}/> RTL</div>
            </button>
            <button className="command-btn btn-emergency" onClick={() => requestCommand(CommandAction.EMERGENCY, null, true)}>
               <div className="cmd-main"><AlertTriangle size={14}/> E-STOP</div>
            </button>
         </div>
      </div>

      {/* COMMAND LIFECYCLE OVERLAY (Right edge) */}
      <div className="lifecycle-overlay">
         {droneIds.map(id => {
            const cs = drones[id]?.commandState;
            if (!cs || !cs.action) return null;
            if (cs.state === 'SUCCESS' && (nowMs - (cs.timestamp || nowMs)) > 5000) return null; // hide success after 5s
            
            let color = 'var(--text-muted)';
            let bg = 'rgba(255,255,255,0.85)';
            if (cs.state === 'SUCCESS') { color = 'var(--success)'; bg = 'rgba(16, 185, 129, 0.1)'; }
            if (cs.state === 'FAILED' || cs.state === 'REJECTED' || cs.state === 'TIMEOUT') { color = 'var(--danger)'; bg = 'rgba(239, 68, 68, 0.1)'; }
            if (cs.state === 'MAVSDK_REQUESTED' || cs.state === 'BACKEND_RECEIVED') { color = 'var(--warning)'; }

            return (
               <div key={id} className="lifecycle-card" style={{ background: bg, borderColor: color }}>
                  <div className="lc-header" style={{color: 'var(--text-main)'}}>{id}: <span style={{color}}>{cs.action.toUpperCase()}</span></div>
                  <div className="lc-state" style={{color}}>{cs.state}</div>
                  {cs.reason && <div className="lc-reason">{cs.reason}</div>}
               </div>
            );
         })}
      </div>

      {/* CONFIRMATION MODAL */}
      {showConfirmModal && (
         <div className="modal-overlay">
            <div className="modal-content">
               <h2>Confirm Action</h2>
               <p>{showConfirmModal.message}</p>
               <div className="modal-actions">
                  <button className="action-btn" onClick={() => setShowConfirmModal(null)}>CANCEL</button>
                  <button className="action-btn btn-emergency" onClick={() => executeCommand(showConfirmModal.action, showConfirmModal.params)}>CONFIRM</button>
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
           --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }

        .drone-control-view {
           display: flex;
           flex-direction: column;
           height: 100vh;
           width: 100vw;
           background-color: #E2E8F0;
           position: relative;
           overflow: hidden;
           font-family: 'Outfit', sans-serif;
        }

        .map-layer {
           position: absolute;
           inset: 0;
           z-index: 0;
        }
        .map-fallback { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #E2E8F0; }
        .map-fallback-title { color: #94A3B8; font-size: 14px; font-weight: bold; }
        .map-fallback-text { color: #94A3B8; font-size: 12px; }

        .hud-overlay {
           position: absolute;
           inset: 0;
           z-index: 10;
           display: flex;
           flex-direction: column;
           pointer-events: none; /* Let clicks pass through to map by default */
        }

        /* Top Bar */
        .hud-top-bar {
           pointer-events: auto;
           display: flex;
           justify-content: space-between;
           align-items: center;
           padding: 6px 12px;
           background: var(--surface);
           border-bottom: 1px solid var(--border);
           box-shadow: var(--shadow-sm);
           flex-wrap: wrap;
           gap: 8px;
        }
        .hud-top-left, .hud-top-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .hud-btn {
           background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px; 
           padding: 6px 10px; cursor: pointer; display: flex; align-items: center; gap: 4px; 
           font-size: 11px; font-weight: bold; color: var(--text-muted);
        }
        .hud-target-selector {
           display: flex; align-items: center; gap: 6px; background: var(--bg-color); 
           padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border);
        }
        .hud-target-selector span { font-size: 10px; font-weight: bold; color: var(--text-muted); }
        .hud-target-selector select { border: none; background: transparent; font-size: 11px; font-weight: bold; color: var(--primary); outline: none; cursor: pointer; }
        
        .hud-status-item { display: flex; align-items: center; gap: 4px; font-size: 10px; font-weight: bold; color: var(--text-muted); }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; }
        .status-dot.good { background: var(--success); box-shadow: 0 0 4px var(--success); }
        .status-dot.danger { background: var(--danger); }
        
        .hud-status-text { font-size: 11px; font-weight: bold; }
        .good-text { color: var(--success); }
        .danger-text { color: var(--danger); }
        .muted { color: var(--text-muted); }
        
        .hud-mode-pill { padding: 4px 8px; border-radius: 6px; color: #fff; font-size: 10px; font-weight: bold; }
        .warning-bg { background: var(--warning); }
        .primary-bg { background: var(--primary); }

        /* Status Cards */
        .hud-status-cards {
           pointer-events: auto;
           display: flex;
           gap: 6px;
           padding: 6px 8px;
           background: rgba(255,255,255,0.85);
           backdrop-filter: blur(8px);
           border-bottom: 1px solid var(--border);
           overflow-x: auto;
        }
        .hud-status-cards::-webkit-scrollbar { display: none; }
        .telemetry-card {
           flex: 1; min-width: 70px; background: var(--surface); border: 1px solid var(--border); 
           border-radius: 8px; padding: 6px 8px; display: flex; flex-direction: column;
           box-shadow: var(--shadow-sm);
        }
        .t-header { font-size: 10px; color: var(--text-muted); font-weight: bold; display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
        .t-main { font-size: 14px; font-weight: 800; font-family: monospace; line-height: 1; margin-bottom: 2px; }
        .t-main.good { color: var(--success); }
        .t-main.danger { color: var(--danger); }
        .t-sub { font-size: 9px; color: var(--text-muted); line-height: 1; }

        /* Estop Warning */
        .estop-warning {
           pointer-events: auto;
           margin: 16px auto;
           background: var(--danger); color: white; padding: 12px 24px; border-radius: 8px; 
           font-weight: bold; box-shadow: var(--shadow-md); text-align: center; max-width: 300px;
        }
        .estop-title { display: flex; alignItems: center; gap: 8px; justify-content: center; font-size: 14px; }
        .estop-desc { font-size: 11px; font-weight: normal; margin-top: 4px; }

        /* Middle Flex Area */
        .hud-middle {
           flex: 1;
           display: flex;
           padding: 12px;
           gap: 12px;
           overflow: hidden;
        }
        .hud-left {
           pointer-events: none;
           display: flex;
           flex-direction: column;
           gap: 12px;
           width: 220px;
           overflow-y: auto;
           overflow-x: hidden;
        }
        .hud-left::-webkit-scrollbar { display: none; }
        .hud-left > * { pointer-events: auto; }
        .hud-center { flex: 1; }
        .hud-spacer { flex: 1; min-height: 12px; }
        
        .hud-right {
           pointer-events: none;
           display: flex;
           flex-direction: column;
           gap: 12px;
           overflow-y: auto;
           overflow-x: hidden;
        }
        .hud-right::-webkit-scrollbar { display: none; }
        .hud-right > * { pointer-events: auto; }
        .right-controls-group {
           display: flex;
           gap: 6px;
           align-items: flex-end;
           justify-content: flex-end;
        }

        /* Control Panels */
        .control-panel {
           background: rgba(255, 255, 255, 0.95);
           backdrop-filter: blur(8px);
           border: 1px solid var(--border);
           border-radius: 8px;
           padding: 10px;
           box-shadow: var(--shadow-md);
           display: flex; flex-direction: column;
        }
        .panel-header { font-size: 10px; font-weight: bold; color: var(--text-muted); text-transform: uppercase; text-align: center; margin-bottom: 8px; }

        /* Preflight Panel */
        .preflight-panel { width: 100%; }
        .preflight-list { display: flex; flex-direction: column; gap: 6px; font-size: 11px; font-weight: 600; }
        .pf-row { display: flex; justify-content: space-between; gap: 12px; }
        .pf-row.good { color: var(--success); }
        .pf-row.danger { color: var(--danger); }
        .pf-reason { margin-top: 8px; padding: 6px; background: rgba(239,68,68,0.1); color: var(--danger); font-size: 10px; font-weight: bold; border-radius: 4px; word-wrap: break-word; }

        /* Mode Toggle */
        .mode-toggle { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
        .mode-toggle button { flex: 1; padding: 8px; font-size: 10px; font-weight: bold; border: none; background: transparent; color: var(--text-muted); cursor: pointer; }
        .mode-toggle button.active.primary { background: var(--primary); color: #fff; }
        .mode-toggle button.active.warning { background: var(--warning); color: #fff; }
        .indoor-warning { font-size: 9px; color: var(--danger); font-weight: bold; text-align: center; margin-top: 6px; }

        /* D-Pads */
        .move-panel { align-self: flex-start; }
        .vert-yaw-panel { }
        .d-pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
        .d-btn {
           background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px;
           display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
           width: 48px; height: 48px; cursor: pointer; color: var(--text-main); user-select: none; touch-action: none;
           transition: transform 0.1s;
        }
        .h-btn { width: 44px; height: 44px; }
        .text-btn { font-size: 10px; font-weight: bold; }
        .d-btn .d-label { font-size: 9px; font-weight: bold; }
        .d-btn:active, .d-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); transform: scale(0.95); }
        .d-center { display: flex; align-items: center; justify-content: center; color: var(--text-muted); cursor: pointer; }

        /* Mini Panels */
        .mini-panel { align-items: center; justify-content: center; min-width: 50px; }
        .mini-val { font-size: 14px; font-weight: bold; color: var(--primary); margin: 4px 0; }
        .mini-unit { font-size: 9px; color: var(--text-muted); margin-bottom: 4px; }
        .form-panel select { font-size: 11px; padding: 4px; margin-bottom: 6px; width: 50px; border-radius: 4px; border: 1px solid var(--border); }

        /* Bottom Command Bar */
        .hud-bottom-bar {
           pointer-events: auto;
           display: flex;
           padding: 8px 12px;
           background: var(--surface);
           border-top: 1px solid var(--border);
           gap: 6px;
           box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
           overflow-x: auto;
        }
        .hud-bottom-bar::-webkit-scrollbar { display: none; }
        .command-btn {
           flex: 1; min-width: 80px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
           padding: 10px 4px; border-radius: 8px; border: none; cursor: pointer; transition: transform 0.1s, opacity 0.2s; color: #fff;
        }
        .command-btn:active:not(:disabled) { transform: scale(0.95); }
        .command-btn.disabled, .command-btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; background: #9CA3AF !important; border-color: #9CA3AF !important; }
        
        .cmd-main { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: bold; }
        .cmd-sub { font-size: 9px; font-weight: normal; color: rgba(255,255,255,0.9); text-align: center; }

        .btn-arm { background: var(--success); }
        .btn-disarm { background: #991B1B; }
        .btn-hold { background: #4B5563; }
        .btn-takeoff { background: var(--primary); }
        .btn-land { background: #DC2626; }
        .btn-rtl { background: #7C3AED; }
        .btn-emergency { background: #7F1D1D; min-width: 90px; }
        .super-key { box-shadow: 0 0 10px rgba(0,0,0,0.2); border: 2px solid rgba(255,255,255,0.3); }

        /* Lifecycle Overlay */
        .lifecycle-overlay {
           position: absolute; top: 120px; right: 12px; z-index: 40; display: flex; flex-direction: column; gap: 6px; width: 220px; pointer-events: none;
        }
        .lifecycle-card {
           padding: 10px 12px; backdrop-filter: blur(4px); border-radius: 8px; font-size: 11px; border: 1px solid; box-shadow: var(--shadow-sm); pointer-events: auto;
        }
        .lc-header { font-weight: bold; margin-bottom: 2px; }
        .lc-state { font-weight: 600; }
        .lc-reason { margin-top: 4px; font-size: 10px; }

        /* Modal */
        .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 16px; pointer-events: auto;}
        .modal-content { background: var(--surface); padding: 24px; border-radius: 12px; width: 100%; max-width: 400px; box-shadow: var(--shadow-md); pointer-events: auto;}
        .modal-actions { display: flex; gap: 8px; margin-top: 24px; }
        .action-btn { flex: 1; padding: 12px; border-radius: 8px; font-weight: bold; font-size: 14px; border: none; cursor: pointer; }
        
        /* Responsive Media Queries */
        @media (max-width: 767px) and (orientation: portrait) {
           .hud-middle { flex-direction: column; overflow-y: auto; pointer-events: auto; }
           .hud-left { width: 100%; overflow: visible; pointer-events: auto; }
           .hud-center { display: none; } /* map is strictly background, no spacer needed in portrait */
           .hud-right { overflow: visible; pointer-events: auto; }
           .right-controls-group { flex-wrap: wrap; justify-content: center; }
           .hud-spacer { display: none; }
           
           /* Reduce d-pad size slightly to fit better on portrait */
           .d-btn { width: 44px; height: 44px; }
           .h-btn { width: 40px; height: 40px; }
           .d-pad { gap: 4px; }
        }
      `}} />
    </div>
  );
"""

final_content = pre_return + new_return

with open("/home/priyanshu/Projects/PhoneOS/mobile/src/views/DroneControlView.jsx", "w") as f:
    f.write(final_content)

print("Successfully rewrote DroneControlView.jsx")
