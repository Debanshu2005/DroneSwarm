import re

with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

# Add imports for react-leaflet at the top
imports = """import { MapContainer, TileLayer, Marker, Polyline, Popup, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
"""

# Add leaflet icon fix after imports
icon_fix = """
// Fix Leaflet's default icon path issues in React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

const createDroneIcon = (color, heading) => {
  const rotation = heading != null && !isNaN(heading) ? `transform: rotate(${heading}deg);` : '';
  const svg = `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="${rotation} transform-origin: center;">
      <path d="M12 2L22 20L12 16L2 20L12 2Z" fill="${color}" stroke="white" stroke-width="1.5"/>
    </svg>`;
  return L.divIcon({ html: svg, className: 'custom-drone-icon', iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -16] });
};
"""

# We need to replace the fake map placeholder:
fake_map = """      {/* BACKGROUND MAP LAYER (Z: 0) */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0, backgroundColor: '#E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
          <Map size={48} color="#94A3B8" style={{opacity: 0.5, marginBottom: '8px'}} />
          <div style={{color: '#94A3B8', fontSize: '14px', fontWeight: 'bold', letterSpacing: '2px'}}>INTEGRATED LIVE MAP</div>
          {isGpsValid ? <div style={{color: 'var(--success)', fontSize: '12px', marginTop: '4px', fontWeight: 'bold'}}>3D FIX</div> : <div style={{color: 'var(--danger)', fontSize: '12px', marginTop: '4px', fontWeight: 'bold'}}>NO FIX</div>}
      </div>"""

real_map = """      {/* BACKGROUND MAP LAYER (Z: 0) */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0 }}>
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
      </div>"""

# Insert mapCenter logic before the return statement inside DroneControlView
map_logic = """
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

"""

if "import { MapContainer" not in content:
    content = content.replace("import { CommandAction } from '../protocol/messages';", imports + "\nimport { CommandAction } from '../protocol/messages';")
if "createDroneIcon" not in content:
    content = content.replace("export default function DroneControlView", icon_fix + "\nexport default function DroneControlView")
if "let mapCenter =" not in content:
    content = content.replace("return (", map_logic + "  return (")
    content = content.replace(fake_map, real_map)

# Swarm controls inclusion on the right side
swarm_controls = """
         {/* Formation Control floating panel */}
         <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
             <div className="panel-header" style={{textAlign: 'center', marginBottom: '8px'}}>FORMATION</div>
             <select className="input-field" value={formationType} onChange={e => setFormationType(e.target.value)} style={{fontSize: '11px', padding: '4px', marginBottom: '8px', width: '70px', borderRadius: '4px', border: '1px solid var(--border)'}}>
                <option value="V">V</option>
                <option value="COLUMN">COLUMN</option>
                <option value="LINE">LINE</option>
                <option value="SQUARE">SQUARE</option>
                <option value="GRID">GRID</option>
                <option value="CIRCLE">CIRCLE</option>
             </select>
             <button className="action-btn" style={{padding: '6px', fontSize: '10px'}} onClick={() => requestCommand(CommandAction.FORMATION, { type: formationType, spacing: formationSpacing })}>APPLY</button>
         </div>
"""
if "FORMATION</div>" not in content:
    content = content.replace("{/* Vertical / Yaw */}", swarm_controls + "\n         {/* Vertical / Yaw */}")


# Add Map to lucide-react imports if it's missing (it was there in my fake map but I might need to make sure)
# It's already there in my previous write.

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)

print("DroneControlView.jsx updated with Leaflet Map and Swarm Controls.")
