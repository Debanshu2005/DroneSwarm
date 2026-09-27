import glob

files = glob.glob('DroneOS*/configs/flight.yaml')

for f in files:
    with open(f, 'r') as file:
        lines = file.readlines()
    
    new_lines = []
    skip = False
    for line in lines:
        if line.startswith('px4_connection_candidates:'):
            skip = True
        elif skip and line.startswith('  -'):
            continue
        elif skip and not line.startswith('  -') and line.strip():
            skip = False
            new_lines.append(line)
        elif not skip:
            new_lines.append(line)
            
    with open(f, 'w') as file:
        file.writelines(new_lines)
