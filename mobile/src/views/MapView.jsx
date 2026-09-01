import React, { useEffect, useState } from 'react';
import { useDroneContext } from '../context/DroneContext';
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { AlertCircle, Crosshair, Layers, Navigation } from 'lucide-react';
import { useDeviceLocation } from '../hooks/useDeviceLocation';
import { DEFAULT_MAP_CENTER, DIGITAL_SKY_AIRSPACE_URL, resolveAirspaceZone } from '../utils/airspace';

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

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
   if (drone.status === 'OFFLINE') return '#6B7280';
   if (drone.status === 'failsafe') return '#F97316';
   if (isSelected) return '#28d17c';
   return '#5de4ff';
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

export default function MapView() {
  const { drones, selectedDrones } = useDroneContext();
  const [mapStyle, setMapStyle] = useState('satellite');
  const [centerTarget, setCenterTarget] = useState(null);
  const location = useDeviceLocation();
  const userLocation = location.coords;

  const tiles = {
     street: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
     satellite: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
     terrain: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}'
  };

  let selectedDroneCenter = null;
  for (const id of selectedDrones) {
     const t = drones[id]?.telemetry;
     if (t && t.latitude && t.longitude && t.latitude !== 0) {
        selectedDroneCenter = [t.latitude, t.longitude];
        break;
     }
  }

  let mapCenter = DEFAULT_MAP_CENTER;
  let hasLiveDroneCenter = false;

  if (userLocation) {
      mapCenter = userLocation;
  } else if (selectedDroneCenter) {
      mapCenter = selectedDroneCenter;
      hasLiveDroneCenter = true;
  } else {
      for (const d of Object.values(drones)) {
         const t = d.telemetry;
         if (t && t.latitude && t.longitude && t.latitude !== 0) {
            mapCenter = [t.latitude, t.longitude];
            hasLiveDroneCenter = true;
            break;
         }
      }
  }

  const currentZone = resolveAirspaceZone(userLocation?.[0] ?? mapCenter[0], userLocation?.[1] ?? mapCenter[1]);
  const locationLabel = userLocation
    ? `${userLocation[0].toFixed(6)}, ${userLocation[1].toFixed(6)}`
    : location.status === 'requesting'
      ? 'Requesting GPS access...'
      : location.error || 'Waiting for device GPS';

  const handleCenter = () => {
     setCenterTarget([...mapCenter]);
     setTimeout(() => setCenterTarget(null), 100);
  };

  return (
    <div className="airspace-map-view">
      <div className="airspace-command-strip" style={{'--zone-color': currentZone.color}}>
         <div className="airspace-command-left">
            <Layers size={18} color="var(--text-muted)"/>
            <select className="input-field airspace-select" value={mapStyle} onChange={e => setMapStyle(e.target.value)}>
               <option value="street">Street</option>
               <option value="satellite">Satellite</option>
               <option value="terrain">Terrain</option>
            </select>
            <div className={`zone-chip ${currentZone.level}`}>
               <AlertCircle size={14}/> {currentZone.label}
            </div>
            <div className="gps-readout">
               <Navigation size={14}/> {locationLabel}
            </div>
         </div>
         <div className="airspace-command-actions">
            <button className="secondary-btn compact" onClick={location.requestLocation} title="Request GPS">
               <Navigation size={18}/> GPS
            </button>
            <button className="secondary-btn compact" onClick={handleCenter} title="Center Map">
               <Crosshair size={18}/> Center
            </button>
         </div>
      </div>

      <div className={`airspace-zone-panel ${currentZone.level}`} style={{'--zone-color': currentZone.color}}>
         <div className="zone-panel-code">{currentZone.shortLabel}</div>
         <div>
            <div className="zone-panel-title">{currentZone.description}</div>
            <div className="zone-panel-meta">
               {currentZone.source}
               {currentZone.distanceLabel ? ` / ${currentZone.distanceLabel}` : ''}
               {' / DigitalSky sync not connected'}
            </div>
         </div>
      </div>

      {!userLocation && (
         <div className="gps-permission-panel">
            <div className="gps-permission-title">Device GPS needed for your launch point</div>
            <div className="gps-permission-text">
               {location.error || 'The map is centered on India until this device shares its live GPS fix.'}
            </div>
            <button className="primary-btn" onClick={location.requestLocation}>Allow GPS Access</button>
         </div>
      )}

      {!hasLiveDroneCenter && userLocation && (
         <div className="map-hint-panel">
            <div className="map-hint-title">NO LIVE DRONE POSITION</div>
            <div className="map-hint-text">Showing your device location as the active launch point.</div>
         </div>
      )}

      <div className="airspace-map-frame">
         <MapContainer center={mapCenter} zoom={16} style={{ height: '100%', width: '100%' }}>
            <TileLayer url={tiles[mapStyle]} attribution="PhoneOS GCS" />
            <RecenterAutomatically center={centerTarget || (userLocation ? mapCenter : null)} />

            {Object.values(drones).map(drone => {
               const t = drone.telemetry;
               if (!t || t.latitude == null || t.longitude == null || isNaN(t.latitude) || isNaN(t.longitude) || t.latitude === 0) return null;

               const isSelected = selectedDrones.has(drone.id);
               const color = getColorForDrone(drone, isSelected);
               const icon = createDroneIcon(color, t.heading);
               const freshnessText = drone.freshness || (drone.status === 'OFFLINE' ? 'OFFLINE' : 'LIVE');
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
                              <div><strong>Heading:</strong> {t.heading != null ? `${t.heading} deg` : 'N/A'}</div>
                              <div><strong>Mode:</strong> {t.flight_mode || 'N/A'}</div>
                           </div>
                        </Popup>
                     </Marker>
                     {drone.path && drone.path.length > 1 && (
                        <Polyline
                           positions={drone.path}
                           color={isSelected ? '#28d17c' : '#5de4ff'}
                           weight={3}
                           opacity={0.72}
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
                     <div style={{fontWeight: 'bold'}}>Your GPS Location</div>
                     {location.accuracy && <div>Accuracy: {location.accuracy.toFixed(0)} m</div>}
                  </Popup>
               </CircleMarker>
            )}
         </MapContainer>
      </div>

      <a className="digital-sky-link" href={DIGITAL_SKY_AIRSPACE_URL} target="_blank" rel="noreferrer">
         Verify live restrictions on DigitalSky before flight
      </a>
    </div>
  );
}
