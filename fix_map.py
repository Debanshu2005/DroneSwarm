with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

icon_fix_safe = """// Fix Leaflet's default icon path issues in React
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
"""

if "try {" not in content.split("delete L.Icon.Default.prototype._getIconUrl")[0][-10:]:
    content = content.replace("""// Fix Leaflet's default icon path issues in React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});""", icon_fix_safe)

map_fallback = """      {/* BACKGROUND MAP LAYER (Z: 0) */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0, backgroundColor: '#E2E8F0' }}>
         <ErrorBoundary fallback={
            <div style={{width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
                <div style={{color: '#94A3B8', fontSize: '24px', fontWeight: 'bold'}}>MAP OFFLINE</div>
                <div style={{color: '#94A3B8', fontSize: '12px'}}>Flight controls remain fully active.</div>
            </div>
         }>
             <MapContainer center={mapCenter} zoom={18} style={{ height: '100%', width: '100%' }} zoomControl={false}>"""

if "ErrorBoundary fallback" not in content:
    content = content.replace("""      {/* BACKGROUND MAP LAYER (Z: 0) */}
      <div style={{ position: 'absolute', inset: 0, zIndex: 0 }}>
         <MapContainer center={mapCenter} zoom={18} style={{ height: '100%', width: '100%' }} zoomControl={false}>""", map_fallback)
    content = content.replace("         </MapContainer>\n      </div>", "         </MapContainer>\n         </ErrorBoundary>\n      </div>")
    content = content.replace("import { MapContainer", "import { ErrorBoundary } from '../components/ErrorBoundary';\nimport { MapContainer")


with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)

print("DroneControlView map logic updated.")
