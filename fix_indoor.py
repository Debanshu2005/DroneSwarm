import re

with open("mobile/src/views/DroneControlView.jsx", "r") as f:
    content = f.read()

# We need to replace the section from <div className="hud-middle"> to the end of <div className="hud-bottom-bar">

hud_middle_start = content.find('<div className="hud-middle">')
hud_bottom_end = content.find('</div>\n      </div>\n\n      {/* COMMAND LIFECYCLE OVERLAY')

if hud_middle_start == -1 or hud_bottom_end == -1:
    print("Could not find boundaries")
    exit(1)

pre = content[:hud_middle_start]
post = content[hud_bottom_end:]

new_ui = """<div className="hud-middle">
            {/* LEFT COLUMN */}
            <div className="hud-left">
               <div className="control-panel">
                  <div className="panel-header">FLIGHT MODE</div>
                  <div className="mode-toggle">
                     <button className={!indoorMode ? 'active primary' : ''} onClick={() => setIndoorMode(false)}>OUTDOOR</button>
                     <button className={indoorMode ? 'active warning' : ''} onClick={() => setIndoorMode(true)}>INDOOR TEST</button>
                  </div>
                  {indoorMode && <div className="indoor-warning">TEST MODE ACTIVE</div>}
               </div>
               
               {!indoorMode ? (
                   <>
                       <div className="control-panel preflight-panel">
                          <div className="panel-header">PX4 PREFLIGHT</div>
                          <div className="preflight-list">
                             <div className={`pf-row ${isPx4Connected ? 'good' : 'danger'}`}>
                                <span>MAVSDK / PX4</span> <span>{isPx4Connected ? 'READY' : 'NOT READY'}</span>
                             </div>
                             <div className={`pf-row ${isGpsValid ? 'good' : 'danger'}`}>
                                <span>GPS FIX</span> <span>{isGpsValid ? 'FIX' : 'NO FIX'}</span>
                             </div>
                             <div className={`pf-row ${isHomeValid ? 'good' : 'danger'}`}>
                                <span>HOME POSITION</span> <span>{isHomeValid ? 'OK' : 'N/A'}</span>
                             </div>
                             <div className={`pf-row ${isHealthy ? 'good' : 'danger'}`}>
                                <span>ESTIMATOR</span> <span>{isHealthy ? 'OK' : 'NOT READY'}</span>
                             </div>
                             <div className={`pf-row ${isBatteryAcceptable ? 'good' : 'danger'}`}>
                                <span>BATTERY</span> <span>{isBatteryAcceptable ? 'OK' : 'LOW'}</span>
                             </div>
                             <div className={`pf-row ${isArmable ? 'good' : 'danger'}`}>
                                <span>ARMABLE</span> <span>{isArmable ? 'READY' : 'NOT READY'}</span>
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
                   </>
               ) : (
                   <div className="control-panel test-panel" style={{border: '2px solid var(--warning)'}}>
                      <div className="panel-header" style={{color: 'var(--warning)', fontSize: '12px'}}>TEST PANEL</div>
                      <div style={{color: 'var(--text-muted)', fontSize: '10px', marginBottom: '12px', textAlign: 'center'}}>Mode: INDOOR TEST</div>
                      
                      <div style={{display: 'flex', flexDirection: 'column', gap: '8px'}}>
                          <button className="command-btn btn-hold" style={{padding: '12px'}} onClick={() => requestCommand(CommandAction.ARM, null, true)}>
                             <div className="cmd-main"><Lock size={14}/> TEST ARM</div>
                          </button>
                          <button className="command-btn btn-takeoff" style={{padding: '12px'}} onClick={() => requestCommand(CommandAction.TAKEOFF, { altitude_m: targetAltitude }, true)}>
                             <div className="cmd-main"><ArrowUp size={14}/> TEST TAKEOFF</div>
                          </button>
                          <button className="command-btn btn-hold" style={{padding: '12px', background: '#4B5563'}} onClick={() => requestCommand(CommandAction.HOVER, null, false)}>
                             <div className="cmd-main"><Square size={14}/> TEST HOVER</div>
                          </button>
                          <button className="command-btn btn-land" style={{padding: '12px'}} onClick={() => requestCommand(CommandAction.LAND)}>
                             <div className="cmd-main"><ArrowDown size={14}/> TEST LAND</div>
                          </button>
                      </div>
                      
                      <div style={{marginTop: '16px', textAlign: 'center', fontSize: '11px', fontWeight: 'bold'}}>
                          TEST STATUS: <br/><span style={{color: 'var(--warning)', fontSize: '14px'}}>ACTIVE</span>
                      </div>
                   </div>
               )}
            </div>
            
            {/* CENTER EMPTY AREA FOR MAP */}
            <div className="hud-center"></div>
            
            {/* RIGHT COLUMN - Hidden in INDOOR mode */}
            {!indoorMode && (
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
            )}
         </div>
         
         {/* BOTTOM COMMAND BAR - Hidden in INDOOR mode */}
         {!indoorMode && (
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
         )}"""

with open("mobile/src/views/DroneControlView.jsx", "w") as f:
    f.write(pre + new_ui + post)

print("Updated view")
