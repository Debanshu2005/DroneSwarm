import os
import re
import difflib

files_to_sync = [
    "core/flight_pipeline.py",
    "core/decision_engine.py",
    "core/collision_avoidance.py",
    "core/smart_rtl_engine.py",
    "core/telemetry_publisher.py",
    "shared/communication/network_node.py"
]

targets = ["DroneOS1", "DroneOS2", "DroneOS3"]

for rel_path in files_to_sync:
    src_path = f"DroneOS/{rel_path}"
    if not os.path.exists(src_path):
        continue
    with open(src_path, "r", encoding="utf-8") as f:
        src_content = f.read()
    
    src_lines = src_content.splitlines()

    for target in targets:
        dst_path = f"{target}/{rel_path}"
        if not os.path.exists(dst_path):
            continue
        
        with open(dst_path, "r", encoding="utf-8") as f:
            curr_content = f.read()
            
        # Normalize back to DroneOS
        curr_content = re.sub(f'{target}\\.', 'DroneOS.', curr_content)
        curr_content = re.sub(f'from {target} ', 'from DroneOS ', curr_content)
        curr_content = re.sub(f'import {target}\\b', 'import DroneOS', curr_content)
        
        curr_lines = curr_content.splitlines()
        
        diff = list(difflib.ndiff(src_lines, curr_lines))
        
        extra_lines = [line[2:] for line in diff if line.startswith('+ ')]
        
        if extra_lines:
            print(f"\nExtra lines in {dst_path} compared to {src_path}:")
            for line in extra_lines:
                print(line)
