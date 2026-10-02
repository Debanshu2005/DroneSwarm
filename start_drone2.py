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


def wait_for_relay_ready(relay_proc, host: str, port: int, timeout: float = 5.0) -> None:
    """Fail fast if a stale relay owns this drone's WebSocket endpoint."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = relay_proc.poll()
        if exit_code is not None:
            raise RuntimeError(f"Relay exited before becoming ready (exit code {exit_code}).")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                # Do not mistake an older listener for the child we just spawned.
                time.sleep(0.2)
                if relay_proc.poll() is None:
                    return
        except OSError:
            pass
        time.sleep(0.1)
    relay_proc.terminate()
    raise RuntimeError(f"Relay did not listen on {host}:{port} within {timeout:.1f}s.")



def main():
    config_dir = Path(__file__).resolve().parent / "DroneOS1" / "configs"
    drone_cfg = load_yaml_config(config_dir / "drone.yaml", DroneConfig)
    from DroneOS1.shared.config.profile import resolve_flight_config
    flight_cfg = resolve_flight_config(config_dir, FlightConfig)
    
    if os.environ.get("DRONEOS_PROFILE") == "sim":
        resolved_conn = "sim"
    else:
        resolved_conn = resolve_serial(drone_cfg.vehicle_name, flight_cfg.px4_connection_string)
    
    print(f"[{drone_cfg.drone_id}] Starting DroneOS Lifecycle Manager...")
    
    # 1. Kill any existing orphaned servers/relays forcefully on this Pi
    import psutil
    is_sim = os.environ.get("DRONEOS_PROFILE") == "sim"
    server_port = 50051
    sim_ws_port = "8082"  # Drone 2 relay WS port
    drone_udp_port = 14551  # Drone 2 DroneOS UDP listen port

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmdline_str = ' '.join(cmdline)
            if not is_sim and proc.info['name'] and 'mavsdk_server' in proc.info['name']:
                print(f"[{drone_cfg.drone_id}] Cleaning up old orphaned mavsdk_server (PID {proc.info['pid']})")
                proc.kill()
            elif cmdline and 'relay.py' in cmdline_str:
                if is_sim and sim_ws_port not in cmdline_str:
                    continue  # Not our relay, skip
                print(f"[{drone_cfg.drone_id}] Cleaning up old orphaned relay (PID {proc.info['pid']})")
                proc.kill()
        except Exception:
            pass

    # Kill any process still holding our DroneOS UDP port (zombie guard).
    for proc in psutil.process_iter(['pid']):
        try:
            for conn in proc.net_connections(kind='udp'):
                if conn.laddr.port == drone_udp_port:
                    print(f"[{drone_cfg.drone_id}] Releasing UDP port {drone_udp_port} held by zombie PID {proc.pid}")
                    proc.kill()
                    break
        except Exception:
            pass

    time.sleep(1.0)
    
    # 3. Spawn Relay manually
    relay_script = Path(__file__).resolve().parent / "relay" / "relay.py"
    relay_args = sys.argv[1:]
    if is_sim:
        relay_args = ["--ws-host", "0.0.0.0", "--ws-port", "8082",
                      "--udp-bind-host", "127.0.0.1", "--udp-bind-port", "14651",
                      "--udp-target-host", "127.0.0.1", "--udp-target-port", "14551",
                      "--udp-target", "drone1=127.0.0.1:14550",
                      "--udp-target", "drone2=127.0.0.1:14551",
                      "--udp-target", "drone3=127.0.0.1:14552",
                      "--udp-target", "drone4=127.0.0.1:14553"]
    relay_proc = subprocess.Popen(
        [sys.executable, str(relay_script)] + relay_args,
        stdout=None if os.environ.get("DRONEOS_DIAGNOSTIC_TRACE") else subprocess.DEVNULL,
        stderr=None if os.environ.get("DRONEOS_DIAGNOSTIC_TRACE") else subprocess.DEVNULL
    )
    if is_sim:
        wait_for_relay_ready(relay_proc, "127.0.0.1", int(sim_ws_port))

    # 5. Run the DroneOS1 application
    from DroneOS1.main import DroneOSApp
    
    # Fake sys.argv so DroneOS1 uses its own config directory correctly
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
            
        print(f"[{drone_cfg.drone_id}] Lifecycle Manager exit.")

if __name__ == "__main__":
    main()
