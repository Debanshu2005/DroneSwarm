with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

lifecycle_panel = """
      {/* COMMAND LIFECYCLE OVERLAY (Z: 40) */}
      <div style={{ position: 'absolute', top: '80px', right: '24px', zIndex: 40, display: 'flex', flexDirection: 'column', gap: '8px', width: '220px', pointerEvents: 'none' }}>
         {droneIds.map(id => {
            const cs = drones[id]?.commandState;
            if (!cs || !cs.action) return null;
            if (cs.state === 'SUCCESS' && (nowMs - (cs.timestamp || nowMs)) > 5000) return null; // hide success after 5s
            
            let color = 'var(--text-muted)';
            let bg = 'rgba(255,255,255,0.85)';
            if (cs.state === 'SUCCESS') { color = 'var(--success)'; bg = 'rgba(16, 185, 129, 0.1)'; }
            if (cs.state === 'FAILED' || cs.state === 'REJECTED' || cs.state === 'TIMEOUT') { color = 'var(--danger)'; bg = 'rgba(239, 68, 68, 0.1)'; }
            if (cs.state === 'MAVSDK_REQUESTED' || cs.state === 'BACKEND_RECEIVED') { color = 'var(--warning)'; }

            return (
               <div key={id} style={{ padding: '8px 12px', background: bg, backdropFilter: 'blur(4px)', borderRadius: '6px', fontSize: '11px', border: `1px solid ${color}`, boxShadow: 'var(--shadow-sm)' }}>
                  <div style={{fontWeight: 'bold', marginBottom: '2px', color: 'var(--text-main)'}}>{id}: <span style={{color}}>{cs.action.toUpperCase()}</span></div>
                  <div style={{color}}>{cs.state}</div>
                  {cs.reason && <div style={{color: 'var(--danger)', marginTop: '2px', fontSize: '10px'}}>{cs.reason}</div>}
               </div>
            );
         })}
      </div>
"""

if "COMMAND LIFECYCLE OVERLAY" not in content:
    content = content.replace("{/* CONFIRMATION MODAL */}", lifecycle_panel + "\n      {/* CONFIRMATION MODAL */}")

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)
