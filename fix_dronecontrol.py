with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

# Fix requestCommand for LAND/RTL
old_request_command = """  const requestCommand = (action, params = null, danger = false) => {
    if (danger && targetMode === 'ALL') {
      setShowConfirmModal({ action, params, message: `Are you sure you want to ${action.toUpperCase()} ALL DRONES?` });
    } else {
      executeCommand(action, params);
    }
  };"""

new_request_command = """  const requestCommand = (action, params = null, danger = false) => {
    if (action === CommandAction.LAND || action === CommandAction.RTL || action === CommandAction.EMERGENCY) {
        stopMove(); // High priority commands cancel active movement immediately
    }
    if (danger && targetMode === 'ALL') {
      setShowConfirmModal({ action, params, message: `Are you sure you want to ${action.toUpperCase()} ALL DRONES?` });
    } else {
      executeCommand(action, params);
    }
  };"""

content = content.replace(old_request_command, new_request_command)

# Fix Top Header
old_header = """             {/* Status Badges */}
             <div style={{display: 'flex', alignItems: 'center', gap: '16px'}}>
                <div className="status-indicator">
                   <div className={`status-dot ${isHeartbeatHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">DRONE</span>
                      <span className="val">{isHeartbeatHealthy ? 'ONLINE' : 'OFFLINE'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${isTelemetryHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">LINK</span>
                      <span className="val">{isTelemetryHealthy ? 'GOOD' : 'POOR'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${isHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">SYSTEM</span>
                      <span className="val">{isHealthy ? 'OK' : 'WARN'}</span>
                   </div>
                </div>"""

new_header = """             {/* Status Badges */}
             <div style={{display: 'flex', alignItems: 'center', gap: '16px'}}>
                <div className="status-indicator">
                   <div className={`status-dot ${isConnected === 'CONNECTED' ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">RELAY</span>
                      <span className="val">{isConnected === 'CONNECTED' ? 'ONLINE' : 'OFFLINE'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${isHeartbeatHealthy ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">SWARM</span>
                      <span className="val">{connectedDronesCount} ACTIVE</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${isPx4Connected ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">PX4 FCU</span>
                      <span className="val">{isPx4Connected ? 'CONNECTED' : 'DISCONNECTED'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${tel.armed_state === 'ARMED' ? 'danger' : 'good'}`}></div>
                   <div className="status-text">
                      <span className="label">ARMED</span>
                      <span className="val" style={{color: tel.armed_state === 'ARMED' ? 'var(--danger)' : 'var(--success)'}}>{tel.armed_state || 'DISARMED'}</span>
                   </div>
                </div>
                <div className="status-indicator">
                   <div className={`status-dot ${tel.flight_mode ? 'good' : 'danger'}`}></div>
                   <div className="status-text">
                      <span className="label">FLIGHT MODE</span>
                      <span className="val">{tel.flight_mode || 'UNKNOWN'}</span>
                   </div>
                </div>"""

content = content.replace(old_header, new_header)

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)
