import glob
import os

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    old_code = "cmdline = proc.info.get('cmdline', [])"
    new_code = "cmdline = proc.info.get('cmdline') or []"
    
    if old_code in content:
        content = content.replace(old_code, new_code)
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Patched {filepath}")

for f in glob.glob('DroneOS*/adapters/px4_adapter.py'):
    patch_file(f)

for f in glob.glob('start_drone*.py'):
    patch_file(f)
