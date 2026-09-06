import asyncio
import sys
from pathlib import Path
import os
import logging
import subprocess
import time

# Add the project root to sys.path so DroneOS2 can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DroneOS2.shared.config.loader import load_yaml_config
from DroneOS2.shared.config.models import DroneConfig, FlightConfig

def main():
    project_root = Path(__file__).resolve().parent
    config_dir = Path(__file__).resolve().parent / "DroneOS2" / "configs"
    drone_cfg = load_yaml_config(config_dir / "drone.yaml", DroneConfig)
    flight_cfg = load_yaml_config(config_dir / "flight.yaml", FlightConfig)
    
    print(f"[{drone_cfg.drone_id}] Starting DroneOS Lifecycle Manager...")
    print(f"[{drone_cfg.drone_id}] MAVSDK Server lifecycle is delegated to MAVSDK-Python.")

    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                joined = ' '.join(cmdline)
                if 'relay.py' in joined and str(project_root) in joined:
                    print(f"[{drone_cfg.drone_id}] Cleaning up old orphaned relay (PID {proc.info['pid']})")
                    proc.kill()
            except Exception:
                pass
    except ImportError:
        logging.warning("psutil not available, skipping orphaned relay cleanup")

    time.sleep(1.0)

    print(f"[{drone_cfg.drone_id}] Starting Relay...")
    relay_script = project_root / "relay" / "relay.py"
    relay_proc = subprocess.Popen(
        [sys.executable, str(relay_script),
         "--ws-port", "8082",
         "--udp-bind-port", "14553",
         "--udp-target-port", "14552"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Run the DroneOS2 application
    from DroneOS2.main import DroneOSApp
    
    # Fake sys.argv so DroneOS2 uses its own config directory correctly
    sys.argv = [sys.argv[0], str(config_dir)]
    
    app = DroneOSApp()
    
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        logging.warning("uvloop not available, using default asyncio event loop")
        
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass
    finally:
        print(f"[{drone_cfg.drone_id}] Terminating managed Relay (PID {relay_proc.pid})...")
        relay_proc.terminate()
        try:
            relay_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            relay_proc.kill()

        print(f"[{drone_cfg.drone_id}] Lifecycle Manager exit.")

if __name__ == "__main__":
    main()
