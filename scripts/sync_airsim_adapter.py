import os
import sys
import re

def sync():
    check_only = "--check" in sys.argv
    changed = False

    files_to_sync = [
        ("DroneOS/adapters/airsim_adapter.py", "adapters/airsim_adapter.py"),
        ("DroneOS/tests/test_airsim_adapter.py", "tests/test_airsim_adapter.py"),
        ("DroneOS/tests/test_collision_avoidance.py", "tests/test_collision_avoidance.py"),
        ("DroneOS/tests/test_sim_config.py", "tests/test_sim_config.py"),
        ("DroneOS/main.py", "main.py"),
        ("DroneOS/shared/config/profile.py", "shared/config/profile.py"),
    ]

    targets = ["DroneOS1", "DroneOS2", "DroneOS3"]

    for src_path, rel_path in files_to_sync:
        if not os.path.exists(src_path):
            print(f"Source file {src_path} not found.")
            sys.exit(1)

        with open(src_path, "r", encoding="utf-8") as f:
            src_content = f.read()

        for target in targets:
            dst_path = f"{target}/{rel_path}"
            
            # Rewrite DroneOS. to Target.
            dst_content = re.sub(r'DroneOS\.', f'{target}.', src_content)
            
            if os.path.exists(dst_path):
                with open(dst_path, "r", encoding="utf-8") as f:
                    curr_content = f.read()
            else:
                curr_content = None

            if dst_content != curr_content:
                changed = True
                if check_only:
                    print(f"Drift detected in {dst_path}")
                else:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    with open(dst_path, "w", encoding="utf-8") as f:
                        f.write(dst_content)
                    print(f"Updated {dst_path}")
            else:
                if not check_only:
                    print(f"{dst_path} is up to date.")

    if check_only and changed:
        sys.exit(1)
    elif check_only:
        print("All files are in sync.")

if __name__ == "__main__":
    sync()
