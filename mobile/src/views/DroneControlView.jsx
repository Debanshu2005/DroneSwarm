import React, { useState, useRef, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import {
  ShieldAlert, ShieldCheck, Navigation, ArrowUp, ArrowDown, Activity,
  ArrowLeft, Square, RotateCcw, RotateCw, ArrowRight, Map, Video, Menu,
  Battery, Signal, Wifi, Compass, Gauge, AlertTriangle, Lock, Unlock,
  Plus, Minus, Settings
} from 'lucide-react';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { MapContainer, TileLayer, Marker, Polyline, Popup, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';

import { CommandAction } from '../protocol/messages';


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

export default function DroneControlView({ setView }) {
  const { drones, nowMs, sendCommand, isConnected, indoorMode, wsManager } = useDroneContext();

  const [targetMode, setTargetMode] = useState('ALL'); // 'ALL' or droneId
  const [targetDroneId, setTargetDroneId] = useState(null);

  // Formation defaults
  const [formationType, setFormationType] = useState('V');
  const [formationSpacing, setFormationSpacing] = useState(2.0);
  const [formationSpeed, setFormationSpeed] = useState(0.5);

  // Speed defaults to 0.10 m/s
  const [movementSpeed, setMovementSpeed] = useState(0.10);
  const [verticalSpeed, setVerticalSpeed] = useState(0.10);
  const [yawRate, setYawRate] = useState(15.0);
  const [targetAltitude, setTargetAltitude] = useState(1.0);


  const [showConfirmModal, setShowConfirmModal] = useState(null); // { action, params, message }

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
     
  let mapCenter = [22.315, 87.310]; // Fallback
  if (activeDrone && tel.latitude && tel.longitude && tel.latitude !== 0) {
      mapCenter = [tel.latitude, tel.longitude];
  } else {
      // Find any drone with valid GPS
      const validDroneId = droneIds.find(id => drones[id]?.telemetry?.latitude && drones[id]?.telemetry?.latitude !== 0);
      if (validDroneId) {
          mapCenter = [drones[validDroneId].telemetry.latitude, drones[validDroneId].telemetry.longitude];
      }
  }

  return () => {
         document.removeEventListener('visibilitychange', handleVisibilityChange);
         stopMove();
     };
     // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Aggregate States
  const isHeartbeatHealthy = activeDrone ? (nowMs - activeDrone.lastSeen) < 2000 : false;
  const isTelemetryHealthy = isHeartbeatHealthy && isConnected === "CONNECTED";
  const isPx4Connected = tel.flight_mode && tel.flight_mode !== "disconnected" && tel.flight_mode !== "UNKNOWN";
  const isBatteryAcceptable = (tel.battery_level || 0) >= 15;
  const isGpsValid = tel.gps_valid === true;
  const isHomeValid = tel.home_valid === true;
  const isArmable = tel.is_armable === true;
  const isHealthy = tel.health_all_ok === true;

  const connectedDronesCount = droneIds.filter(id => (nowMs - drones[id].lastSeen) < 2000).length;

  
  let mapCenter = [22.315, 87.310]; // Fallback
  if (activeDrone && tel.latitude && tel.longitude && tel.latitude !== 0) {
      mapCenter = [tel.latitude, tel.longitude];
  } else {
      // Find any drone with valid GPS
      const validDroneId = droneIds.find(id => drones[id]?.telemetry?.latitude && drones[id]?.telemetry?.latitude !== 0);
      if (validDroneId) {
          mapCenter = [drones[validDroneId].telemetry.latitude, drones[validDroneId].telemetry.longitude];
      }
  }

  return (
    <div className="drone-control-view" style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', backgroundColor: 'var(--bg-color)', position: 'relative', overflow: 'hidden' }}>

      {/* BACKGROUND MAP LAYER (Z: 0) */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0, backgroundColor: '#E2E8F0' }}>
         <ErrorBoundary fallback={
            <div style={{width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
                <div style={{color: '#94A3B8', fontSize: '14px', fontWeight: 'bold'}}>MAP OFFLINE</div>
                <div style={{color: '#94A3B8', fontSize: '12px'}}>Flight controls remain fully active.</div>
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

      {/* HEADER & TELEMETRY STRIP (Z: 20) */}
      <div style={{ zIndex: 20, display: 'flex', flexDirection: 'column', backgroundColor: 'var(--surface)', borderBottom: '1px solid var(--border)', boxShadow: 'var(--shadow-sm)' }}>
         
         {/* Top Header */}
          <div style={{display: 'flex', padding: '4px 12px', alignItems: 'center', justifyContent: 'space-between'}}>
              <div style={{display: 'flex', alignItems: 'center', gap: '6px'}}>
                  {/* BACK BUTTON */}
                  <button onClick={() => setView('DASHBOARD')} style={{background: 'var(--bg-color)', border: '1px solid var(--border)', borderRadius: '6px', padding: '4px 8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px', fontWeight: 'bold', color: 'var(--text-muted)'}}>
                    <Menu size={14}/> ← BACK
                  </button>
                  
                  {/* TARGET DROPDOWN */}
                  <div style={{display: 'flex', alignItems: 'center', gap: '4px', background: 'var(--bg-color)', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--border)'}}>
                     <span style={{fontSize: '9px', fontWeight: 'bold', color: 'var(--text-muted)'}}>TARGET:</span>
                     <select value={targetMode === 'ALL' ? 'ALL' : targetDroneId || ''} onChange={handleTargetChange} style={{border: 'none', background: 'transparent', fontSize: '10px', fontWeight: 'bold', outline: 'none', cursor: 'pointer', color: 'var(--primary)'}}>
                        <option value="ALL">ALL DRONES</option>
                        {droneIds.map(id => <option key={id} value={id}>{id}</option>)}
                     </select>
                  </div>
              </div>
              {/* Compact Status Row */}
              <div style={{display: 'flex', alignItems: 'center', gap: '6px'}}>
                 <div style={{display: 'flex', alignItems: 'center', gap: '3px'}}>
                    <div style={{width: '6px', height: '6px', borderRadius: '50%', background: isConnected === 'CONNECTED' ? 'var(--success)' : 'var(--danger)'}}></div>
                    <span style={{fontSize: '8px', fontWeight: 'bold', color: 'var(--text-muted)'}}>{isConnected === 'CONNECTED' ? 'ONLINE' : 'OFFLINE'}</span>
                 </div>
                 <div style={{display: 'flex', alignItems: 'center', gap: '3px'}}>
                    <div style={{width: '6px', height: '6px', borderRadius: '50%', background: connectedDronesCount > 0 ? 'var(--success)' : 'var(--danger)'}}></div>
                    <span style={{fontSize: '8px', fontWeight: 'bold', color: 'var(--text-muted)'}}>{connectedDronesCount} DRN</span>
                 </div>
                 <div style={{display: 'flex', alignItems: 'center', gap: '3px'}}>
                    <div style={{width: '6px', height: '6px', borderRadius: '50%', background: isPx4Connected ? 'var(--success)' : 'var(--danger)'}}></div>
                    <span style={{fontSize: '8px', fontWeight: 'bold', color: 'var(--text-muted)'}}>PX4</span>
                 </div>
                 <span style={{fontSize: '9px', fontWeight: 'bold', color: tel.armed_state === 'ARMED' ? 'var(--danger)' : 'var(--success)'}}>{tel.armed_state || 'DISARMED'}</span>
                 <span style={{fontSize: '8px', fontWeight: 'bold', color: 'var(--text-muted)'}}>{tel.flight_mode || '---'}</span>
                 <button onClick={() => setView('SETTINGS')} style={{background: 'var(--bg-color)', border: '1px solid var(--border)', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', fontSize: '8px', fontWeight: 'bold', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '2px'}}>
                    <Settings size={10}/> SET
                 </button>
              </div>
          </div>
             
         {/* Telemetry Strip */}
          <div style={{display: 'flex', gap: '3px', padding: '3px 8px', backgroundColor: 'var(--bg-color)'}}>
            <div className="telemetry-card" style={{flex: 1}}>
               <div className="t-header"><Battery size={10}/> BAT</div>
               <div className={`t-main ${tel.battery_level > 20 ? 'good' : 'danger'}`}>{tel.battery_level != null ? `${tel.battery_level.toFixed(0)}%` : '--'}</div>
               <div className="t-sub">{tel.voltage ? `${tel.voltage.toFixed(1)}V` : '--'}</div>
            </div>
            <div className="telemetry-card" style={{flex: 1}}>
               <div className="t-header"><Compass size={10}/> GPS</div>
               <div className={`t-main ${isGpsValid ? 'good' : 'danger'}`}>{isGpsValid ? 'FIX' : 'NO FIX'}</div>
               <div className="t-sub">{tel.satellites || 0} Sat</div>
            </div>
            <div className="telemetry-card" style={{flex: 1}}>
               <div className="t-header"><Navigation size={10}/> HOME</div>
               <div className={`t-main ${isHomeValid ? 'good' : 'danger'}`}>{isHomeValid ? 'OK' : 'N/A'}</div>
               <div className="t-sub">{tel.distance_to_home != null ? `${tel.distance_to_home.toFixed(0)}m` : '--'}</div>
            </div>
            <div className="telemetry-card" style={{flex: 1}}>
               <div className="t-header"><ArrowUp size={10}/> ALT</div>
               <div className="t-main">{tel.altitude != null ? `${tel.altitude.toFixed(1)}` : '--'}</div>
               <div className="t-sub">m AGL</div>
            </div>
            <div className="telemetry-card" style={{flex: 1}}>
               <div className="t-header"><Gauge size={10}/> SPD</div>
               <div className="t-main">{tel.ground_speed != null ? `${tel.ground_speed.toFixed(1)}` : '--'}</div>
               <div className="t-sub">m/s</div>
            </div>
            <div className="telemetry-card" style={{flex: 1}}>
               <div className="t-header"><RotateCw size={10}/> HDG</div>
               <div className="t-main">{tel.heading != null ? `${tel.heading.toFixed(0)}°` : '--'}</div>
            </div>
          </div>
      </div>

      {/* LEFT OVERLAY: HORIZONTAL MOVEMENT D-PAD (Z: 10) */}
       <div style={{ position: 'absolute', bottom: '56px', left: '8px', zIndex: 10 }}>
          <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)'}}>
             <div className="panel-header" style={{textAlign: 'center', marginBottom: '4px'}}>MOVE</div>
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

       {/* RIGHT OVERLAY: VERTICAL, YAW, SPEED, ALT (Z: 10) */}
       <div style={{ position: 'absolute', bottom: '56px', right: '8px', zIndex: 10, display: 'flex', gap: '3px' }}>
          
          {/* Speed Control */}
          <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minWidth: '42px'}}>
              <div className="panel-header" style={{textAlign: 'center', marginBottom: '2px'}}>SPD</div>
              <button className="d-btn h-btn" onClick={() => setMovementSpeed(Math.min(5.0, movementSpeed + 0.05))}><Plus size={12}/></button>
              <div style={{fontSize: '12px', fontWeight: 'bold', color: 'var(--primary)', margin: '2px 0'}}>{movementSpeed.toFixed(2)}</div>
              <div style={{fontSize: '8px', color: 'var(--text-muted)'}}>m/s</div>
              <button className="d-btn h-btn" onClick={() => setMovementSpeed(Math.max(0.05, movementSpeed - 0.05))}><Minus size={12}/></button>
          </div>
          {/* Altitude Hold */}
          <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minWidth: '42px'}}>
              <div className="panel-header" style={{textAlign: 'center', marginBottom: '2px'}}>ALT</div>
              <button className="d-btn h-btn" onClick={() => setTargetAltitude(targetAltitude + 0.5)}><Plus size={12}/></button>
              <div style={{fontSize: '12px', fontWeight: 'bold', color: 'var(--primary)', margin: '2px 0'}}>{targetAltitude.toFixed(1)}</div>
              <div style={{fontSize: '8px', color: 'var(--text-muted)'}}>m</div>
              <button className="d-btn h-btn" onClick={() => setTargetAltitude(Math.max(0.5, targetAltitude - 0.5))}><Minus size={12}/></button>
          </div>

          {/* Formation Control */}
          <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minWidth: '50px'}}>
              <div className="panel-header" style={{textAlign: 'center', marginBottom: '2px'}}>FORM</div>
              <select value={formationType} onChange={e => setFormationType(e.target.value)} style={{fontSize: '9px', padding: '2px', marginBottom: '4px', width: '48px', borderRadius: '4px', border: '1px solid var(--border)'}}>
                 <option value="V">V</option>
                 <option value="COLUMN">COL</option>
                 <option value="LINE">LINE</option>
                 <option value="SQUARE">SQ</option>
                 <option value="GRID">GRID</option>
                 <option value="CIRCLE">CIR</option>
              </select>
              <button className="d-btn h-btn" style={{fontSize: '8px', width: '48px'}} onClick={() => requestCommand(CommandAction.FORMATION_UPDATE, { type: formationType, spacing: formationSpacing })}>APPLY</button>
          </div>

          {/* Vertical / Yaw D-Pad */}
          <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(8px)'}}>
             <div className="panel-header" style={{textAlign: 'center', marginBottom: '2px'}}>VERT/YAW</div>
             <div className="d-pad">
               <button className={`d-btn h-btn ${activeMoveParams?.yaw_rate < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({yaw_rate: -yawRate}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                 <RotateCcw size={12}/><span className="d-label">YL</span>
               </button>
               <button className={`d-btn h-btn ${activeMoveParams?.vz < 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vz: -verticalSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                 <ArrowUp size={12}/><span className="d-label">UP</span>
               </button>
               <button className={`d-btn h-btn ${activeMoveParams?.yaw_rate > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({yaw_rate: yawRate}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                 <RotateCw size={12}/><span className="d-label">YR</span>
               </button>
               <div></div>
               <button className={`d-btn h-btn ${activeMoveParams?.vz > 0 ? 'active' : ''}`} onPointerDown={(e) => { e.preventDefault(); startMove({vz: verticalSpeed}); }} onPointerUp={stopMove} onPointerLeave={stopMove} onContextMenu={(e) => e.preventDefault()}>
                 <ArrowDown size={12}/><span className="d-label">DN</span>
               </button>
               <div></div>
             </div>
          </div>
       </div>

       {/* BOTTOM COMMAND BAR (Z: 30) */}
       <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 30, display: 'flex', padding: '4px 8px', backgroundColor: 'var(--surface)', borderTop: '1px solid var(--border)', gap: '3px', boxShadow: '0 -2px 6px -1px rgba(0,0,0,0.1)' }}>
          <button className="command-btn btn-arm" onClick={() => requestCommand(CommandAction.ARM, null, true)}>
             <Lock size={12}/> ARM
          </button>
          <button className="command-btn btn-disarm" onClick={() => requestCommand(CommandAction.DISARM, null, true)}>
             <Unlock size={12}/> DISARM
          </button>
          <button className="command-btn btn-hold" onClick={() => requestCommand(CommandAction.HOVER, null, false)}>
             <Square size={12}/> HOLD
          </button>
          <button className="command-btn btn-takeoff" onClick={() => requestCommand(CommandAction.TAKEOFF, { altitude_m: targetAltitude }, true)}>
             <ArrowUp size={12}/> TAKEOFF
          </button>
          {/* LAND & RTL = SUPER KEYS - always execute immediately */}
          <button className="command-btn btn-land super-key" onClick={() => requestCommand(CommandAction.LAND)}>
             <ArrowDown size={14}/> LAND
          </button>
          <button className="command-btn btn-rtl super-key" onClick={() => requestCommand(CommandAction.RTL)}>
             <Navigation size={14}/> RTL
          </button>
          <button className="command-btn btn-emergency" onClick={() => requestCommand(CommandAction.EMERGENCY, null, true)}>
             <AlertTriangle size={12}/> E-STOP
          </button>
       </div>

      
      {/* COMMAND LIFECYCLE OVERLAY (Z: 40) */}
      <div style={{ position: 'absolute', top: '80px', right: '24px', zIndex: 40, display: 'flex', flexDirection: 'column', gap: '4px', width: '220px', pointerEvents: 'none' }}>
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
               <div key={id} style={{ padding: '8px 12px', background: bg, backdropFilter: 'blur(4px)', borderRadius: '6px', fontSize: '11px', border: `1px solid ${color}`, boxShadow: 'var(--shadow-sm)' }}>
                  <div style={{fontWeight: 'bold', marginBottom: '2px', color: 'var(--text-main)'}}>{id}: <span style={{color}}>{cs.action.toUpperCase()}</span></div>
                  <div style={{color}}>{cs.state}</div>
                  {cs.reason && <div style={{color: 'var(--danger)', marginTop: '2px', fontSize: '10px'}}>{cs.reason}</div>}
               </div>
            );
         })}
      </div>

      {/* CONFIRMATION MODAL */}
      {showConfirmModal && (
         <div className="modal-overlay">
            <div className="modal-content" style={{maxWidth: '400px'}}>
               <h2 style={{marginTop: 0}}>Confirm Action</h2>
               <p>{showConfirmModal.message}</p>
               <div style={{display: 'flex', gap: '4px', marginTop: '24px'}}>
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

        .status-indicator { display: flex; flex-direction: column; align-items: flex-start; justify-content: center; min-width: max-content; }
        .status-dot { width: 6px; height: 6px; border-radius: 50%; margin-bottom: 2px; }
        .status-dot.good { background-color: var(--success); box-shadow: 0 0 4px var(--success); }
        .status-dot.danger { background-color: var(--danger); }
        .status-text { display: flex; flex-direction: column; }
        .status-text .label { font-size: 8px; color: var(--text-muted); font-weight: bold; line-height: 1; margin-bottom: 2px; }
        .status-text .val { font-size: 10px; font-weight: 800; color: var(--text-main); line-height: 1; }

        .telemetry-card {
           background: var(--surface);
           border: 1px solid var(--border);
           border-radius: 6px;
           padding: 4px 6px;
           display: flex;
           flex-direction: column;
           box-shadow: var(--shadow-sm);
           min-width: 60px;
        }
        .t-header { font-size: 8px; color: var(--text-muted); font-weight: bold; display: flex; align-items: center; gap: 4px; margin-bottom: 2px; }
        .t-main { font-size: 13px; font-weight: 800; font-family: monospace; line-height: 1; margin-bottom: 2px; }
        .t-main.good { color: var(--success); }
        .t-main.danger { color: var(--danger); }
        .t-sub { font-size: 8px; color: var(--text-muted); line-height: 1; }

        .control-panel {
           background: rgba(255, 255, 255, 0.9);
           border: 1px solid var(--border);
           border-radius: 8px;
           padding: 6px;
           box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .panel-header { font-size: 9px; font-weight: bold; color: var(--text-muted); text-transform: uppercase; }

        .d-pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
        .d-btn {
           background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px;
           display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
           width: 40px; height: 40px; cursor: pointer; color: var(--text-main); user-select: none; touch-action: none;
           transition: all 0.1s;
        }
        .h-btn { width: 36px; height: 36px; }
        .d-btn .d-label { font-size: 7px; font-weight: bold; }
        .d-btn:active, .d-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); transform: scale(0.95); }
        .d-center { display: flex; align-items: center; justify-content: center; color: var(--text-muted); cursor: pointer; }

        .command-btn {
           flex: 1; display: flex; flex-direction: row; align-items: center; justify-content: center; gap: 4px;
           height: 40px; border-radius: 6px; border: none; font-weight: bold; font-size: 10px;
           cursor: pointer; transition: transform 0.1s, opacity 0.2s; color: #fff;
           white-space: nowrap;
        }
        .command-btn:active { transform: scale(0.95); }
        .command-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

        .btn-arm { background: var(--success); }
        .btn-disarm { background: #991B1B; }
        .btn-hold { background: #4B5563; }
        .btn-takeoff { background: var(--primary); }
        .btn-land { background: #DC2626; }
        .btn-rtl { background: #7C3AED; }
        .btn-emergency { background: #7F1D1D; flex: 1.3; font-size: 9px; }
        .super-key { flex: 1.3; font-size: 12px; font-weight: 900; box-shadow: 0 0 8px rgba(0,0,0,0.3); border: 2px solid rgba(255,255,255,0.4); }

        .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .modal-content { background: var(--surface); padding: 24px; border-radius: 12px; width: 100%; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
      `}} />
    </div>
  );
}
