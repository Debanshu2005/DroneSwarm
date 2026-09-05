import asyncio
import sys
from pathlib import Path
import os
import logging

# Add the project root to sys.path so DroneOS2 can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from DroneOS2.shared.config.loader import load_yaml_config
from DroneOS2.shared.config.models import DroneConfig, FlightConfig

def main():
    config_dir = Path(__file__).resolve().parent / "DroneOS2" / "configs"
    drone_cfg = load_yaml_config(config_dir / "drone.yaml", DroneConfig)
    flight_cfg = load_yaml_config(config_dir / "flight.yaml", FlightConfig)
    
    print(f"[{drone_cfg.drone_id}] Starting DroneOS Lifecycle Manager...")
    print(f"[{drone_cfg.drone_id}] MAVSDK Server lifecycle is delegated to MAVSDK-Python.")
    
    # 1. Run the DroneOS2 application
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
        print(f"[{drone_cfg.drone_id}] Lifecycle Manager exit.")

if __name__ == "__main__":
    main()
