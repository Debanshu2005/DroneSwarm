import asyncio
import glob
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
import os

# Add the project root to sys.path so DroneOS1 can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DroneOS1.shared.config.loader import load_yaml_config
from DroneOS1.shared.config.models import DroneConfig, FlightConfig

def resolve_serial(vehicle_name: str, conn_str: str) -> str:
    if not conn_str.startswith("serial://auto:"):
        return conn_str
    
    baud = conn_str.split(":")[-1]
    device = None
    
    by_id_paths = sorted(glob.glob("/dev/serial/by-id/*"))
    acm_paths = sorted(glob.glob("/dev/ttyACM*"))
    usb_paths = sorted(glob.glob("/dev/ttyUSB*"))
    
    match = re.search(r'\d+', vehicle_name)
    idx = (int(match.group()) - 1) if match else 0
    
    if by_id_paths and len(by_id_paths) > idx:
        device = by_id_paths[idx]
    elif acm_paths and len(acm_paths) > idx:
        device = acm_paths[idx]
    elif usb_paths and len(usb_paths) > idx:
        device = usb_paths[idx]
    elif by_id_paths:
        device = by_id_paths[-1]
        
    if device:
        return f"serial://{device}:{baud}"
    return conn_str

def wait_for_port(port: int, timeout: float = 10.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.1)
    return False


def main():
    config_dir = Path(__file__).resolve().parent / "DroneOS1" / "configs"
    drone_cfg = load_yaml_config(config_dir / "drone.yaml", DroneConfig)
    flight_cfg = load_yaml_config(config_dir / "flight.yaml", FlightConfig)
    
    resolved_conn = resolve_serial(drone_cfg.vehicle_name, flight_cfg.px4_connection_string)
    
    print(f"[{drone_cfg.drone_id}] Starting DroneOS Lifecycle Manager...")
    
    # 1. Kill any existing orphaned servers/relays forcefully on this Pi
    import psutil
    server_port = 50051
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline', [])
            if proc.info['name'] and 'mavsdk_server' in proc.info['name']:
                if cmdline and any(str(server_port) in arg for arg in cmdline):
                    print(f"[{drone_cfg.drone_id}] Cleaning up old orphaned mavsdk_server (PID {proc.info['pid']})")
                    proc.kill()
        except Exception:
            pass
            
    time.sleep(1.0)
    

    # 5. Run the DroneOS1 application
    from DroneOS1.main import DroneOSApp
    
    # Fake sys.argv so DroneOS1 uses its own config directory correctly
    sys.argv = [sys.argv[0], str(config_dir)]
    
    app = DroneOSApp()
    
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        import logging
        logging.warning("uvloop not available, using default asyncio event loop")
        
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass
    finally:

        print(f"[{drone_cfg.drone_id}] Lifecycle Manager exit.")

if __name__ == "__main__":
    main()
