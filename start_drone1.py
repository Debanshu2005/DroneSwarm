import asyncio
import glob
import socket
import subprocess
import sys
import time
from pathlib import Path
import os

# Add the project root to sys.path so DroneOS can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DroneOS.shared.config.loader import load_yaml_config
from DroneOS.shared.config.models import DroneConfig, FlightConfig

def resolve_serial(vehicle_name: str, conn_str: str) -> str:
    if not conn_str.startswith("serial://auto:"):
        return conn_str
    
    baud = conn_str.split(":")[-1]
    device = None
    
    by_id_paths = sorted(glob.glob("/dev/serial/by-id/*"))
    acm_paths = sorted(glob.glob("/dev/ttyACM*"))
    usb_paths = sorted(glob.glob("/dev/ttyUSB*"))
    ama_paths = sorted(glob.glob("/dev/ttyAMA*"))
    
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


def wait_for_relay_ready(relay_proc, host: str, port: int, timeout: float = 45.0) -> None:
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True, write_through=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True, write_through=True)

    if "--perception" in sys.argv:
        os.environ["DRONEOS_PERCEPTION"] = "1"
        sys.argv.remove("--perception")
    elif "--no-perception" in sys.argv:
        os.environ["DRONEOS_PERCEPTION"] = "0"
        sys.argv.remove("--no-perception")
    elif "DRONEOS_PERCEPTION" not in os.environ:
        os.environ["DRONEOS_PERCEPTION"] = "1"

    if "DRONEOS_PROFILE" not in os.environ and sys.platform.startswith("win"):
        os.environ["DRONEOS_PROFILE"] = "test"
    config_dir = Path(__file__).resolve().parent / "DroneOS" / "configs"
    drone_cfg = load_yaml_config(config_dir / "drone.yaml", DroneConfig)
    from DroneOS.shared.config.profile import resolve_flight_config
    flight_cfg = resolve_flight_config(config_dir, FlightConfig)
    if os.environ.get("DRONEOS_PROFILE") not in ("sim", "test"):
        resolve_serial(drone_cfg.vehicle_name, flight_cfg.px4_connection_string)
    
    print(f"[{drone_cfg.drone_id}] Starting DroneOS Lifecycle Manager...")
    
    is_sim = os.environ.get("DRONEOS_PROFILE") in ("sim", "test")
    sim_ws_port = "8081"  # Drone 1 relay WS port
    
    print(f"[{drone_cfg.drone_id}] Starting Relay...")
    
    # 3. Spawn Relay manually
    relay_script = Path(__file__).resolve().parent / "relay" / "relay.py"
    relay_args = sys.argv[1:]
    if is_sim:
        relay_args = ["--ws-host", "0.0.0.0", "--ws-port", "8081",
                      "--udp-bind-host", "127.0.0.1", "--udp-bind-port", "14650",
                      "--udp-target-host", "127.0.0.1", "--udp-target-port", "14550",
                      "--udp-target", "drone1=127.0.0.1:14550",
                      "--udp-target", "drone2=127.0.0.1:14551",
                      "--udp-target", "drone3=127.0.0.1:14552",
                      "--udp-target", "drone4=127.0.0.1:14553",
                      "--relay-ack-db", "data/relay_acks_drone1.db",
                      "--gfs-db-path", "data/gfs_drone1.db",
                      "--allow-duplicate-ips"]
    relay_proc = subprocess.Popen(
        [sys.executable, "-u", str(relay_script)] + relay_args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    if is_sim:
        wait_for_relay_ready(relay_proc, "127.0.0.1", int(sim_ws_port))

    
    # 5. Run the DroneOS application
    from DroneOS.main import DroneOSApp
    
    # Fake sys.argv so DroneOS uses its own config directory correctly
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
