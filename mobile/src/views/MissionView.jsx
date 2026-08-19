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
    <div className="view-container">
      <div className="view-header" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
         <div>
            <h2>Mission Planner</h2>
            <p className="text-muted">Upload and execute automated flight plans</p>
         </div>
         {isSwarmMission && <span className="status-badge badge-warning">SWARM MISSION</span>}
      </div>

      <div style={{display: 'flex', gap: '24px', flex: 1, minHeight: 0, flexWrap: 'wrap'}}>
         {/* Waypoint Editor */}
         <div className="card" style={{flex: 1, minWidth: '350px', display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px'}}>
               <h3 style={{fontSize: '16px', fontWeight: 600}}>Waypoints</h3>
               <button className="btn btn-secondary" style={{padding: '6px 12px', display: 'flex', alignItems: 'center', gap: '8px'}} onClick={addWaypoint}><Plus size={16}/> Add WP</button>
            </div>
            
            <div style={{flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px', paddingRight: '4px'}}>
               {waypoints.length === 0 ? (
                  <div style={{textAlign: 'center', padding: '20px', color: 'var(--text-muted)'}}>No waypoints added.</div>
               ) : (
                  waypoints.map((wp, i) => (
                     <div key={i} style={{background: 'var(--bg-main)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px'}}>
                        <div style={{fontWeight: 'bold', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px', marginBottom: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                           <span style={{fontSize: '14px'}}>WP {i+1}</span>
                           <button className="icon-btn" style={{padding:0, color:'#ef4444', background: 'transparent', border: 'none', cursor: 'pointer'}} onClick={() => removeWaypoint(i)}><Trash2 size={16}/></button>
                        </div>
                        <div className="input-group" style={{flexDirection: 'row', gap: '8px', marginBottom: '8px'}}>
                           <input type="number" className="input-field" placeholder="Lat" value={wp.lat} onChange={(e) => updateWaypoint(i, 'lat', parseFloat(e.target.value))} style={{width: '50%', padding: '8px'}}/>
                           <input type="number" className="input-field" placeholder="Lon" value={wp.lon} onChange={(e) => updateWaypoint(i, 'lon', parseFloat(e.target.value))} style={{width: '50%', padding: '8px'}}/>
                        </div>
                        <div className="input-group" style={{flexDirection: 'row', gap: '8px'}}>
                           <input type="number" className="input-field" placeholder="Alt (m)" value={wp.alt} onChange={(e) => updateWaypoint(i, 'alt', parseFloat(e.target.value))} style={{width: '50%', padding: '8px'}} title="Altitude (m)"/>
                           <input type="number" className="input-field" placeholder="Spd (m/s)" value={wp.speed} onChange={(e) => updateWaypoint(i, 'speed', parseFloat(e.target.value))} style={{width: '50%', padding: '8px'}} title="Speed (m/s)"/>
                        </div>
                     </div>
                  ))
               )}
            </div>
         </div>

         {/* Mission Controls */}
         <div className="card" style={{flex: 1, minWidth: '350px', display: 'flex', flexDirection: 'column', gap: '20px'}}>
            <h3 style={{fontSize: '16px', fontWeight: 600}}>Execution</h3>
            
            <div style={{display: 'flex', flexDirection: 'column', background: 'var(--bg-main)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-color)'}}>
               <span style={{fontSize: '12px', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px'}}>Target Drones</span>
               <span style={{fontWeight: 600}}>{selectedDrones.size === 0 ? 'None Selected' : Array.from(selectedDrones).join(', ')}</span>
            </div>

            <button className="btn btn-primary" onClick={handleUpload} disabled={waypoints.length === 0 || selectedDrones.size === 0} style={{display:'flex', justifyContent:'center', gap:'8px', width: '100%', padding: '12px'}}>
               <Upload size={18}/> UPLOAD MISSION
            </button>
            
            <div style={{height: '1px', background: 'var(--border-color)', margin: '4px 0'}}></div>
            
            <button className="btn btn-primary" style={{background: '#10b981', borderColor: '#10b981', display: 'flex', justifyContent: 'center', gap: '8px', padding: '12px'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_START)}>
               <Play size={20}/> START MISSION
            </button>
            
            <div style={{display: 'flex', gap: '12px'}}>
               <button className="btn btn-secondary" style={{flex: 1, padding: '12px', display: 'flex', justifyContent: 'center', gap: '8px'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_PAUSE)}>
                  <Pause size={18}/> PAUSE
               </button>
               <button className="btn btn-primary" style={{flex: 1, padding: '12px', background: '#ef4444', borderColor: '#ef4444', display: 'flex', justifyContent: 'center', gap: '8px'}} disabled={selectedDrones.size === 0} onClick={() => sendCommand(CommandAction.MISSION_ABORT)}>
                  <Square size={18}/> ABORT
               </button>
            </div>
            
            <div style={{marginTop: 'auto', background: 'var(--bg-main)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border-color)'}}>
               <h4 style={{fontSize: '12px', color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase'}}>Selected Drone Status</h4>
               <div style={{fontSize: '13px', display: 'flex', flexDirection: 'column', gap: '12px'}}>
                  {Array.from(selectedDrones).map(id => {
                     const d = drones[id];
                     const ms = d?.missionState?.status || 'none';
                     return (
                        <div key={id} style={{display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border-color)', paddingBottom: '8px'}}>
                           <span style={{fontWeight: 500}}>{id}:</span>
                           <span style={{fontWeight: 600, color: ms === 'running' ? '#10b981' : ms === 'aborted' ? '#ef4444' : 'var(--text-muted)'}}>{ms.toUpperCase()}</span>
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
