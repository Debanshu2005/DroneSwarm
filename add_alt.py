with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

alt_state = "  const [targetAltitude, setTargetAltitude] = useState(12.0);\n"
if "const [targetAltitude" not in content:
    content = content.replace("const [yawRate, setYawRate] = useState(15.0);", "const [yawRate, setYawRate] = useState(15.0);\n" + alt_state)

speed_panel = """         {/* Speed Control floating panel */}
         <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
             <div className="panel-header" style={{textAlign: 'center', marginBottom: '12px'}}>SPEED</div>
             <button className="action-btn" style={{padding: '8px', marginBottom: '8px'}} onClick={() => setMovementSpeed(Math.min(5.0, movementSpeed + 0.05))}><Plus size={20}/></button>
             <div style={{fontSize: '16px', fontWeight: 'bold', color: 'var(--primary)', margin: '8px 0'}}>{movementSpeed.toFixed(2)}</div>
             <div style={{fontSize: '10px', color: 'var(--text-muted)', marginBottom: '8px'}}>m/s</div>
             <button className="action-btn" style={{padding: '8px'}} onClick={() => setMovementSpeed(Math.max(0.05, movementSpeed - 0.05))}><Minus size={20}/></button>
         </div>"""

alt_panel = """
         {/* Altitude Hold floating panel */}
         <div className="control-panel" style={{backgroundColor: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
             <div className="panel-header" style={{textAlign: 'center', marginBottom: '12px'}}>ALT HOLD</div>
             <button className="action-btn" style={{padding: '8px', marginBottom: '8px'}} onClick={() => setTargetAltitude(targetAltitude + 1.0)}><Plus size={20}/></button>
             <div style={{fontSize: '16px', fontWeight: 'bold', color: 'var(--primary)', margin: '8px 0'}}>{targetAltitude.toFixed(1)}</div>
             <div style={{fontSize: '10px', color: 'var(--text-muted)', marginBottom: '8px'}}>m</div>
             <button className="action-btn" style={{padding: '8px'}} onClick={() => setTargetAltitude(Math.max(1.0, targetAltitude - 1.0))}><Minus size={20}/></button>
         </div>
"""
if "ALT HOLD</div>" not in content:
    content = content.replace(speed_panel, speed_panel + alt_panel)

# Change TAKEOFF command to use targetAltitude
if "CommandAction.TAKEOFF, null, true" in content:
    content = content.replace("CommandAction.TAKEOFF, null, true", "CommandAction.TAKEOFF, { altitude_m: targetAltitude }, true")

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)
