import glob

files = glob.glob('DroneOS*/adapters/px4_adapter.py')

for f in files:
    with open(f, 'r') as file:
        content = file.read()
    
    # Timeouts
    content = content.replace('timeout=10.0', 'timeout=30.0')
    content = content.replace('timeout=15.0', 'timeout=45.0')
    
    with open(f, 'w') as file:
        file.write(content)
