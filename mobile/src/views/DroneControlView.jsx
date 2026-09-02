import React, { useState, useRef, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import {
  ShieldCheck, Navigation, ArrowUp, ArrowDown,
  ArrowLeft, Square, RotateCcw, RotateCw, ArrowRight, Menu,
  Battery, Compass, Gauge, AlertTriangle, Lock, Unlock,
  Plus, Minus, Settings, X, LocateFixed, Radar
} from 'lucide-react';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { MapContainer, TileLayer, Marker, Polyline, CircleMarker, Popup, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';

import { CommandAction } from '../protocol/messages';
import { useDeviceLocation } from '../hooks/useDeviceLocation';
import { DEFAULT_MAP_CENTER, resolveAirspaceZone } from '../utils/airspace';
import AirspaceZonePanel from '../components/AirspaceZonePanel';


// Fix Leaflet's default icon path issues in React
try {
  delete L.Icon.Default.prototype._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
    iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
  });
} catch (e) {
  console.warn("Leaflet icon manipulation failed", e);
}


const createDroneIcon = (color, heading) => {
  const rotation = heading != null && !isNaN(heading) ? `transform: rotate(${heading}deg);` : '';
  const svg = `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="${rotation} transform-origin: center;">
      <path d="M12 2L22 20L12 16L2 20L12 2Z" fill="${color}" stroke="white" stroke-width="1.5"/>
    </svg>`;
  return L.divIcon({ html: svg, className: 'custom-drone-icon', iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -16] });
};

function RecenterAutomatically({ center }) {
  const map = useMap();
  useEffect(() => {
    if (center) {
      map.setView(center, map.getZoom(), { animate: true });
    }
  }, [center, map]);
  return null;
}

export default function DroneControlView({ setView }) {
  const { drones, nowMs, sendCommand, isConnected, indoorMode, setIndoorMode } = useDroneContext();

  const [targetMode, setTargetMode] = useState('ALL'); // 'ALL' or droneId
  const [targetDroneId, setTargetDroneId] = useState(null);

  // Formation defaults
  const [formationType, setFormationType] = useState('V');
  const [formationSpacing, setFormationSpacing] = useState(2.0);

  // Speed defaults to 0.10 m/s
  const [movementSpeed, setMovementSpeed] = useState(0.10);
  const [verticalSpeed, setVerticalSpeed] = useState(0.10);
  const [yawRate, setYawRate] = useState(15.0);
  const [targetAltitude, setTargetAltitude] = useState(1.0);

  const location = useDeviceLocation();
  const userLocation = location.coords;

  const [showConfirmModal, setShowConfirmModal] = useState(null); // { action, params, message }
  const [dismissedCmds, setDismissedCmds] = useState(new Set());

  const [newIp, setNewIp] = useState("192.168.1.100");
  const [newPort, setNewPort] = useState("8080");

  const moveIntervalRef = useRef(null);
  const [activeMoveParams, setActiveMoveParams] = useState(null);

  // Determine active drone context for telemetry display
  // If 'ALL', we show aggregate or just pick the first healthy drone as reference.
  const droneIds = Object.keys(drones || {});
  const activeId = targetMode === 'ALL' ? (droneIds[0] || null) : targetDroneId;
  const activeDrone = drones[activeId];
  const tel = activeDrone?.telemetry || {};

  const handleTargetChange = (e) => {
    const val = e.target.value;
    if (val === 'ALL') {
      setTargetMode('ALL');
      setTargetDroneId(null);
    } else {
      setTargetMode('SINGLE');
      setTargetDroneId(val);
    }
  };

  const getTargetArray = () => {
    return targetMode === 'ALL' ? droneIds : (targetDroneId ? [targetDroneId] : []);
  };

  const executeCommand = (action, params = null) => {
    const targets = getTargetArray();
    if (targets.length === 0) return;

    if (action === CommandAction.EMERGENCY) {
      sendCommand(CommandAction.STOP, params, targets, true);
    } else {
      sendCommand(action, params, targets);
    }
    setShowConfirmModal(null);
  };

  const requestCommand = (action, params = null, danger = false) => {
    // LAND, RTL, EMERGENCY are SUPER KEYS - always execute immediately, no confirmation
    if (action === CommandAction.LAND || action === CommandAction.RTL || action === CommandAction.EMERGENCY) {
        stopMove(); // High priority commands cancel active movement immediately
        executeCommand(action, params);
        return;
    }
    if (danger && targetMode === 'ALL') {
      setShowConfirmModal({ action, params, message: `Are you sure you want to ${action.toUpperCase()} ALL DRONES?` });
    } else {
      executeCommand(action, params);
    }
  };

  const startMove = (params) => {
     if (activeMoveParams) return;
     setActiveMoveParams(params);
     if (moveIntervalRef.current) clearInterval(moveIntervalRef.current);
     executeCommand(CommandAction.MOVE, params);
     moveIntervalRef.current = setInterval(() => {
        executeCommand(CommandAction.MOVE, params);
     }, 200);
  };

  const stopMove = () => {
     if (moveIntervalRef.current) clearInterval(moveIntervalRef.current);
     setActiveMoveParams(null);
     executeCommand(CommandAction.HOVER);
  };

  // Joystick safety
  useEffect(() => {
     const handleVisibilityChange = () => {
         if (document.hidden) {
             stopMove();
         }
     };
     document.addEventListener('visibilitychange', handleVisibilityChange);
     
     return () => {
         document.removeEventListener('visibilitychange', handleVisibilityChange);
         stopMove();
     };
     // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Aggregate States
  const isHeartbeatHealthy = activeDrone ? (nowMs - activeDrone.lastSeen) < 4000 : false;
  const isTelemetryHealthy = isHeartbeatHealthy && isConnected === "CONNECTED";
  const isPx4Connected = tel.flight_mode && tel.flight_mode !== "disconnected" && tel.flight_mode !== "UNKNOWN";
  const isBatteryAcceptable = (tel.battery_level || 0) >= 15;
  const isGpsValid = tel.gps_valid === true;
  const isHomeValid = tel.home_valid === true;
  const isArmable = tel.is_armable === true;
  const isHealthy = tel.health_all_ok === true;

  const connectedDronesCount = droneIds.filter(id => (nowMs - drones[id].lastSeen) < 4000).length;

  
  let mapCenter;
  if (userLocation) {
      mapCenter = userLocation;
  } else if (activeDrone && tel.latitude && tel.longitude && tel.latitude !== 0) {
      mapCenter = [tel.latitude, tel.longitude];
  } else {
      // Find any drone with valid GPS
      const validDroneId = droneIds.find(id => drones[id]?.telemetry?.latitude && drones[id]?.telemetry?.latitude !== 0);
      if (validDroneId) {
          mapCenter = [drones[validDroneId].telemetry.latitude, drones[validDroneId].telemetry.longitude];
      }
  }
  if (!mapCenter) mapCenter = DEFAULT_MAP_CENTER;

  const currentZone = resolveAirspaceZone(userLocation?.[0] ?? mapCenter[0], userLocation?.[1] ?? mapCenter[1]);
  const gpsSourceText = userLocation
      ? `Pilot GPS ${userLocation[0].toFixed(5)}, ${userLocation[1].toFixed(5)}`
      : location.status === 'requesting'
        ? 'Requesting pilot GPS...'
        : location.error || 'Pilot GPS unavailable';

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
            <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" attribution="" />
            <RecenterAutomatically center={mapCenter} />
            
            {droneIds.map(id => {
               const d = drones[id];
               const t = d?.telemetry;
               if (!t || t.latitude == null || t.longitude == null || isNaN(t.latitude) || isNaN(t.longitude) || t.latitude === 0) return null;
               
               const isTargeted = targetMode === 'ALL' || targetDroneId === id;
               const color = isTargeted ? '#10B981' : '#3B82F6';
               const icon = createDroneIcon(color, t.heading);
               
               return (
                  <React.Fragment key={id}>
                     <Marker position={[t.latitude, t.longitude]} icon={icon}>
                        <Popup>
                           <div style={{color: '#000', fontWeight: 'bold'}}>{id}</div>
                        </Popup>
                     </Marker>
                     {d.path && d.path.length > 1 && (
                        <Polyline 
                           positions={d.path} 
                           color={color} 
                           weight={3} 
                           opacity={0.6}
                        />
                     )}
                  </React.Fragment>
               );
            })}

            {userLocation && (
               <CircleMarker 
                  center={userLocation} 
                  pathOptions={{ color: '#5de4ff', fillColor: '#5de4ff', fillOpacity: 0.9 }} 
                  radius={8}
               >
                  <Popup>
                     <div style={{color: '#000', fontWeight: 'bold'}}>Pilot GPS</div>
                  </Popup>
               </CircleMarker>
            )}
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

                 <button className="hud-btn hud-gps-btn" onClick={location.requestLocation}>
                    <LocateFixed size={14}/> PILOT GPS
                 </button>
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
            <div className="telemetry-card pilot-card">
               <div className="t-header"><LocateFixed size={12}/> LAUNCH</div>
               <div className={`t-main ${userLocation ? 'good' : 'danger'}`}>{userLocation ? 'LOCK' : 'GPS'}</div>
               <div className="t-sub">{userLocation && location.accuracy ? `+/- ${location.accuracy.toFixed(0)}m` : gpsSourceText}</div>
            </div>
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

         <AirspaceZonePanel currentZone={currentZone} userLocation={userLocation} location={location} />
         
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
                          <button className="command-btn btn-hold" style={{padding: '12px'}} onClick={() => console.log("TEST ARM Triggered")}>
                             <div className="cmd-main"><Lock size={14}/> TEST ARM</div>
                          </button>
                          <button className="command-btn btn-takeoff" style={{padding: '12px'}} onClick={() => console.log("TEST TAKEOFF Triggered")}>
                             <div className="cmd-main"><ArrowUp size={14}/> TEST TAKEOFF</div>
                          </button>
                          <button className="command-btn btn-hold" style={{padding: '12px', background: '#4B5563'}} onClick={() => console.log("TEST HOVER Triggered")}>
                             <div className="cmd-main"><Square size={14}/> TEST HOVER</div>
                          </button>
                          <button className="command-btn btn-land" style={{padding: '12px'}} onClick={() => console.log("TEST LAND Triggered")}>
                             <div className="cmd-main"><ArrowDown size={14}/> TEST LAND</div>
                          </button>
                          <div style={{marginTop: '8px', fontSize: '10px', color: 'var(--warning)', textAlign: 'center'}}>
                              (Test controls are isolated from real MAVSDK handlers)
                          </div>
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
                 <button className={`command-btn btn-arm ${!isPx4Connected ? 'disabled' : ''}`} disabled={!isPx4Connected} onClick={() => requestCommand(CommandAction.ARM, null, true)}>
                    <div className="cmd-main"><Lock size={14}/> {
                      (activeDrone?.commandState?.action === 'arm' && (activeDrone?.commandState?.state === 'SENDING' || activeDrone?.commandState?.state === 'ACCEPTED') && tel.armed_state !== 'ARMED') ? 'ARMING...' : 
                      (tel.armed_state === 'ARMED' ? 'ARMED' : 'ARM')
                    }</div>
                    {(activeDrone?.commandState?.action === 'arm' && activeDrone?.commandState?.state === 'REJECTED') && 
                      <div className="cmd-sub" style={{color: 'var(--danger)'}}>{activeDrone.commandState.reason || 'FC REJECTED ARM'}</div>
                    }
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
                <button className="command-btn btn-emergency" style={{background: '#D97706'}} onClick={() => requestCommand(CommandAction.STOP, null, true)}>
                   <div className="cmd-main"><ShieldCheck size={14}/> E-RESET</div>
                </button>
             </div>
         )}
      </div>

      {/* COMMAND LIFECYCLE OVERLAY (Right edge) */}
      <div className="lifecycle-overlay">
         {droneIds.map(id => {
            const cs = drones[id]?.commandState;
            if (!cs || !cs.action) return null;
            if (dismissedCmds.has(cs.cmd_id)) return null;
            if (cs.state === 'ACCEPTED' && (nowMs - (cs.timestamp || nowMs)) > 5000) return null; // hide success after 5s
            
            let color = 'var(--text-muted)';
            let bg = 'rgba(255,255,255,0.85)';
            if (cs.state === 'ACCEPTED') { color = 'var(--success)'; bg = 'rgba(16, 185, 129, 0.1)'; }
            if (cs.state === 'FAILED' || cs.state === 'REJECTED' || cs.state === 'TIMEOUT') { color = 'var(--danger)'; bg = 'rgba(239, 68, 68, 0.1)'; }
            if (cs.state === 'MAVSDK_REQUESTED' || cs.state === 'BACKEND_RECEIVED') { color = 'var(--warning)'; }

            return (
               <div key={id} className="lifecycle-card" style={{ background: bg, borderColor: color, position: 'relative' }}>
                  <button 
                     onClick={() => setDismissedCmds(prev => new Set(prev).add(cs.cmd_id))}
                     style={{ position: 'absolute', top: '4px', right: '4px', background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
                  >
                     <X size={14} />
                  </button>
                  <div className="lc-header" style={{color: 'var(--text-main)', paddingRight: '16px'}}>{id}: <span style={{color}}>{cs.action.toUpperCase()}</span></div>
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
           --bg-color: #121212;
           --surface: #1E1E1E;
           --border: #333333;
           --text-main: #E0E0E0;
           --text-muted: #9E9E9E;
           --primary: #0066CC;
           --success: #388E3C;
           --warning: #F57C00;
           --danger: #D32F2F;
           --shadow-sm: 0 1px 2px rgba(0,0,0,0.2);
           --shadow-md: 0 4px 6px rgba(0,0,0,0.3);
        }

        .drone-control-view {
           display: flex;
           flex-direction: column;
           height: 100vh;
           width: 100vw;
           background-color: var(--bg-color);
           position: relative;
           overflow: hidden;
           font-family: 'Inter', sans-serif;
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
        
        /* Futuristic flight deck overrides */
        .drone-control-view {
           --bg-color: #05080c;
           --surface: rgba(8, 14, 20, 0.86);
           --surface-strong: rgba(10, 18, 26, 0.94);
           --border: rgba(93, 228, 255, 0.2);
           --text-main: #e7f4ff;
           --text-muted: #8ea3b8;
           --primary: #2f9dff;
           --success: #28d17c;
           --warning: #ffbf3d;
           --danger: #ff4b55;
           background:
             linear-gradient(90deg, rgba(93, 228, 255, 0.06) 1px, transparent 1px),
             linear-gradient(0deg, rgba(93, 228, 255, 0.04) 1px, transparent 1px),
             #05080c;
           background-size: 44px 44px, 44px 44px, auto;
        }

        .map-layer .leaflet-tile-pane {
           filter: saturate(0.74) contrast(1.16) brightness(0.58);
        }

        .map-layer::after {
           content: "";
           position: absolute;
           inset: 0;
           z-index: 500;
           pointer-events: none;
           background:
             linear-gradient(90deg, rgba(93, 228, 255, 0.07) 1px, transparent 1px),
             linear-gradient(0deg, rgba(93, 228, 255, 0.05) 1px, transparent 1px),
             radial-gradient(circle at 50% 42%, transparent 0 22%, rgba(5, 8, 12, 0.36) 72%),
             linear-gradient(180deg, rgba(5, 8, 12, 0.42), transparent 24%, rgba(5, 8, 12, 0.7));
           background-size: 84px 84px, 84px 84px, auto, auto;
        }

        .hud-top-bar,
        .hud-bottom-bar {
           background: rgba(5, 8, 12, 0.88);
           border-color: rgba(93, 228, 255, 0.18);
           box-shadow: 0 16px 40px rgba(0, 0, 0, 0.34);
           backdrop-filter: blur(18px);
        }

        .hud-top-bar {
           padding: 8px 12px;
        }

        .hud-btn,
        .hud-target-selector,
        .hud-target-selector select {
           color: var(--text-main);
        }

        .hud-btn,
        .hud-target-selector {
           background: rgba(11, 20, 29, 0.86);
           border: 1px solid rgba(93, 228, 255, 0.24);
           border-radius: 7px;
           box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.03);
        }

        .hud-btn {
           min-height: 30px;
           color: #cdefff;
        }

        .hud-gps-btn {
           color: #5de4ff;
        }

        .hud-status-item,
        .hud-status-text,
        .hud-mode-pill {
           min-height: 26px;
           display: inline-flex;
           align-items: center;
           gap: 5px;
           padding: 5px 8px;
           background: rgba(11, 20, 29, 0.78);
           border: 1px solid rgba(93, 228, 255, 0.16);
           border-radius: 999px;
        }

        .zone-pill {
           background: rgba(7, 11, 16, 0.72);
           border-color: color-mix(in srgb, var(--zone-color) 56%, transparent);
           color: var(--zone-color);
        }

        .hud-status-cards {
           padding: 8px 10px;
           background: rgba(5, 8, 12, 0.58);
           border-bottom: 1px solid rgba(93, 228, 255, 0.12);
           backdrop-filter: blur(12px);
        }

        .telemetry-card {
           min-width: 84px;
           background:
             linear-gradient(180deg, rgba(18, 31, 43, 0.9), rgba(7, 12, 18, 0.9));
           border: 1px solid rgba(93, 228, 255, 0.18);
           border-radius: 7px;
           box-shadow: inset 0 1px 0 rgba(255,255,255,0.06), 0 10px 22px rgba(0,0,0,0.24);
        }

        .zone-card {
           border-color: color-mix(in srgb, var(--zone-color) 52%, rgba(93, 228, 255, 0.18));
        }

        .pilot-card .t-sub {
           max-width: 120px;
           overflow: hidden;
           text-overflow: ellipsis;
           white-space: nowrap;
        }

        .t-header {
           color: #8ea3b8;
           letter-spacing: 0;
        }

        .t-main {
           color: #e7f4ff;
           font-family: "Consolas", "SFMono-Regular", monospace;
        }

        .flight-zone-banner {
           pointer-events: auto;
           align-self: center;
           width: min(760px, calc(100% - 24px));
           display: grid;
           grid-template-columns: 72px 1fr auto;
           align-items: center;
           gap: 12px;
           margin-top: 8px;
           padding: 10px 12px;
           background: rgba(5, 8, 12, 0.72);
           border: 1px solid color-mix(in srgb, var(--zone-color) 46%, transparent);
           border-left: 4px solid var(--zone-color);
           border-radius: 8px;
           box-shadow: 0 16px 36px rgba(0, 0, 0, 0.28);
           backdrop-filter: blur(18px);
        }

        .flight-zone-code {
           display: grid;
           place-items: center;
           min-height: 42px;
           border-radius: 6px;
           color: var(--zone-color);
           background: color-mix(in srgb, var(--zone-color) 16%, rgba(7, 11, 16, 0.9));
           font-size: 12px;
           font-weight: 900;
        }

        .flight-zone-title {
           font-size: 12px;
           font-weight: 800;
           color: var(--text-main);
        }

        .flight-zone-meta {
           margin-top: 3px;
           font-size: 10px;
           color: var(--text-muted);
           overflow: hidden;
           text-overflow: ellipsis;
           white-space: nowrap;
        }

        .flight-zone-action {
           white-space: nowrap;
        }

        .hud-middle {
           padding: 14px;
        }

        .hud-left {
           width: 236px;
        }

        .control-panel {
           background: rgba(7, 12, 18, 0.78);
           border: 1px solid rgba(93, 228, 255, 0.22);
           border-radius: 8px;
           box-shadow: inset 0 1px 0 rgba(255,255,255,0.05), 0 18px 36px rgba(0,0,0,0.28);
           backdrop-filter: blur(18px);
        }

        .panel-header {
           color: #b7cadc;
           letter-spacing: 0;
           text-align: left;
        }

        .preflight-list {
           gap: 8px;
        }

        .pf-row {
           padding: 6px 8px;
           background: rgba(255, 255, 255, 0.025);
           border: 1px solid rgba(255, 255, 255, 0.05);
           border-radius: 6px;
           color: var(--text-main);
        }

        .pf-row.good {
           color: #28d17c;
           border-color: rgba(40, 209, 124, 0.2);
        }

        .pf-row.danger {
           color: #ff6971;
           border-color: rgba(255, 75, 85, 0.22);
        }

        .mode-toggle {
           border: 1px solid rgba(93, 228, 255, 0.18);
           border-radius: 7px;
        }

        .mode-toggle button.active.primary {
           background: rgba(47, 157, 255, 0.24);
           color: #dff6ff;
        }

        .mode-toggle button.active.warning {
           background: rgba(255, 191, 61, 0.2);
           color: #ffe5a4;
        }

        .d-pad {
           gap: 7px;
        }

        .d-btn,
        .d-center {
           background: radial-gradient(circle at 50% 30%, rgba(93, 228, 255, 0.14), rgba(7, 12, 18, 0.94));
           border: 1px solid rgba(93, 228, 255, 0.26);
           border-radius: 50%;
           box-shadow: inset 0 1px 0 rgba(255,255,255,0.06), 0 10px 18px rgba(0,0,0,0.25);
           color: #dff6ff;
        }

        .d-center {
           width: 48px;
           height: 48px;
           border-radius: 50%;
        }

        .d-btn:active,
        .d-btn.active {
           background: radial-gradient(circle at 50% 35%, rgba(93, 228, 255, 0.42), rgba(47, 157, 255, 0.34));
           color: #ffffff;
           border-color: #5de4ff;
           box-shadow: 0 0 22px rgba(93, 228, 255, 0.28);
        }

        .mini-val {
           color: #5de4ff;
           font-family: "Consolas", "SFMono-Regular", monospace;
        }

        .form-panel select {
           background: rgba(5, 8, 12, 0.9);
           color: var(--text-main);
           border-color: rgba(93, 228, 255, 0.22);
        }

        .hud-bottom-bar {
           padding: 10px 12px;
        }

        .command-btn {
           border: 1px solid rgba(255, 255, 255, 0.08);
           border-radius: 7px;
           min-height: 54px;
           box-shadow: inset 0 1px 0 rgba(255,255,255,0.08), 0 12px 24px rgba(0,0,0,0.26);
        }

        .btn-arm { background: linear-gradient(180deg, #31b978, #147a4b); }
        .btn-disarm { background: linear-gradient(180deg, #6d2430, #3a1219); }
        .btn-hold { background: linear-gradient(180deg, #465563, #1e2933); }
        .btn-takeoff { background: linear-gradient(180deg, #2f9dff, #12609e); }
        .btn-land { background: linear-gradient(180deg, #ff4b55, #972530); }
        .btn-rtl { background: linear-gradient(180deg, #7dd3fc, #25637d); color: #061017; }
        .btn-emergency { background: linear-gradient(180deg, #b91c1c, #4a0b0f); }

        .super-key {
           border: 1px solid rgba(255, 255, 255, 0.3);
           box-shadow: 0 0 18px rgba(255, 75, 85, 0.16), inset 0 1px 0 rgba(255,255,255,0.1);
        }

        .lifecycle-card {
           background: rgba(5, 8, 12, 0.86) !important;
           border-radius: 8px;
           backdrop-filter: blur(14px);
        }

        .modal-content {
           background: rgba(8, 14, 20, 0.96);
           border: 1px solid rgba(93, 228, 255, 0.22);
           border-radius: 8px;
        }

        @media (orientation: landscape) and (max-height: 720px) {
           .hud-overlay {
              overflow: hidden;
           }

           .hud-top-bar {
              flex-wrap: nowrap;
              gap: 8px;
              min-height: 46px;
              padding: 6px 10px;
              overflow-x: auto;
              overflow-y: hidden;
           }

           .hud-top-bar::-webkit-scrollbar,
           .hud-status-cards::-webkit-scrollbar,
           .hud-bottom-bar::-webkit-scrollbar {
              display: none;
           }

           .hud-top-left,
           .hud-top-right {
              flex-wrap: nowrap;
              gap: 6px;
              flex-shrink: 0;
           }

           .hud-btn,
           .hud-target-selector,
           .hud-status-item,
           .hud-status-text,
           .hud-mode-pill {
              min-height: 34px;
              padding: 7px 9px;
              font-size: 10px;
              white-space: nowrap;
           }

           .hud-target-selector span {
              display: none;
           }

           .hud-target-selector select {
              max-width: 118px;
              font-size: 10px;
           }

           .hud-status-cards {
              height: 68px;
              padding: 7px 10px;
              gap: 7px;
              align-items: stretch;
              overflow-x: auto;
              flex-shrink: 0;
           }

           .telemetry-card {
              flex: 0 0 118px;
              min-width: 118px;
              padding: 7px 9px;
           }

           .t-header {
              font-size: 9px;
              margin-bottom: 4px;
           }

           .t-main {
              font-size: 15px;
           }

           .t-sub {
              font-size: 9px;
              max-width: 100%;
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
           }

           .flight-zone-banner {
              position: absolute;
              top: 126px;
              left: 50%;
              transform: translateX(-50%);
              width: min(680px, calc(100% - 320px));
              min-width: 360px;
              grid-template-columns: 58px 1fr auto;
              min-height: 50px;
              margin: 0;
              padding: 8px 10px;
              z-index: 24;
           }

           .flight-zone-code {
              min-height: 34px;
              font-size: 11px;
           }

           .flight-zone-title {
              font-size: 11px;
           }

           .flight-zone-meta {
              font-size: 9px;
           }

           .flight-zone-action {
              min-height: 30px;
              padding: 6px 8px;
           }

           .hud-middle {
              padding: 10px 12px 8px;
              gap: 10px;
              min-height: 0;
           }

           .hud-left {
              width: 214px;
              gap: 8px;
           }

           .hud-right {
              align-self: stretch;
              justify-content: flex-end;
              max-width: 440px;
           }

           .hud-left .hud-spacer,
           .hud-right .hud-spacer {
              display: none;
           }

           .control-panel {
              padding: 9px;
           }

           .panel-header {
              font-size: 9px;
              margin-bottom: 7px;
           }

           .preflight-list {
              gap: 5px;
           }

           .pf-row {
              padding: 5px 7px;
              font-size: 10px;
           }

           .pf-reason {
              font-size: 9px;
              margin-top: 6px;
              padding: 5px;
           }

           .mode-toggle button {
              padding: 8px 6px;
              font-size: 9px;
           }

           .right-controls-group {
              align-items: flex-end;
              gap: 7px;
           }

           .mini-panel {
              min-width: 58px;
           }

           .mini-val {
              font-size: 13px;
              margin: 3px 0;
           }

           .mini-unit {
              font-size: 8px;
           }

           .d-pad {
              gap: 5px;
           }

           .d-btn {
              width: 42px;
              height: 42px;
           }

           .h-btn {
              width: 38px;
              height: 38px;
           }

           .d-center {
              width: 42px;
              height: 42px;
           }

           .d-btn .d-label {
              font-size: 8px;
           }

           .hud-bottom-bar {
              min-height: 70px;
              padding: 7px 10px 8px;
              gap: 7px;
              overflow-x: auto;
              flex-shrink: 0;
           }

           .command-btn {
              flex: 0 0 122px;
              min-width: 122px;
              min-height: 54px;
              padding: 8px 6px;
           }

           .cmd-main {
              font-size: 11px;
           }

           .cmd-sub {
              font-size: 8px;
           }

           .lifecycle-overlay {
              top: 124px;
              width: 190px;
           }
        }

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
}
