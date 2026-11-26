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
    parser.add_argument("--log-dir", type=str, default=None, help="Directory to save per-drone stdout logs")
    args = parser.parse_args()

    env = os.environ.copy()
    env["DRONEOS_PROFILE"] = "sim"
    env["PYTHONUNBUFFERED"] = "1"
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    # Pre-clean any stale relays or DroneOS processes
    import psutil
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = ' '.join(proc.info.get('cmdline') or [])
            if 'relay.py' in cmd or 'start_drone' in cmd:
                if proc.pid != os.getpid():
                    proc.kill()
        except Exception:
            pass
    time.sleep(0.5)

    print("=" * 70)
    print(f"Launching {args.drones} Simulation Drones (PROFILE=sim, start_stagger_s={args.stagger}s)")
    print("=" * 70)

    procs = []
    log_files = []
    for i in range(1, args.drones + 1):
        script_name = f"start_drone{i}.py"
        script_path = REPO_ROOT / script_name
        if not script_path.exists():
            print(f"Warning: {script_name} not found, skipping Drone{i}")
            continue

        print(f"Starting Drone{i} ({script_name})...")
        stdout_dest = None
        if log_dir:
            lf = open(log_dir / f"drone{i}.log", "w", encoding="utf-8")
            log_files.append(lf)
            stdout_dest = lf
        p = subprocess.Popen([sys.executable, "-u", str(script_path)], cwd=str(REPO_ROOT), env=env, stdout=stdout_dest, stderr=subprocess.STDOUT)
        procs.append((f"Drone{i}", p))

        if i < args.drones and args.stagger > 0:
            time.sleep(args.stagger)

    print(f"\nAll {len(procs)} drones launched. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.5)
            if log_dir and (log_dir / "stop.flag").exists():
                print("Stop flag detected. Terminating swarm cleanly...")
                break
            for name, p in procs:
                ret = p.poll()
                if ret is not None:
                    print(f"Process {name} exited with code {ret}")
    except KeyboardInterrupt:
        print("\nTerminating swarm processes...")
    finally:
        print("Stopping drone processes and flushing logs...")
        for name, p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for name, p in procs:
            try:
                p.wait(timeout=4.0)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        for lf in log_files:
            try:
                lf.flush()
                lf.close()
            except Exception:
                pass
        print("Swarm stopped.")

if __name__ == "__main__":
    main()
