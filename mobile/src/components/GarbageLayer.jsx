import React from 'react';
import { Marker, Popup } from 'react-leaflet';
import L from 'leaflet';

/**
 * Resolve marker color based on detection confidence:
 * >= 0.7: red (#ef4444)
 * 0.5 - 0.7: orange (#f97316)
 * < 0.5: amber (#f59e0b)
 */
const getConfidenceColor = (conf) => {
  if (conf >= 0.7) return '#ef4444';
  if (conf >= 0.5) return '#f97316';
  return '#f59e0b';
};

const createGarbageIcon = (confidence, isSynthetic) => {
  const color = getConfidenceColor(confidence);
  const ringSvg = isSynthetic
    ? `<circle cx="16" cy="16" r="14" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="3,3" />`
    : '';

  const html = `
    <div style="position: relative; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;">
      <svg width="32" height="32" viewBox="0 0 32 32" style="position: absolute; top: 0; left: 0;">
        ${ringSvg}
        <circle cx="16" cy="16" r="11" fill="${color}" stroke="#ffffff" stroke-width="1.5" />
      </svg>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="position: relative; z-index: 2;">
        <path d="M3 6h18"/>
        <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>
        <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
        <line x1="10" y1="11" x2="10" y2="17"/>
        <line x1="14" y1="11" x2="14" y2="17"/>
      </svg>
    </div>
  `;

  return L.divIcon({
    html,
    className: 'custom-garbage-marker',
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -16],
  });
};

export default function GarbageLayer({ garbage = {}, onDismiss }) {
  const items = Object.values(garbage);

  return (
    <>
      {items.map((item) => {
        if (!item || !Number.isFinite(item.lat) || !Number.isFinite(item.lon)) return null;

        const icon = createGarbageIcon(item.confidence, item.synthetic);
        const confPct = Math.round((item.confidence || 0) * 100);
        const firstSeenStr = item.firstSeen ? new Date(item.firstSeen).toLocaleTimeString() : 'N/A';
        const lastSeenStr = item.lastSeen ? new Date(item.lastSeen).toLocaleTimeString() : 'N/A';
        const seenByDrones = item.seenBy && item.seenBy.length > 0 ? item.seenBy.join(', ') : item.reportingDrone;

        return (
          <Marker key={item.id} position={[item.lat, item.lon]} icon={icon}>
            <Popup>
              <div style={{ color: '#111827', minWidth: '180px', fontSize: '12px' }}>
                <div style={{ fontWeight: 700, borderBottom: '1px solid #e5e7eb', paddingBottom: '4px', marginBottom: '6px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>{item.id}</span>
                  {item.synthetic && (
                    <span style={{ fontSize: '10px', background: '#fef3c7', color: '#92400e', padding: '1px 4px', borderRadius: '4px', fontWeight: 600 }}>
                      SYNTHETIC
                    </span>
                  )}
                </div>
                <div style={{ marginBottom: '2px' }}><strong>Label:</strong> {item.label || 'rubbish'}</div>
                <div style={{ marginBottom: '2px' }}><strong>Confidence:</strong> {confPct}%</div>
                <div style={{ marginBottom: '2px' }}><strong>Times Seen:</strong> {item.hits || 1}</div>
                <div style={{ marginBottom: '2px' }}><strong>Reporting Drone:</strong> {item.reportingDrone}</div>
                {item.seenBy && item.seenBy.length > 1 && (
                  <div style={{ marginBottom: '2px' }}><strong>Observed by:</strong> {seenByDrones}</div>
                )}
                <div style={{ marginBottom: '2px' }}><strong>First Seen:</strong> {firstSeenStr}</div>
                <div style={{ marginBottom: '2px' }}><strong>Last Seen:</strong> {lastSeenStr}</div>
                <div style={{ marginBottom: '6px' }}><strong>Coords:</strong> {item.lat.toFixed(6)}, {item.lon.toFixed(6)}</div>
                <button
                  type="button"
                  style={{
                    width: '100%',
                    padding: '4px 8px',
                    fontSize: '11px',
                    backgroundColor: '#fee2e2',
                    color: '#991b1b',
                    border: '1px solid #fca5a5',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontWeight: 600,
                  }}
                  onClick={() => onDismiss && onDismiss(item.id)}
                >
                  Dismiss (false positive)
                </button>
              </div>
            </Popup>
          </Marker>
        );
      })}
    </>
  );
}
