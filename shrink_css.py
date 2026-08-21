with open('mobile/src/views/DroneControlView.jsx', 'r') as f:
    content = f.read()

content = content.replace("width: 64px; height: 64px;", "width: 44px; height: 44px;")
content = content.replace("width: 56px; height: 56px;", "width: 40px; height: 40px;")
content = content.replace("height: 64px;", "height: 44px;")
content = content.replace("font-size: 13px;", "font-size: 10px;")
content = content.replace("font-size: 15px;", "font-size: 11px;")
content = content.replace("flex-direction: column;", "flex-direction: row;") # bottom bar command buttons should be row layout to save height
content = content.replace("flex: 1; display: flex; flex-direction: row; align-items: center; justify-content: center; gap: 6px;", "flex: 1; display: flex; flex-direction: row; align-items: center; justify-content: center; gap: 4px;")

with open('mobile/src/views/DroneControlView.jsx', 'w') as f:
    f.write(content)
