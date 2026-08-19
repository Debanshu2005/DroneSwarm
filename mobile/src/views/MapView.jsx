import React, { useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { Crosshair, Layers } from 'lucide-react';

// Fix Leaflet's default icon path issues in React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// Custom icons based on selection
const createIcon = (color) => new L.Icon({
  iconUrl: `https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-${color}.png`,
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

const defaultIcon = createIcon('blue');
const selectedIcon = createIcon('green');
const warningIcon = createIcon('orange');
const offlineIcon = createIcon('grey');

function RecenterAutomatically({ center }) {
   const map = useMap();
   if (center) {
      map.setView(center, map.getZoom());
   }
   return null;
}

export default function MapView() {
  const { drones, selectedDrones } = useDroneContext();
  const [mapStyle, setMapStyle] = useState('street');
  const [centerTarget, setCenterTarget] = useState(null);

  const tiles = {
     street: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
     satellite: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
     terrain: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}"
  };

  // Find a valid center: first selected drone, or first drone with GPS, or default
  let center = [0, 0];
  let hasValidCenter = false;

  for (const id of selectedDrones) {
     const t = drones[id]?.telemetry;
     if (t && t.latitude && t.longitude && t.latitude !== 0) {
        center = [t.latitude, t.longitude];
        hasValidCenter = true;
        break;
     }
  }

  if (!hasValidCenter) {
     for (const d of Object.values(drones)) {
        const t = d.telemetry;
        if (t && t.latitude && t.longitude && t.latitude !== 0) {
           center = [t.latitude, t.longitude];
           hasValidCenter = true;
           break;
        }
     }
  }

  const handleCenter = () => {
     if (hasValidCenter) {
        setCenterTarget([...center]);
        setTimeout(() => setCenterTarget(null), 100);
     }
  };

  if (Object.keys(drones).length === 0) {
     return <div className="card" style={{textAlign: 'center', padding: '40px'}}><h3 style={{color: 'var(--text-muted)'}}>No drones connected. Map unavailable.</h3></div>;
  }

  if (!hasValidCenter) {
     return (
        <div style={{display: 'flex', flexDirection: 'column', height: '100%', gap: '16px'}}>
           <div className="card" style={{flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444'}}>
              <div style={{textAlign: 'center'}}>
                 <h3 style={{marginBottom: '8px'}}>POSITION UNAVAILABLE</h3>
                 <span style={{color: 'var(--text-muted)'}}>(Waiting for GPS 3D Fix)</span>
              </div>
           </div>
        </div>
     );
  }

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100%', position: 'relative', gap: '16px'}}>
      
      <div className="card" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px'}}>
         <div style={{display: 'flex', gap: '10px', alignItems: 'center'}}>
            <Layers size={18} color="var(--text-muted)"/>
            <select className="input-field" value={mapStyle} onChange={e => setMapStyle(e.target.value)} style={{padding: '6px 8px', fontSize: '13px', width: '120px'}}>
               <option value="street">Street</option>
               <option value="satellite">Satellite</option>
               <option value="terrain">Terrain</option>
            </select>
         </div>
         <button className="btn btn-secondary" style={{padding: '6px 12px', display: 'flex', alignItems: 'center', gap: '8px'}} onClick={handleCenter} title="Center on selected">
            <Crosshair size={18}/> Center
         </button>
      </div>

      <div style={{flex: 1, borderRadius: '12px', overflow: 'hidden', border: '1px solid var(--border)', zIndex: 1}}>
         <MapContainer center={center} zoom={16} style={{ height: '100%', width: '100%' }}>
            <TileLayer url={tiles[mapStyle]} attribution="PhoneOS GCS" />
            <RecenterAutomatically center={centerTarget} />

            {Object.values(drones).map(drone => {
               const t = drone.telemetry;
               if (!t || !t.latitude || !t.longitude || t.latitude === 0) return null;
               
               let icon = defaultIcon;
               if (drone.status === 'OFFLINE') icon = offlineIcon;
               else if (drone.status === 'failsafe') icon = warningIcon;
               else if (selectedDrones.has(drone.id)) icon = selectedIcon;

               return (
                  <React.Fragment key={drone.id}>
                     <Marker position={[t.latitude, t.longitude]} icon={icon}>
                        <Popup>
                           <div style={{color: '#000', fontWeight: 'bold'}}>
                              {drone.id}<br/>
                              Alt: {t.altitude?.toFixed(1)}m<br/>
                              Spd: {t.ground_speed?.toFixed(1)}m/s<br/>
                              Mode: {t.flight_mode}
                           </div>
                        </Popup>
                     </Marker>
                     {drone.path && drone.path.length > 1 && (
                        <Polyline 
                           positions={drone.path} 
                           color={selectedDrones.has(drone.id) ? '#16A34A' : '#2563EB'} 
                           weight={3} 
                           opacity={0.6}
                        />
                     )}
                  </React.Fragment>
               );
            })}
         </MapContainer>
      </div>
    </div>
  );
}
