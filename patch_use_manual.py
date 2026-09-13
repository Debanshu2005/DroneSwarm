import re
import glob

files = glob.glob('*/adapters/px4_adapter.py')
for f in files:
    with open(f, 'r') as file:
        content = file.read()
    
    def replacer(match):
        indent = match.group(1)
        return f'{indent}has_nav = telemetry.gps_valid or telemetry.local_pos_valid\n{indent}use_manual = (not has_nav) or (mode_upper in ["ALTCTL", "MANUAL", "STABILIZED"])'
    
    new_content = re.sub(r'([ \t]+)use_manual = \(not telemetry\.gps_valid\) or \(mode_upper in \["ALTCTL", "MANUAL", "STABILIZED"\]\)', replacer, content)
    
    with open(f, 'w') as file:
        file.write(new_content)
    print(f'Updated {f}')
