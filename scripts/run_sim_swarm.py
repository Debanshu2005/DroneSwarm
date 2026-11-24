"""
Launch simulation swarm (Drone1..Drone4) with DRONEOS_PROFILE=sim and startup stagger.
Default startup stagger in sim is 1.0s to avoid GPU CUDA warmup contention.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser(description="Launch DroneOS 4-drone simulation swarm")
    parser.add_argument("--stagger", type=float, default=1.0, help="Startup stagger in seconds between drones (default: 1.0s)")
    parser.add_argument("--drones", type=int, default=4, help="Number of drones to launch (default: 4)")
    args = parser.parse_args()

    env = os.environ.copy()
    env["DRONEOS_PROFILE"] = "sim"

    print("=" * 70)
    print(f"Launching {args.drones} Simulation Drones (PROFILE=sim, start_stagger_s={args.stagger}s)")
    print("=" * 70)

    procs = []
    for i in range(1, args.drones + 1):
        script_name = f"start_drone{i}.py"
        script_path = REPO_ROOT / script_name
        if not script_path.exists():
            print(f"Warning: {script_name} not found, skipping Drone{i}")
            continue

        print(f"Starting Drone{i} ({script_name})...")
        p = subprocess.Popen([sys.executable, str(script_path)], cwd=str(REPO_ROOT), env=env)
        procs.append((f"Drone{i}", p))

        if i < args.drones and args.stagger > 0:
            time.sleep(args.stagger)

    print(f"\nAll {len(procs)} drones launched. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
            for name, p in procs:
                ret = p.poll()
                if ret is not None:
                    print(f"Process {name} exited with code {ret}")
    except KeyboardInterrupt:
        print("\nTerminating swarm processes...")
        for name, p in procs:
            p.terminate()
        for name, p in procs:
            p.wait()
        print("Swarm stopped.")

if __name__ == "__main__":
    main()
