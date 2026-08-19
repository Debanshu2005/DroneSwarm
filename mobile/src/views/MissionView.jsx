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
    <div style={{display: 'flex', flexDirection: 'column', height: '100%', gap: '16px'}}>
      <div className="glass-panel" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px'}}>
         <h2>Mission Planner</h2>
         {isSwarmMission && <span className="status-badge badge-warning">SWARM MISSION</span>}
      </div>

      <div style={{display: 'flex', gap: '16px', flex: 1, minHeight: 0, flexWrap: 'wrap'}}>
         {/* Waypoint Editor */}
         <div className="glass-panel" style={{flex: 1, minWidth: '300px', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px'}}>
               <h3>Waypoints</h3>
               <button className="secondary-btn" style={{padding: '6px 12px'}} onClick={addWaypoint}><Plus size={16}/> Add WP</button>
            </div>
            
            <div style={{flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px', paddingRight: '4px'}}>
               {waypoints.length === 0 ? (
                  <div style={{textAlign: 'center', padding: '20px', color: 'var(--text-muted)'}}>No waypoints added.</div>
               ) : (
                  waypoints.map((wp, i) => (
                     <div key={i} style={{background: 'var(--bg-color)', border: '1px solid var(--border)', borderRadius: '8px', padding: '12px'}}>
                        <div style={{fontWeight: 'bold', borderBottom: '1px solid var(--border)', paddingBottom: '8px', marginBottom: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                           <span>WP {i+1}</span>
                           <button className="icon-btn" style={{padding:0, color:'var(--danger)'}} onClick={() => removeWaypoint(i)}><Trash2 size={16}/></button>
                        </div>
                        <div className="input-group" style={{flexDirection: 'row', gap: '8px', marginBottom: '8px'}}>
                           <input type="number" placeholder="Lat" value={wp.lat} onChange={(e) => updateWaypoint(i, 'lat', parseFloat(e.target.value))} style={{width: '50%'}}/>
                           <input type="number" placeholder="Lon" value={wp.lon} onChange={(e) => updateWaypoint(i, 'lon', parseFloat(e.target.value))} style={{width: '50%'}}/>
                        </div>
                        <div className="input-group" style={{flexDirection: 'row', gap: '8px'}}>
                           <input type="number" placeholder="Alt (m)" value={wp.alt} onChange={(e) => updateWaypoint(i, 'alt', parseFloat(e.target.value))} style={{width: '50%'}} title="Altitude (m)"/>
                           <input type="number" placeholder="Spd (m/s)" value={wp.speed} onChange={(e) => updateWaypoint(i, 'speed', parseFloat(e.target.value))} style={{width: '50%'}} title="Speed (m/s)"/>
                        </div>
                     </div>
                  ))
               )}
            </div>
         </div>

         {/* Mission Controls */}
         <div className="glass-panel" style={{flex: 1, minWidth: '280px', display: 'flex', flexDirection: 'column', gap: '16px'}}>
            <h3>Execution</h3>
            
            <div style={{display: 'flex', flexDirection: 'column'}}>
               <span style={{fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase'}}>Target Drones</span>
               <span style={{fontWeight: 600}}>{selectedDrones.size === 0 ? 'None Selected' : Array.from(selectedDrones).join(', ')}</span>
            </div>

            <button className="primary-btn" onClick={handleUpload} disabled={waypoints.length === 0 || selectedDrones.size === 0} style={{display:'flex', justifyContent:'center', gap:'8px', width: '100%'}}>
               <Upload size={18}/> UPLOAD MISSION
            </button>
            
            <div style={{height: '1px', background: 'var(--border)', margin: '4px 0'}}></div>
            
            <button className="action-btn" style={{borderColor: 'var(--success)', color: 'var(--success)'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_START)}>
               <Play size={20}/> START MISSION
            </button>
            
            <div style={{display: 'flex', gap: '12px'}}>
               <button className="action-btn" style={{flex: 1, padding: '12px'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_PAUSE)}>
                  <Pause size={18}/> PAUSE
               </button>
               <button className="action-btn" style={{flex: 1, padding: '12px', borderColor: 'var(--danger)', color: 'var(--danger)'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_ABORT)}>
                  <Square size={18}/> ABORT
               </button>
            </div>
            
            <div style={{marginTop: 'auto'}}>
               <h4 style={{fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px'}}>Selected Drone Status</h4>
               <div style={{fontSize: '13px', display: 'flex', flexDirection: 'column', gap: '8px'}}>
                  {Array.from(selectedDrones).map(id => {
                     const d = drones[id];
                     const ms = d?.missionState?.status || 'none';
                     return (
                        <div key={id} style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '4px'}}>
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
