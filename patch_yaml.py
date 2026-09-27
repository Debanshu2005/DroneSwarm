import glob
import re

files = glob.glob('DroneOS*/configs/flight.yaml')

for f in files:
    with open(f, 'r') as file:
        content = file.read()
    
    # Remove px4_connection_candidates entirely to prevent race conditions on baud scan
    content = re.sub(r'px4_connection_candidates:.*?(?=^[a-zA-Z]|\Z)', '', content, flags=re.MULTILINE | re.DOTALL)
    
    with open(f, 'w') as file:
        file.write(content)
