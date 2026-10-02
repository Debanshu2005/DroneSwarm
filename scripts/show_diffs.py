import sys
import re
import difflib

def diff_files(src, target_pkg, target_file):
    with open(src, 'r') as f:
        src_lines = f.readlines()
    
    try:
        with open(target_file, 'r') as f:
            tgt_content = f.read()
    except:
        return
        
    tgt_content = re.sub(f'{target_pkg}\\.', 'DroneOS.', tgt_content)
    tgt_content = re.sub(f'from {target_pkg} ', 'from DroneOS ', tgt_content)
    tgt_content = re.sub(f'import {target_pkg}\\b', 'import DroneOS', tgt_content)
    
    tgt_lines = tgt_content.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(src_lines, tgt_lines, fromfile=src, tofile=target_file))
    if diff:
        return "".join(diff)
    return ""

output = []
for pkg in ["DroneOS1", "DroneOS2", "DroneOS3"]:
    for f in ["core/flight_pipeline.py", "core/decision_engine.py", "core/collision_avoidance.py", "core/smart_rtl_engine.py", "core/telemetry_publisher.py", "shared/communication/network_node.py"]:
        res = diff_files(f"DroneOS/{f}", pkg, f"{pkg}/{f}")
        if res:
            output.append(res)

with open('diff_utf8.txt', 'w', encoding='utf-8') as f:
    f.write("".join(output))
