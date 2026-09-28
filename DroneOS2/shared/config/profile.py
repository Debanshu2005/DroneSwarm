import os
import logging
import yaml
from pathlib import Path
from DroneOS2.shared.config.loader import load_yaml_config

logger = logging.getLogger(__name__)

def deep_merge(d1, d2):
    for k, v in d2.items():
        if isinstance(v, dict) and k in d1 and isinstance(d1[k], dict):
            deep_merge(d1[k], v)
        else:
            d1[k] = v
    return d1

def resolve_flight_config(config_dir: Path, flight_config_cls):
    """
    Resolves the flight configuration by checking DRONEOS_PROFILE.
    Unset or 'hw': loads configs/flight.yaml directly.
    'sim': loads configs/flight.yaml, then deep merges configs/flight.sim.yaml over it.
    """
    profile = os.environ.get("DRONEOS_PROFILE", "hw")
    config_dir = Path(config_dir)
    base_flight_path = config_dir / "flight.yaml"
    sim_flight_path = config_dir / "flight.sim.yaml"
    
    # Load base config
    with open(base_flight_path, 'r') as f:
        base_data = yaml.safe_load(f) or {}
        
    if profile == "sim":
        if sim_flight_path.exists():
            with open(sim_flight_path, 'r') as f:
                sim_data = yaml.safe_load(f) or {}
            merged_data = deep_merge(base_data, sim_data)
        else:
            logger.warning(f"Profile is 'sim' but {sim_flight_path} not found.")
            merged_data = base_data
            
        flight_cfg = flight_config_cls(**merged_data)
        
        if flight_cfg.adapter_type == "px4":
            logger.error("PROFILE is 'sim' but resolved adapter is 'px4'. Refusing to start.")
            import sys
            sys.exit(1)
    else:
        flight_cfg = flight_config_cls(**base_data)
        if flight_cfg.adapter_type != "px4":
            logger.error("PROFILE is 'hw' (or unset) but resolved adapter_type is not 'px4'.")
            
    return flight_cfg

def resolve_network_config(config_dir: Path, network_config_cls):
    """Load the production network config, overlaying network.sim.yaml in sim."""
    config_dir = Path(config_dir)
    with open(config_dir / "network.yaml", "r") as f:
        network_data = yaml.safe_load(f) or {}
    if os.environ.get("DRONEOS_PROFILE", "hw") == "sim":
        sim_path = config_dir / "network.sim.yaml"
        if sim_path.exists():
            with open(sim_path, "r") as f:
                network_data = deep_merge(network_data, yaml.safe_load(f) or {})
        else:
            logger.warning(f"Profile is 'sim' but {sim_path} not found.")
    return network_config_cls(**network_data)

def log_startup_banner(drone_id: str, vehicle_name: str, adapter_type: str):
    profile = os.environ.get("DRONEOS_PROFILE", "hw")
    msg = f"BACKEND={adapter_type} PROFILE={profile} drone={drone_id} vehicle={vehicle_name}"
    print(msg)
    logger.info(msg)
