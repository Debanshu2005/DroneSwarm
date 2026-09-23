import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { CommandAction } from '../protocol/messages';
import { Play, Pause, Square, Trash2, Plus, Upload, Map, Navigation, Route } from 'lucide-react';
import './MissionView.css';

export default function MissionView() {
  const { selectedDrones, drones, wsManager } = useDroneContext();
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
    if (!wsManager) return;
    Array.from(selectedDrones).forEach(id => {
       wsManager.send({
          msg_type: "mission_upload",
          sender_id: "gs_phone",
          target_id: id,
          timestamp: Date.now() / 1000.0,
          waypoints: waypoints
       });
    });
  };

  const sendMissionCmd = (type) => {
    if (!wsManager) return;
    Array.from(selectedDrones).forEach(id => {
       wsManager.send({
          msg_type: type,
          sender_id: "gs_phone",
          target_id: id,
          timestamp: Date.now() / 1000.0
       });
    });
  };

  const [goToLat, setGoToLat] = useState(0);
  const [goToLon, setGoToLon] = useState(0);
  const [goToAlt, setGoToAlt] = useState(10);
  
  const generateGoToPoint = () => {
    setWaypoints([{ lat: goToLat, lon: goToLon, alt: goToAlt, speed: 5, action: 'WAYPOINT' }]);
  };

  const isSwarmMission = selectedDrones.size > 1;

  return (
    <div className="mission-view-wrapper">
      <div className="view-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
         <div>
            <h2>Mission Planner</h2>
            <p className="text-muted">Upload and execute automated flight plans</p>
         </div>
         {isSwarmMission && <span className="status-badge badge-warning" style={{background: 'rgba(234, 179, 8, 0.2)', color: '#facc15', padding: '4px 8px', borderRadius: '4px', fontSize: '12px', fontWeight: 'bold'}}>SWARM MISSION</span>}
      </div>

      <div className="mission-grid">
         {/* Waypoint Editor */}
         <div className="glass-panel">
            <h3 className="section-title">
               <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}><Navigation size={18}/> Point-to-Point (Go To)</span>
            </h3>
            
            <div className="input-row">
               <input type="number" className="glass-input" placeholder="Latitude" value={goToLat} onChange={e => setGoToLat(parseFloat(e.target.value))}/>
               <input type="number" className="glass-input" placeholder="Longitude" value={goToLon} onChange={e => setGoToLon(parseFloat(e.target.value))}/>
               <input type="number" className="glass-input" style={{flex: '0 0 80px'}} placeholder="Alt (m)" value={goToAlt} onChange={e => setGoToAlt(parseFloat(e.target.value))}/>
            </div>
            
            <button className="glass-btn glass-btn-outline" onClick={generateGoToPoint}>
               <Map size={16} /> Generate Waypoint
            </button>

            <div className="divider" style={{ margin: '1rem 0' }}></div>

            <h3 className="section-title">
               <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}><Route size={18}/> Waypoints</span>
               <button className="glass-btn glass-btn-primary glass-btn-small" onClick={addWaypoint}>
                  <Plus size={16}/> Add WP
               </button>
            </h3>
            
            <div className="waypoints-list">
               {waypoints.length === 0 ? (
                  <div className="empty-state">
                     <Route size={32} />
                     <span>No waypoints added</span>
                  </div>
               ) : (
                  waypoints.map((wp, i) => (
                     <div key={i} className="waypoint-card">
                        <div className="waypoint-header">
                           <span className="waypoint-badge">WP {i+1}</span>
                           <button className="icon-btn-danger" onClick={() => removeWaypoint(i)}>
                              <Trash2 size={16}/>
                           </button>
                        </div>
                        
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                           <div className="input-row">
                              <input type="number" className="glass-input" placeholder="Lat" value={wp.lat} onChange={(e) => updateWaypoint(i, 'lat', parseFloat(e.target.value))}/>
                              <input type="number" className="glass-input" placeholder="Lon" value={wp.lon} onChange={(e) => updateWaypoint(i, 'lon', parseFloat(e.target.value))}/>
                           </div>
                           <div className="input-row">
                              <input type="number" className="glass-input" placeholder="Alt (m)" value={wp.alt} onChange={(e) => updateWaypoint(i, 'alt', parseFloat(e.target.value))}/>
                              <input type="number" className="glass-input" placeholder="Speed (m/s)" value={wp.speed} onChange={(e) => updateWaypoint(i, 'speed', parseFloat(e.target.value))}/>
                           </div>
                        </div>
                     </div>
                  ))
               )}
            </div>
         </div>

         {/* Mission Controls */}
         <div className="glass-panel">
            <h3 className="section-title">Execution Controls</h3>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginBottom: '8px' }}>
               <span className="sub-title">Target Drones</span>
               <span style={{ fontWeight: 600, color: '#e2e8f0' }}>
                  {selectedDrones.size === 0 ? 'None Selected' : Array.from(selectedDrones).join(', ')}
               </span>
            </div>

            <button className="glass-btn glass-btn-outline" onClick={handleUpload} disabled={waypoints.length === 0 || selectedDrones.size === 0}>
               <Upload size={18}/> Upload Mission
            </button>
            
            <div className="divider"></div>
            
            <button className="glass-btn glass-btn-success" disabled={selectedDrones.size === 0} onClick={() => sendMissionCmd('mission_start')}>
               <Play size={20}/> Start Mission
            </button>
            
            <div style={{ display: 'flex', gap: '1rem' }}>
               <button className="glass-btn glass-btn-primary" disabled={selectedDrones.size === 0} onClick={() => sendMissionCmd('mission_pause')}>
                  <Pause size={18}/> Pause
               </button>
               <button className="glass-btn glass-btn-danger" disabled={selectedDrones.size === 0} onClick={() => sendMissionCmd('mission_abort')}>
                  <Square size={18}/> Abort
               </button>
            </div>
            
            <div className="divider" style={{ margin: '1rem 0' }}></div>
            
            <h3 className="section-title">
               <span style={{ fontSize: '0.9rem' }}>Selected Drone Status</span>
            </h3>
            
            <div className="status-list">
               {selectedDrones.size === 0 ? (
                  <div className="empty-state" style={{ padding: '2rem 1rem' }}>
                     <span>No drones selected</span>
                  </div>
               ) : (
                  Array.from(selectedDrones).map(id => {
                     const d = drones[id];
                     const ms = d?.missionState?.status || 'none';
                     return (
                        <div key={id} className="status-item">
                           <span className="status-id">{id}</span>
                           <span className={`status-badge ${ms}`}>
                              {ms}
                           </span>
                        </div>
                     )
                  })
               )}
            </div>
         </div>
      </div>
    </div>
  );
}
