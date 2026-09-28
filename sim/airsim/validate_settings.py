import json
import os
import sys
import yaml

def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)

def pass_check(msg):
    print(f"PASS: {msg}")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(script_dir, "settings.json")
    project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))

    # 1. Parse settings.json
    try:
        with open(settings_path, "r") as f:
            settings = json.load(f)
        pass_check("settings.json is valid JSON.")
    except Exception as e:
        fail(f"settings.json is invalid JSON: {e}")

    # Check SimMode
    if settings.get("SimMode") != "Multirotor":
        fail("SimMode is not Multirotor.")
    pass_check("SimMode is Multirotor.")

    # 2. Read configs from the repo
    packages = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]
    repo_vehicles = {}
    airsim_ports = set()
    
    print("\n--- Adapter Types ---")

    for pkg in packages:
        drone_yaml = os.path.join(project_root, pkg, "configs", "drone.yaml")
        flight_yaml = os.path.join(project_root, pkg, "configs", "flight.yaml")
        
        if not os.path.exists(drone_yaml) or not os.path.exists(flight_yaml):
            fail(f"Missing config files for {pkg}")
            
        with open(drone_yaml, "r") as f:
            d_cfg = yaml.safe_load(f)
            v_name = d_cfg.get("vehicle_name")
            if not v_name:
                fail(f"No vehicle_name found in {pkg}/configs/drone.yaml")
            repo_vehicles[v_name] = pkg
            
        with open(flight_yaml, "r") as f:
            f_cfg = yaml.safe_load(f)
            port = f_cfg.get("airsim_port", 41451)
            airsim_ports.add(port)
            ad_type = f_cfg.get("adapter_type", "unknown")
            print(f"INFO: {pkg} adapter_type is '{ad_type}'")

    print("---------------------\n")

    # 3. Check vehicles
    settings_vehicles = settings.get("Vehicles", {})
    
    # Check no extra or missing vehicles
    repo_vnames = set(repo_vehicles.keys())
    settings_vnames = set(settings_vehicles.keys())
    
    missing = repo_vnames - settings_vnames
    if missing:
        fail(f"settings.json is missing vehicles from repo: {missing}")
        
    extra = settings_vnames - repo_vnames
    if extra:
        fail(f"settings.json has extra vehicles not in repo: {extra}")
        
    pass_check(f"Exact vehicle match between repo and settings.json ({repo_vnames})")

    # 4. Check VehicleType and duplicate coords
    coords = set()
    for vname, vdata in settings_vehicles.items():
        vtype = vdata.get("VehicleType")
        if vtype != "SimpleFlight":
            fail(f"Vehicle {vname} uses type {vtype}, expected SimpleFlight")
        
        x = vdata.get("X", 0)
        y = vdata.get("Y", 0)
        pos = (x, y)
        if pos in coords:
            fail(f"Vehicle {vname} has duplicate X/Y coordinates {pos}")
        coords.add(pos)
        
    pass_check("All vehicles use SimpleFlight and have unique X/Y spawn coordinates.")

    # 5. Check airsim_port consistency
    if len(airsim_ports) > 1:
        fail(f"Mismatch in airsim_port across packages: {airsim_ports}")
    
    repo_port = airsim_ports.pop()
    pass_check(f"All packages agree on airsim_port {repo_port}")

    api_port = settings.get("ApiServerPort")
    if api_port is not None:
        if api_port != repo_port:
            fail(f"settings.json ApiServerPort {api_port} does not match repo airsim_port {repo_port}")
        pass_check("ApiServerPort in settings.json matches repo port.")
    else:
        if repo_port != 41451:
            fail(f"Repo uses port {repo_port} but ApiServerPort is missing from settings.json")
        pass_check("ApiServerPort omitted because repo uses default 41451.")

    print("\nAll checks PASSED.")

if __name__ == "__main__":
    main()
