import React, { useState, useEffect } from 'react';
import { LocateFixed, X, Info } from 'lucide-react';
import { ZONE_LEGEND } from '../utils/airspace';

export default function AirspaceZonePanel({ currentZone, userLocation, location, className = '' }) {
  const [dismissedLevel, setDismissedLevel] = useState(null);
  const [showLegend, setShowLegend] = useState(false);

  useEffect(() => {
    if (dismissedLevel && currentZone.level !== dismissedLevel) {
      setDismissedLevel(null);
    }
  }, [currentZone.level, dismissedLevel]);

  if (dismissedLevel === currentZone.level && !showLegend) {
    return null;
  }

  const gpsSourceText = userLocation
      ? `Pilot GPS ${userLocation[0].toFixed(5)}, ${userLocation[1].toFixed(5)}`
      : location.status === 'requesting'
        ? 'Requesting pilot GPS...'
        : location.error || 'Pilot GPS unavailable';

  return (
    <>
      <div className={`flight-zone-banner ${currentZone.level} ${className}`.trim()} style={{'--zone-color': currentZone.color}}>
         <div className="flight-zone-code">{currentZone.shortLabel}</div>
         <div className="flight-zone-copy">
            <div className="flight-zone-title">{currentZone.description}</div>
            <div className="flight-zone-meta">
               {gpsSourceText} / {currentZone.source}{currentZone.distanceLabel ? ` / ${currentZone.distanceLabel}` : ''}
            </div>
         </div>
         <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
             {!userLocation && (
                <button 
                  style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 10px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', fontWeight: 'bold', cursor: 'pointer' }} 
                  onClick={location.requestLocation}
                >
                   <LocateFixed size={14}/> ALLOW GPS
                </button>
             )}
             <button 
                style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 10px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', fontWeight: 'bold', cursor: 'pointer' }}
                onClick={() => setShowLegend(true)} title="Zone Legend"
             >
                <Info size={14} /> INFO
             </button>
             <button 
                style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 10px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', fontWeight: 'bold', cursor: 'pointer' }}
                onClick={() => setDismissedLevel(currentZone.level)} title="Dismiss"
             >
                <X size={14} />
             </button>
         </div>
      </div>

      {showLegend && (
        <div className="modal-overlay">
           <div className="modal-content" style={{ maxWidth: '400px' }}>
              <h2>Airspace Zones Legend</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '16px' }}>
                 {['green', 'yellow', 'red'].map(level => {
                    const z = ZONE_LEGEND[level];
                    return (
                      <div key={level} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                         <div style={{ background: z.color, color: '#000', padding: '4px 8px', borderRadius: '4px', fontWeight: 'bold', fontSize: '11px', minWidth: '60px', textAlign: 'center' }}>
                            {z.shortLabel}
                         </div>
                         <div style={{ fontSize: '12px', color: 'var(--text-main)' }}>
                            <div style={{ fontWeight: 'bold', marginBottom: '2px' }}>{z.label}</div>
                            <div style={{ color: 'var(--text-muted)' }}>{z.description}</div>
                         </div>
                      </div>
                    );
                 })}
              </div>
              <button className="secondary-btn" style={{marginTop: '20px', width: '100%'}} onClick={() => setShowLegend(false)}>CLOSE</button>
           </div>
        </div>
      )}
    </>
  );
}
