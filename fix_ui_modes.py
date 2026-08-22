import re

with open("mobile/src/protocol/messages.js", "r") as f:
    content = f.read()

if "EMERGENCY_RESET" not in content:
    content = content.replace('EMERGENCY: "emergency",', 'EMERGENCY: "emergency",\n    EMERGENCY_RESET: "emergency_reset",')

with open("mobile/src/protocol/messages.js", "w") as f:
    f.write(content)

with open("mobile/src/views/DroneControlView.jsx", "r") as f:
    content = f.read()

# Add a reset button logic to the UI
reset_btn = """                <button className="command-btn btn-emergency" onClick={() => requestCommand(CommandAction.EMERGENCY, null, true)}>
                   <div className="cmd-main"><AlertTriangle size={14}/> E-STOP</div>
                </button>
                <button className="command-btn btn-emergency" style={{background: '#D97706'}} onClick={() => requestCommand(CommandAction.EMERGENCY_RESET, null, true)}>
                   <div className="cmd-main"><ShieldCheck size={14}/> E-RESET</div>
                </button>"""

if "E-RESET" not in content:
    content = content.replace("""                <button className="command-btn btn-emergency" onClick={() => requestCommand(CommandAction.EMERGENCY, null, true)}>
                   <div className="cmd-main"><AlertTriangle size={14}/> E-STOP</div>
                </button>""", reset_btn)

with open("mobile/src/views/DroneControlView.jsx", "w") as f:
    f.write(content)

print("Updated UI messages and drone view")
