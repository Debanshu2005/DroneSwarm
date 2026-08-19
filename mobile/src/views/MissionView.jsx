import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { CommandAction } from '../protocol/messages';
import { Route, Play, Pause, Square, Trash2, Plus, Upload, CheckCircle } from 'lucide-react';

export default function MissionView() {
  const { selectedDrones, drones, sendCommand } = useDroneContext();
  const [waypoints, setWaypoints] = useState([]);
  
  const addWaypoint = () => {
    setWaypoints([...waypoints, { lat: 0, lon: 0, alt: 10, speed: 5, action: 'WAYPOINT' }]);
  };

  const updateWaypoint = (index, field, value) => {
    const newWps = [...waypoints];
    newWps[index][field] = value;
    setWaypoints(newWps);
  };

  const removeWaypoint = (index) => {
    setWaypoints(waypoints.filter((_, i) => i !== index));
  };

  const handleUpload = () => {
    // Note: MISSION_UPLOAD is currently supported in backend, passing array of waypoints
    sendCommand(CommandAction.MISSION_UPLOAD, { waypoints });
  };

  const isSwarmMission = selectedDrones.size > 1;

  return (
    <div className="view-container fade-in" style={{display: 'flex', flexDirection: 'column', height: '100%'}}>
      <div className="view-header">
         <h2>Mission Planner</h2>
         {isSwarmMission && <div className="status-badge warning-bg">SWARM MISSION</div>}
      </div>

      <div style={{display: 'flex', gap: '15px', flex: 1, minHeight: 0}}>
         {/* Waypoint Editor */}
         <div className="glass-panel" style={{flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px'}}>
               <h3>Waypoints</h3>
               <button className="secondary-btn" onClick={addWaypoint}><Plus size={16}/> Add WP</button>
            </div>
            
            <div style={{flex: 1, overflowY: 'auto', paddingRight: '10px'}}>
               {waypoints.length === 0 ? (
                  <div className="no-drone-msg" style={{fontSize: '0.9rem'}}>No waypoints added.</div>
               ) : (
                  waypoints.map((wp, i) => (
                     <div key={i} className="waypoint-card">
                        <div style={{fontWeight: 'bold', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '5px', marginBottom: '5px', display: 'flex', justifyContent: 'space-between'}}>
                           <span>WP {i+1}</span>
                           <button className="icon-btn" style={{padding:0, color:'var(--danger)'}} onClick={() => removeWaypoint(i)}><Trash2 size={16}/></button>
                        </div>
                        <div className="input-group" style={{flexDirection: 'row', gap: '5px', marginBottom: '5px'}}>
                           <input type="number" placeholder="Lat" value={wp.lat} onChange={(e) => updateWaypoint(i, 'lat', parseFloat(e.target.value))} style={{width: '50%'}}/>
                           <input type="number" placeholder="Lon" value={wp.lon} onChange={(e) => updateWaypoint(i, 'lon', parseFloat(e.target.value))} style={{width: '50%'}}/>
                        </div>
                        <div className="input-group" style={{flexDirection: 'row', gap: '5px'}}>
                           <input type="number" placeholder="Alt (m)" value={wp.alt} onChange={(e) => updateWaypoint(i, 'alt', parseFloat(e.target.value))} style={{width: '50%'}} title="Altitude (m)"/>
                           <input type="number" placeholder="Spd (m/s)" value={wp.speed} onChange={(e) => updateWaypoint(i, 'speed', parseFloat(e.target.value))} style={{width: '50%'}} title="Speed (m/s)"/>
                        </div>
                     </div>
                  ))
               )}
            </div>
         </div>

         {/* Mission Controls */}
         <div className="glass-panel" style={{width: '280px', display: 'flex', flexDirection: 'column', gap: '15px'}}>
            <h3>Execution</h3>
            
            <div className="diag-item" style={{alignItems: 'flex-start'}}>
               <span className="diag-label">Target Drones</span>
               <span className="diag-val">{selectedDrones.size === 0 ? 'None Selected' : Array.from(selectedDrones).join(', ')}</span>
            </div>

            <button className="primary-btn" onClick={handleUpload} disabled={waypoints.length === 0 || selectedDrones.size === 0} style={{display:'flex', justifyContent:'center', gap:'8px'}}>
               <Upload size={18}/> UPLOAD MISSION
            </button>
            
            <div style={{height: '1px', background: 'var(--glass-border)', margin: '10px 0'}}></div>
            
            <button className="control-btn" style={{borderColor: 'var(--success)', color: 'var(--success)'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_START)}>
               <Play size={20}/> START MISSION
            </button>
            
            <div style={{display: 'flex', gap: '10px'}}>
               <button className="control-btn" style={{flex: 1, padding: '0.6rem'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_PAUSE)}>
                  <Pause size={18}/> PAUSE
               </button>
               <button className="control-btn" style={{flex: 1, padding: '0.6rem', borderColor: 'var(--danger)', color: 'var(--danger)'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_ABORT)}>
                  <Square size={18}/> ABORT
               </button>
            </div>
            
            <div style={{marginTop: 'auto'}}>
               <h4 style={{fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '5px'}}>Selected Drone Status</h4>
               <div style={{fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: '4px'}}>
                  {Array.from(selectedDrones).map(id => {
                     const d = drones[id];
                     const ms = d?.missionState?.status || 'none';
                     return (
                        <div key={id} style={{display: 'flex', justifyContent: 'space-between'}}>
                           <span>{id}:</span>
                           <span className={ms === 'running' ? 'good' : ms === 'aborted' ? 'danger' : ''}>{ms.toUpperCase()}</span>
                        </div>
                     )
                  })}
               </div>
            </div>
         </div>
      </div>
    </div>
  );
}
