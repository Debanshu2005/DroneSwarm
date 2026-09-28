import os
import re

for drone_num, package in [("", "DroneOS2"), ("1", "DroneOS"), ("2", "DroneOS1"), ("3", "DroneOS2"), ("4", "DroneOS3")]:
    filename = f"start_drone{drone_num}.py"
    if not os.path.exists(filename): continue
    
    with open(filename, "r") as f:
        content = f.read()
        
    old_load = 'flight_cfg = load_yaml_config(config_dir / "flight.yaml", FlightConfig)'
    new_load = f'from {package}.shared.config.profile import resolve_flight_config\n    flight_cfg = resolve_flight_config(config_dir, FlightConfig)'
    content = content.replace(old_load, new_load)
    
    if 'resolve_serial(' in content:
        old_resolve = 'resolved_conn = resolve_serial(drone_cfg.vehicle_name, flight_cfg.px4_connection_string)'
        new_resolve = 'if os.environ.get("DRONEOS_PROFILE") == "sim":\n        resolved_conn = "sim"\n    else:\n        resolved_conn = resolve_serial(drone_cfg.vehicle_name, flight_cfg.px4_connection_string)'
        content = content.replace(old_resolve, new_resolve)
        
    if 'mavsdk_server' in content:
        old_server = 'server_port ='
        new_server = 'is_sim = os.environ.get("DRONEOS_PROFILE") == "sim"\n    server_port ='
        content = content.replace(old_server, new_server, 1)
        
        old_mavsdk_cond = "if proc.info['name'] and 'mavsdk_server' in proc.info['name']:"
        new_mavsdk_cond = "if not is_sim and proc.info['name'] and 'mavsdk_server' in proc.info['name']:"
        content = content.replace(old_mavsdk_cond, new_mavsdk_cond)
        
    with open(filename, "w") as f:
        f.write(content)
    print(f"Updated {filename}")
