with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

# Make top header more compact
content = content.replace("gap: '16px'", "gap: '8px'")
content = content.replace("padding: '12px 24px'", "padding: '8px 16px'")
content = content.replace("padding: '16px 24px'", "padding: '8px 16px'")
content = content.replace("marginBottom: '12px'", "marginBottom: '6px'")
content = content.replace("gap: '12px'", "gap: '8px'")

# Make the status dots and text smaller
content = content.replace("width: 8px; height: 8px;", "width: 6px; height: 6px; margin-bottom: 2px;")
content = content.replace("font-size: 9px;", "font-size: 8px;")
content = content.replace("font-size: 11px;", "font-size: 10px;")

# Make the telemetry cards smaller
content = content.replace("padding: '12px'", "padding: '6px 8px'")
content = content.replace("fontSize: '18px'", "fontSize: '14px'")
content = content.replace("fontSize: '24px'", "fontSize: '18px'")
content = content.replace("gap: '8px'", "gap: '4px'")

# Make joystick panels smaller
content = content.replace("padding: '16px'", "padding: '8px'")
content = content.replace("width: '50px'", "width: '40px'")
content = content.replace("height: '50px'", "height: '40px'")
content = content.replace("margin: '4px'", "margin: '2px'")

# Reduce bottom bar button size
content = content.replace("padding: '12px 24px'", "padding: '8px 16px'")
content = content.replace("fontSize: '13px'", "fontSize: '11px'")
content = content.replace("size={20}", "size={16}")

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)
