import os
import glob
import re

for adapter_path in glob.glob("DroneOS*/adapters/px4_adapter.py"):
    with open(adapter_path, 'r') as f:
        content = f.read()

    # Apply the index fix
    content = re.sub(
        r"match = re\.search\(r'\\d\+', self\.vehicle_name\)\s+idx = \(int\(match\.group\(\)\) - 1\) if match else 0",
        "idx = 0",
        content
    )

    # Add ama_paths definition if not exists
    if "ama_paths" not in content:
        content = content.replace(
            'usb_paths = sorted(glob.glob("/dev/ttyUSB*"))',
            'usb_paths = sorted(glob.glob("/dev/ttyUSB*"))\n            ama_paths = sorted(glob.glob("/dev/ttyAMA*"))'
        )
        
        content = content.replace(
            'elif usb_paths and len(usb_paths) > idx:\n                device = usb_paths[idx]',
            'elif usb_paths and len(usb_paths) > idx:\n                device = usb_paths[idx]\n            elif ama_paths and len(ama_paths) > idx:\n                device = ama_paths[idx]'
        )

    with open(adapter_path, 'w') as f:
        f.write(content)
