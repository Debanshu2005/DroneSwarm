import React, { useState, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { useDeviceLocation } from '../hooks/useDeviceLocation';
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { Navigation, Compass, MapPin, Signal, AlertTriangle, AlertCircle } from 'lucide-react';
import { calculateDistance, calculateBearing } from '../utils/geoUtils';

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

const createDroneIcon = (color) => {
  const svg = `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="transform-origin: center;">
      <path d="M12 2L22 20L12 16L2 20L12 2Z" fill="${color}" stroke="white" stroke-width="1.5"/>
    </svg>`;
  return L.divIcon({ html: svg, className: 'custom-drone-icon', iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -16] });
};

function RecenterAutomatically({ center, zoom = 16 }) {
  const map = useMap();
  useEffect(() => {
    if (center) {
      map.setView(center, zoom, { animate: true });
    }
  }, [center, map, zoom]);
  return null;
}

export default function FindMyDroneView() {
  const { drones, nowMs } = useDroneContext();
  const location = useDeviceLocation();
  const userLocation = location.coords;

  const [selectedDroneId, setSelectedDroneId] = useState(null);

  const droneList = Object.values(drones).map(drone => {
    const isOffline = drone.status === 'OFFLINE' || drone.freshness === 'OFFLINE';
    const lastSeenSeconds = Math.round((nowMs - drone.lastSeen) / 1000);
    const tel = drone.telemetry || {};
    
    // We get the last known location from the path if available, or direct telemetry
    let lat = null;
    let lon = null;
    
    if (drone.path && drone.path.length > 0) {
        const lastPos = drone.path[drone.path.length - 1];
        lat = lastPos[0];
        lon = lastPos[1];
    } else if (tel.latitude && tel.longitude && tel.latitude !== 0) {
        lat = tel.latitude;
        lon = tel.longitude;
    }

    let distance = null;
    let bearing = null;
    if (userLocation && lat != null && lon != null) {
        distance = calculateDistance(userLocation[0], userLocation[1], lat, lon);
        bearing = calculateBearing(userLocation[0], userLocation[1], lat, lon);
    }

    return {
        id: drone.id,
        isOffline,
        lastSeenSeconds,
        lat,
        lon,
        distance,
        bearing,
        armedState: tel.armed_state || 'DISARMED',
        battery: tel.battery_level
    };
  });

  // Select the first offline drone with a known location by default, or the first drone overall
  useEffect(() => {
      if (!selectedDroneId && droneList.length > 0) {
          const offlineWithLoc = droneList.find(d => d.isOffline && d.lat != null);
          if (offlineWithLoc) {
              setSelectedDroneId(offlineWithLoc.id);
          } else {
              setSelectedDroneId(droneList[0].id);
          }
      }
  }, [droneList, selectedDroneId]);


  const selectedDrone = droneList.find(d => d.id === selectedDroneId);

  // Map Bounds Calculation
  let mapCenter = [20.5937, 78.9629]; // Default India
  let zoom = 5;
  if (userLocation && selectedDrone && selectedDrone.lat != null) {
      // Center between user and drone
      mapCenter = [
          (userLocation[0] + selectedDrone.lat) / 2,
          (userLocation[1] + selectedDrone.lon) / 2
      ];
      // Basic zoom calculation based on distance
      if (selectedDrone.distance) {
          if (selectedDrone.distance < 500) zoom = 16;
          else if (selectedDrone.distance < 2000) zoom = 14;
          else if (selectedDrone.distance < 10000) zoom = 12;
          else zoom = 10;
      }
  } else if (userLocation) {
      mapCenter = userLocation;
      zoom = 16;
  } else if (selectedDrone && selectedDrone.lat != null) {
      mapCenter = [selectedDrone.lat, selectedDrone.lon];
      zoom = 16;
  }

  const formatDistance = (meters) => {
      if (meters == null) return '--';
      if (meters < 1000) return `${meters.toFixed(0)} m`;
      return `${(meters / 1000).toFixed(2)} km`;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '16px' }}>
      
      {/* Title & Status */}
      <div className="glass-panel" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px' }}>
         <div>
            <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: 0, color: 'var(--text-main)' }}>
               <Compass size={24} color="var(--primary)" /> FIND MY DRONE
            </h2>
            <div style={{ color: 'var(--text-muted)', fontSize: '13px', marginTop: '4px' }}>
               Track and retrieve lost or disconnected drones
            </div>
         </div>
         
         <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <div className="status-badge" style={{ background: userLocation ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)', color: userLocation ? 'var(--success)' : 'var(--danger)', padding: '6px 12px', borderRadius: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
               <MapPin size={16} /> 
               {userLocation ? 'PHONE GPS: OK' : 'PHONE GPS: WAITING'}
            </div>
            {!userLocation && (
                <button className="primary-btn" onClick={location.requestLocation}>
                   ENABLE GPS
                </button>
            )}
         </div>
      </div>

      <div style={{ display: 'flex', flex: 1, gap: '16px', minHeight: '400px' }}>
          {/* Sidebar List */}
          <div className="glass-panel" style={{ width: '300px', display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 0 }}>
             <div style={{ padding: '16px', borderBottom: '1px solid var(--border)', background: 'rgba(0,0,0,0.2)' }}>
                <h3 style={{ margin: 0, fontSize: '14px', color: 'var(--text-muted)' }}>FLEET TRACKING</h3>
             </div>
             
             <div style={{ flex: 1, overflowY: 'auto' }}>
                 {droneList.length === 0 ? (
                     <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--text-muted)' }}>
                         No drones found in history.
                     </div>
                 ) : (
                     droneList.map(d => (
                         <div 
                            key={d.id} 
                            onClick={() => setSelectedDroneId(d.id)}
                            style={{ 
                                padding: '16px', 
                                borderBottom: '1px solid var(--border)', 
                                cursor: 'pointer',
                                background: selectedDroneId === d.id ? 'rgba(0, 102, 204, 0.15)' : 'transparent',
                                borderLeft: selectedDroneId === d.id ? '4px solid var(--primary)' : '4px solid transparent'
                            }}
                         >
                             <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                                 <strong style={{ fontSize: '15px' }}>{d.id}</strong>
                                 <span className={`status-badge badge-${d.isOffline ? 'danger' : 'good'}`} style={{ fontSize: '10px', padding: '2px 6px' }}>
                                    {d.isOffline ? 'OFFLINE' : 'ONLINE'}
                                 </span>
                             </div>
                             
                             {d.lat != null ? (
                                 <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                        <MapPin size={12} /> {d.lat.toFixed(5)}, {d.lon.toFixed(5)}
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                        <Navigation size={12} /> {formatDistance(d.distance)} away
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                        <Signal size={12} /> Last seen {d.isOffline ? `${d.lastSeenSeconds}s ago` : 'just now'}
                                    </div>
                                 </div>
                             ) : (
                                 <div style={{ fontSize: '12px', color: 'var(--warning)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                                     <AlertTriangle size={12} /> No GPS data available
                                 </div>
                             )}
                         </div>
                     ))
                 )}
             </div>
          </div>

          {/* Map Area */}
          <div className="glass-panel" style={{ flex: 1, padding: '4px', position: 'relative', overflow: 'hidden' }}>
             {selectedDrone && (
                 <div style={{ position: 'absolute', top: '16px', left: '16px', zIndex: 1000, display: 'flex', gap: '12px' }}>
                    
                    {/* Targeting HUD Overlay */}
                    <div className="control-panel" style={{ background: 'rgba(25, 25, 25, 0.85)', padding: '16px', borderRadius: '12px', border: '1px solid var(--border)' }}>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px', fontWeight: 'bold' }}>TARGET LOCATION</div>
                        <div style={{ fontSize: '24px', fontWeight: 'bold', fontFamily: 'monospace', color: 'var(--primary)', marginBottom: '4px' }}>
                            {formatDistance(selectedDrone.distance)}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '16px', color: 'var(--text-main)', fontFamily: 'monospace' }}>
                            Bearing: {selectedDrone.bearing != null ? `${selectedDrone.bearing.toFixed(0)}°` : '--'}
                            {selectedDrone.bearing != null && (
                                <Navigation size={16} style={{ transform: `rotate(${selectedDrone.bearing}deg)`, color: 'var(--success)' }} />
                            )}
                        </div>
                        <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border)', fontSize: '12px', color: 'var(--text-muted)' }}>
                            <div style={{ marginBottom: '4px' }}>Bat: {selectedDrone.battery != null ? `${selectedDrone.battery}%` : '--'}</div>
                            <div>State: {selectedDrone.armedState}</div>
                        </div>
                    </div>
                    
                 </div>
             )}

             {!userLocation && (
                 <div style={{ position: 'absolute', top: '20px', right: '20px', zIndex: 1000, background: 'rgba(239, 68, 68, 0.9)', color: 'white', padding: '12px 20px', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '10px', boxShadow: 'var(--shadow-md)' }}>
                     <AlertCircle size={20} />
                     <div>
                         <div style={{ fontWeight: 'bold', fontSize: '14px' }}>GPS Required</div>
                         <div style={{ fontSize: '12px', marginTop: '2px' }}>Enable device GPS for distance tracking</div>
                     </div>
                 </div>
             )}

             <MapContainer center={mapCenter} zoom={zoom} style={{ height: '100%', width: '100%', borderRadius: '6px' }} zoomControl={false}>
                <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" attribution="" />
                <RecenterAutomatically center={mapCenter} zoom={zoom} />
                
                {/* User Location */}
                {userLocation && (
                   <CircleMarker 
                      center={userLocation} 
                      pathOptions={{ color: '#10B981', fillColor: '#10B981', fillOpacity: 1.0 }} 
                      radius={10}
                   >
                      <Popup><div style={{color: '#000', fontWeight: 'bold'}}>Your Phone</div></Popup>
                   </CircleMarker>
                )}

                {/* Drone Location */}
                {selectedDrone && selectedDrone.lat != null && (
                   <Marker 
                      position={[selectedDrone.lat, selectedDrone.lon]} 
                      icon={createDroneIcon(selectedDrone.isOffline ? '#F59E0B' : '#3B82F6')}
                   >
                      <Popup>
                         <div style={{color: '#000', fontWeight: 'bold'}}>{selectedDrone.id} (Last Known)</div>
                      </Popup>
                   </Marker>
                )}

                {/* Path from User to Drone */}
                {userLocation && selectedDrone && selectedDrone.lat != null && (
                   <Polyline 
                      positions={[userLocation, [selectedDrone.lat, selectedDrone.lon]]} 
                      color="#F59E0B" 
                      weight={4} 
                      opacity={0.8}
                      dashArray="10, 10"
                   />
                )}
             </MapContainer>
          </div>
      </div>
    </div>
  );
}
