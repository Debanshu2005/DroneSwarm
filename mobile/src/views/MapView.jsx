import React, { useState, useEffect } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { Crosshair, Layers, AlertCircle } from 'lucide-react';

// Fix Leaflet's default icon path issues in React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

const getZoneInfo = (lat, lng) => {
   if (!lat || !lng) return { color: '#6B7280', text: 'UNKNOWN ZONE' };
   const hash = Math.floor(Math.abs(lat * 100 + lng * 100));
   if (hash % 3 === 0) return { color: '#DC2626', text: 'RED ZONE (NO-FLY)' };
   if (hash % 2 === 0) return { color: '#F59E0B', text: 'YELLOW ZONE (AUTH REQ)' };
   return { color: '#10B981', text: 'GREEN ZONE (SAFE)' };
};

const FALLBACK_CENTER = [22.315, 87.310];

const createDroneIcon = (color, heading) => {
  const rotation = heading !== undefined && heading !== null && !isNaN(heading) ? `transform: rotate(${heading}deg);` : '';
  const svg = `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="${rotation} transform-origin: center;">
      <path d="M12 2L22 20L12 16L2 20L12 2Z" fill="${color}" stroke="white" stroke-width="1.5"/>
    </svg>`;
  return L.divIcon({
    html: svg,
    className: 'custom-drone-icon',
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -16]
  });
};

const getColorForDrone = (drone, isSelected) => {
   if (drone.status === 'OFFLINE') return '#6B7280'; // grey
   if (drone.status === 'failsafe') return '#F97316'; // orange
   if (isSelected) return '#10B981'; // green
   return '#3B82F6'; // blue
};

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
  const [userLocation, setUserLocation] = useState(null);

  useEffect(() => {
     if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
           (pos) => setUserLocation([pos.coords.latitude, pos.coords.longitude]),
           (err) => console.warn('Geolocation blocked or failed:', err)
        );
     }
  }, []);

  const tiles = {
     street: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
     satellite: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
     terrain: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}"
  };

  let center = [0, 0];
  let hasSelectedDroneCenter = false;

  for (const id of selectedDrones) {
     const t = drones[id]?.telemetry;
     if (t && t.latitude && t.longitude && t.latitude !== 0) {
        center = [t.latitude, t.longitude];
        hasSelectedDroneCenter = true;
        break;
     }
  }

  let mapCenter = FALLBACK_CENTER;
  let hasValidCenter = false;

  if (hasSelectedDroneCenter) {
      mapCenter = center;
      hasValidCenter = true;
  } else if (userLocation) {
      mapCenter = userLocation;
      // We don't set hasValidCenter = true here because we want the "NO LIVE DRONE POSITION" banner to show if no drones are active
  } else {
      for (const d of Object.values(drones)) {
         const t = d.telemetry;
         if (t && t.latitude && t.longitude && t.latitude !== 0) {
            mapCenter = [t.latitude, t.longitude];
            hasValidCenter = true;
            break;
         }
      }
  }

  const currentZone = getZoneInfo(mapCenter[0], mapCenter[1]);

  const handleCenter = () => {
     setCenterTarget([...mapCenter]);
     setTimeout(() => setCenterTarget(null), 100);
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100%', position: 'relative', gap: '16px'}}>
      
      <div className="card" style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '2px solid ' + currentZone.color}}>
         <div style={{display: 'flex', gap: '10px', alignItems: 'center'}}>
            <Layers size={18} color="var(--text-muted)"/>
            <select className="input-field" value={mapStyle} onChange={e => setMapStyle(e.target.value)} style={{padding: '6px 8px', fontSize: '13px', width: '120px'}}>
               <option value="street">Street</option>
               <option value="satellite">Satellite</option>
               <option value="terrain">Terrain</option>
            </select>
            <div style={{marginLeft: '16px', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 'bold', color: currentZone.color, background: 'rgba(0,0,0,0.05)', padding: '4px 8px', borderRadius: '4px'}}>
               <AlertCircle size={14}/> {currentZone.text}
            </div>
         </div>
         <div style={{display: 'flex', gap: '8px'}}>
            <button className="btn btn-secondary" style={{padding: '6px 12px', display: 'flex', alignItems: 'center', gap: '8px'}} onClick={handleCenter} title="Center Map">
               <Crosshair size={18}/> Center
            </button>
         </div>
      </div>

      {!hasValidCenter && (
         <div style={{position: 'absolute', top: '80px', left: '50%', transform: 'translateX(-50%)', zIndex: 1000, background: 'rgba(255,255,255,0.95)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-md)', padding: '12px 24px', borderRadius: '4px', textAlign: 'center'}}>
            <div style={{fontWeight: 'bold', fontSize: '14px', color: 'var(--text-main)'}}>NO LIVE DRONE POSITION</div>
            <div style={{fontSize: '12px', marginTop: '4px', color: 'var(--text-muted)'}}>
               {userLocation ? 'Displaying Your Current Location' : 'Displaying Default Map Center'}
            </div>
         </div>
      )}

      <div style={{flex: 1, borderRadius: '12px', overflow: 'hidden', border: '1px solid var(--border)', zIndex: 1}}>
         <MapContainer center={mapCenter} zoom={16} style={{ height: '100%', width: '100%' }}>
            <TileLayer url={tiles[mapStyle]} attribution="PhoneOS GCS" />
            <RecenterAutomatically center={centerTarget} />

            {Object.values(drones).map(drone => {
               const t = drone.telemetry;
               if (!t || t.latitude == null || t.longitude == null || isNaN(t.latitude) || isNaN(t.longitude) || t.latitude === 0) return null;
               
               const isSelected = selectedDrones.has(drone.id);
               const color = getColorForDrone(drone, isSelected);
               const icon = createDroneIcon(color, t.heading);
               const freshnessText = drone.freshness || (drone.status === 'OFFLINE' ? 'OFFLINE' : 'LIVE');
               
               // Do not render moving marker if GPS is offline/stale and we only want to show last known
               const opacity = (drone.status === 'OFFLINE' || drone.freshness === 'OFFLINE') ? 0.5 : 1.0;

               return (
                  <React.Fragment key={drone.id}>
                     <Marker position={[t.latitude, t.longitude]} icon={icon} opacity={opacity}>
                        <Popup>
                           <div style={{color: '#000'}}>
                              <div style={{fontWeight: 'bold', borderBottom: '1px solid #ccc', paddingBottom: '4px', marginBottom: '4px'}}>{drone.id}</div>
                              <div><strong>GPS:</strong> {freshnessText} ({t.gps_valid ? '3D FIX' : 'NO FIX'})</div>
                              <div><strong>Satellites:</strong> {t.satellites != null ? t.satellites : 'N/A'}</div>
                              <div><strong>HDOP:</strong> {t.hdop != null ? t.hdop : 'N/A'}</div>
                              <div><strong>Alt:</strong> {t.altitude != null ? `${t.altitude.toFixed(1)}m` : 'N/A'}</div>
                              <div><strong>Spd:</strong> {t.ground_speed != null ? `${t.ground_speed.toFixed(1)}m/s` : 'N/A'}</div>
                              <div><strong>Heading:</strong> {t.heading != null ? `${t.heading}°` : 'N/A'}</div>
                              <div><strong>Mode:</strong> {t.flight_mode || 'N/A'}</div>
                           </div>
                        </Popup>
                     </Marker>
                     {drone.path && drone.path.length > 1 && (
                        <Polyline 
                           positions={drone.path} 
                           color={isSelected ? '#10B981' : '#3B82F6'} 
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
                  pathOptions={{ color: '#3B82F6', fillColor: '#3B82F6', fillOpacity: 0.8 }} 
                  radius={6}
               >
                  <Popup>
                     <div style={{fontWeight: 'bold'}}>Your Location</div>
                  </Popup>
               </CircleMarker>
            )}
         </MapContainer>
      </div>
    </div>
  );
}
