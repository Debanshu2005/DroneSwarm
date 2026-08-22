import re

with open("mobile/src/views/DroneControlView.jsx", "r") as f:
    content = f.read()

# Replace the test panel buttons so they don't call requestCommand
old_test_panel = """<div style={{display: 'flex', flexDirection: 'column', gap: '8px'}}>
                          <button className="command-btn btn-hold" style={{padding: '12px'}} onClick={() => requestCommand(CommandAction.ARM, null, true)}>
                             <div className="cmd-main"><Lock size={14}/> TEST ARM</div>
                          </button>
                          <button className="command-btn btn-takeoff" style={{padding: '12px'}} onClick={() => requestCommand(CommandAction.TAKEOFF, { altitude_m: targetAltitude }, true)}>
                             <div className="cmd-main"><ArrowUp size={14}/> TEST TAKEOFF</div>
                          </button>
                          <button className="command-btn btn-hold" style={{padding: '12px', background: '#4B5563'}} onClick={() => requestCommand(CommandAction.HOVER, null, false)}>
                             <div className="cmd-main"><Square size={14}/> TEST HOVER</div>
                          </button>
                          <button className="command-btn btn-land" style={{padding: '12px'}} onClick={() => requestCommand(CommandAction.LAND)}>
                             <div className="cmd-main"><ArrowDown size={14}/> TEST LAND</div>
                          </button>
                      </div>"""

new_test_panel = """<div style={{display: 'flex', flexDirection: 'column', gap: '8px'}}>
                          <button className="command-btn btn-hold" style={{padding: '12px'}} onClick={() => console.log("TEST ARM Triggered")}>
                             <div className="cmd-main"><Lock size={14}/> TEST ARM</div>
                          </button>
                          <button className="command-btn btn-takeoff" style={{padding: '12px'}} onClick={() => console.log("TEST TAKEOFF Triggered")}>
                             <div className="cmd-main"><ArrowUp size={14}/> TEST TAKEOFF</div>
                          </button>
                          <button className="command-btn btn-hold" style={{padding: '12px', background: '#4B5563'}} onClick={() => console.log("TEST HOVER Triggered")}>
                             <div className="cmd-main"><Square size={14}/> TEST HOVER</div>
                          </button>
                          <button className="command-btn btn-land" style={{padding: '12px'}} onClick={() => console.log("TEST LAND Triggered")}>
                             <div className="cmd-main"><ArrowDown size={14}/> TEST LAND</div>
                          </button>
                          <div style={{marginTop: '8px', fontSize: '10px', color: 'var(--warning)', textAlign: 'center'}}>
                              (Test controls are isolated from real MAVSDK handlers)
                          </div>
                      </div>"""

content = content.replace(old_test_panel, new_test_panel)

with open("mobile/src/views/DroneControlView.jsx", "w") as f:
    f.write(content)

print("Updated test panel bindings")
