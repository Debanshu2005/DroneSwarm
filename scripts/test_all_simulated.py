import asyncio
import importlib
import yaml
import yaml
import sys
import time
import os

async def test_drone(pkg_name, vehicle_name):
    print(f"[{pkg_name}] Initializing...")
    try:
        pkg_adapter = importlib.import_module(f"{pkg_name}.adapters.airsim_adapter")
        pkg_profile = importlib.import_module(f"{pkg_name}.shared.config.profile")
        pkg_config = importlib.import_module(f"{pkg_name}.shared.config.models")
    except ImportError as e:
        print(f"[{pkg_name}] Failed to import package: {e}")
        return False
        
    os.environ["DRONEOS_PROFILE"] = "sim"
    flight_config = pkg_profile.resolve_flight_config(f"{pkg_name}/configs", pkg_config.FlightConfig)
    assert flight_config.adapter_type == 'airsim', f"Expected adapter_type='airsim' in {pkg_name}, got {flight_config.adapter_type}"
    
    adapter = pkg_adapter.AirSimFlightController(vehicle_name, flight_config)
    
    print(f"[{pkg_name}] Connecting...")
    connected = await adapter.connect()
    if not connected:
        if os.environ.get("ALLOW_SKIP") == "1":
            print(f"[{pkg_name}] Simulator unavailable. Exiting 0 (SKIPPED).")
            return True
        else:
            print(f"[{pkg_name}] Simulator unavailable. Ensure AirSim is running.")
            return False
        
    print(f"[{pkg_name}] Connected. Arming...")
    await adapter.arm()
    
    start_wait = time.time()
    while (await adapter.get_telemetry()).armed_state != "ARMED":
        if time.time() - start_wait > 5.0:
            print(f"[{pkg_name}] Failed to arm in time.")
            return False
        await asyncio.sleep(0.1)
        
    print(f"[{pkg_name}] Armed successfully.")
    
    await asyncio.sleep(2.0)
    
    print(f"[{pkg_name}] Disarming...")
    await adapter.disarm()
    
    start_wait = time.time()
    while (await adapter.get_telemetry()).armed_state != "DISARMED":
        if time.time() - start_wait > 5.0:
            print(f"[{pkg_name}] Failed to disarm in time.")
            return False
        await asyncio.sleep(0.1)
        
    print(f"[{pkg_name}] Disarmed successfully. Disconnecting...")
    await adapter.disconnect()
    
    print(f"[{pkg_name}] Done.")
    return True

async def main():
    drones = [
        ("DroneOS", "Drone1"),
        ("DroneOS1", "Drone2"),
        ("DroneOS2", "Drone3"),
        ("DroneOS3", "Drone4")
    ]
    
    print("Starting concurrent test of 4 simulated drones...")
    results = await asyncio.gather(*(test_drone(pkg, vehicle) for pkg, vehicle in drones))
    
    if all(results):
        print("Swarm simulation backend test passed!")
        sys.exit(0)
    else:
        print("Swarm simulation backend test failed!")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
