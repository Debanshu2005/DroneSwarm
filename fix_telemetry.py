import glob
for f in glob.glob('DroneOS*/core/telemetry_publisher.py'):
    with open(f, 'r') as file:
        content = file.read()
    content = content.replace('== "sim"', 'in ("sim", "test")')
    content = content.replace("== 'sim'", "in ('sim', 'test')")
    with open(f, 'w') as file:
        file.write(content)
