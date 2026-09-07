import asyncio
import glob
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
import os

# Add the project root to sys.path so DroneOS2 can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DroneOS2.shared.config.loader import load_yaml_config
from DroneOS2.shared.config.models import DroneConfig, FlightConfig

def resolve_serial(vehicle_name: str, conn_str: str) -> str:
    if not conn_str.startswith("serial://auto:"):
        return conn_str

    baud = conn_str.split(":")[-1]
    device = None

    by_id_paths = sorted(glob.glob("/dev/serial/by-id/*"))
    acm_paths = sorted(glob.glob("/dev/ttyACM*"))
    usb_paths = sorted(glob.glob("/dev/ttyUSB*"))
    ama_paths = sorted(glob.glob("/dev/ttyAMA*"))

    match = re.search(r'\d+', vehicle_name)
    idx = 0  # Changed: Each Pi only has 1 device connected, so always use the first one

    if by_id_paths and len(by_id_paths) > idx:
        device = by_id_paths[idx]
    elif acm_paths and len(acm_paths) > idx:
        device = acm_paths[idx]
    elif usb_paths and len(usb_paths) > idx:
        device = usb_paths[idx]
    elif ama_paths and len(ama_paths) > idx:
        device = ama_paths[idx]
    elif by_id_paths:
        device = by_id_paths[-1]

    if device:
        return f"serial://{device}:{baud}"
    return conn_str

def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.1)
    return False

def get_mavsdk_server_path():
    from importlib.resources import files
    import mavsdk.bin
    exec_name = "mavsdk_server.exe" if sys.platform.startswith("win") else "mavsdk_server"
    return os.fspath(files(mavsdk.bin).joinpath(exec_name))

def main():
    config_dir = Path(__file__).resolve().parent / "DroneOS2" / "configs"
    drone_cfg = load_yaml_config(config_dir / "drone.yaml", DroneConfig)
    flight_cfg = load_yaml_config(config_dir / "flight.yaml", FlightConfig)

    resolved_conn = resolve_serial(drone_cfg.vehicle_name, flight_cfg.px4_connection_string)

    print(f"[{drone_cfg.drone_id}] Starting DroneOS Lifecycle Manager...")

    # 1. Kill any existing orphaned servers/relays
    import psutil
    server_port = 50051
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline', [])
            if proc.info['name'] and 'mavsdk_server' in proc.info['name']:
                if cmdline and any(str(server_port) in arg for arg in cmdline):
                    print(f"[{drone_cfg.drone_id}] Cleaning up old orphaned mavsdk_server (PID {proc.info['pid']})")
                    proc.kill()
            elif cmdline and 'relay.py' in ' '.join(cmdline) and '8080' in ' '.join(cmdline):
                print(f"[{drone_cfg.drone_id}] Cleaning up old orphaned relay (PID {proc.info['pid']})")
                proc.kill()
        except Exception:
            pass

    time.sleep(1.0)

    # 2. Spawn MAVSDK Server on unique port
    mavsdk_bin = get_mavsdk_server_path()
    print(f"[{drone_cfg.drone_id}] Starting {mavsdk_bin} on port {server_port}")

    mavsdk_proc = subprocess.Popen(
        [mavsdk_bin, "-p", str(server_port), resolved_conn],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    if not wait_for_port(server_port):
        print(f"[{drone_cfg.drone_id}] ERROR: MAVSDK server failed to listen on port {server_port}.")
        mavsdk_proc.kill()
        sys.exit(1)

    print(f"[{drone_cfg.drone_id}] MAVSDK server ready. Starting Relay...")

    # 3. Spawn Relay on default ports (WS 8080, UDP 14550/14551)
    relay_script = Path(__file__).resolve().parent / "relay" / "relay.py"
    relay_proc = subprocess.Popen(
        [sys.executable, str(relay_script)]
    )

    # 4. Monkey-patch mavsdk.System so DroneOS2 connects to the already-running
    #    mavsdk_server on server_port WITHOUT spawning a new one.
    import mavsdk
    old_init = mavsdk.System.__init__
    def patched_init(self, *args, **kwargs):
        kwargs['mavsdk_server_address'] = '127.0.0.1'
        kwargs['port'] = server_port
        old_init(self, *args, **kwargs)
    mavsdk.System.__init__ = patched_init

    # Tell px4_adapter not to kill the pre-spawned mavsdk_server
    import os
    os.environ['MAVSDK_SERVER_PORT'] = str(server_port)

    # 5. Run the DroneOS2 application
    from DroneOS2.main import DroneOSApp

    sys.argv = [sys.argv[0], str(config_dir)]

    app = DroneOSApp()
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

        print(f"[{drone_cfg.drone_id}] Terminating managed MAVSDK server (PID {mavsdk_proc.pid})...")
        mavsdk_proc.terminate()
        try:
            mavsdk_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            mavsdk_proc.kill()

        print(f"[{drone_cfg.drone_id}] Lifecycle Manager exit.")

if __name__ == "__main__":
    main()
