with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

# Fix status-text back to column so LABEL is above VAL
content = content.replace(".status-text { display: flex; flex-direction: row; }", ".status-text { display: flex; flex-direction: column; }")

# Make the status bar flex-wrap so it doesn't squish horizontally
content = content.replace("<div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>", "<div style={{display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap'}}>")

# Make telemetry cards smaller text
content = content.replace("fontSize: '14px'", "fontSize: '12px'")
content = content.replace("fontSize: '18px'", "fontSize: '14px'")

# Make the HOME INVALID text smaller
content = content.replace("fontSize: '18px', color: 'var(--danger)'", "fontSize: '14px', color: 'var(--danger)'")

# Make the TELEMETRY header flex-wrap too
content = content.replace("<div style={{ display: 'flex', gap: '8px' }}>", "<div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>")

# Reduce joystick container padding so it fits
content = content.replace("padding: '8px'", "padding: '4px'")

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)
