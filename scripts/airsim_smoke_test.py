import argparse
import asyncio
import time
import sys
import importlib
import yaml
import math
import os
from pathlib import Path

def calculate_distance(lat1, lon1, lat2, lon2):
    return math.sqrt(((lat1 - lat2) * 111320.0)**2 + ((lon1 - lon2) * 111320.0 * math.cos(math.radians(lat1)))**2)

async def run_smoke_test(args):
    try:
        import airsim
    except ImportError:
        if os.environ.get("ALLOW_SKIP") == "1":
            print("AirSim module not found. Exiting 0 (SKIPPED).")
            sys.exit(0)
        else:
            print("AirSim module not found. Exiting 1.")
            sys.exit(1)

    try:
        pkg_core_pipeline = importlib.import_module(f"{args.pkg}.core.flight_pipeline")
        pkg_core_intents = importlib.import_module(f"{args.pkg}.core.intents")
        pkg_adapter = importlib.import_module(f"{args.pkg}.adapters.airsim_adapter")
        pkg_profile = importlib.import_module(f"{args.pkg}.shared.config.profile")
        pkg_config = importlib.import_module(f"{args.pkg}.shared.config.models")
    except ImportError as e:
        print(f"Failed to import package {args.pkg}: {e}")
        sys.exit(1)

    CommandWriter = pkg_core_pipeline.CommandWriter
    FlightIntent = pkg_core_intents.FlightIntent
    IntentSource = pkg_core_intents.IntentSource
    IntentAction = pkg_core_intents.IntentAction
    AirSimFlightController = pkg_adapter.AirSimFlightController
    
    os.environ["DRONEOS_PROFILE"] = "sim"
    flight_config = pkg_profile.resolve_flight_config(Path(f"{args.pkg}/configs"), pkg_config.FlightConfig)
    assert flight_config.adapter_type == 'airsim', f"Expected adapter_type='airsim' in sim profile, got {flight_config.adapter_type}"

    adapter = AirSimFlightController(args.vehicle, flight_config)
    writer = CommandWriter(adapter)

    print("Connecting to AirSim...")
    start = time.time()
    connected = await adapter.connect()
    print(f"Connect returned in {time.time()-start:.3f}s")
    if not connected:
        if os.environ.get("ALLOW_SKIP") == "1":
            print("Simulator unavailable. Exiting 0 (SKIPPED).")
            sys.exit(0)
        else:
            print("Simulator unavailable. Exiting 1.")
            sys.exit(1)

    try:
        home = await adapter.get_home_position()
        telem = await adapter.get_telemetry()
        print(f"Home: {home}")
        print(f"Telem: lat={telem.latitude}, lon={telem.longitude}, alt={telem.altitude}")

        print("Arming...")
        start = time.time()
        await adapter.arm()
        print(f"Arm returned in {time.time()-start:.3f}s")
        while (await adapter.get_telemetry()).armed_state != "ARMED":
            await asyncio.sleep(0.1)

        print("Takeoff to 5m...")
        start = time.time()
        intent_takeoff = FlightIntent(IntentSource.MANUAL, IntentAction.TAKEOFF, params={'altitude': 5.0})
        await writer.execute(intent_takeoff)
        print(f"Takeoff (blocking) returned in {time.time()-start:.3f}s")

        print("Moving forward 3m (3s of move_velocity @ 10Hz)...")
        # 10 Hz = 30 calls
        start_move = time.time()
        for i in range(30):
            intent_vel = FlightIntent(IntentSource.MANUAL, IntentAction.MOVE_VELOCITY, params={'vx': 1.0, 'vy': 0.0, 'vz': 0.0, 'duration': 0.1})
            start = time.time()
            await writer.execute(intent_vel)
            if i == 0:
                print(f"First move_velocity returned in {time.time()-start:.3f}s")
            await asyncio.sleep(0.1)
        print(f"Completed 3s of velocity commands in {time.time()-start_move:.3f}s")
        
        # Stop
        intent_hover = FlightIntent(IntentSource.MANUAL, IntentAction.HOVER)
        await writer.execute(intent_hover)

        # Goto 10m north
        target_lat = home[0] + (10.0 / 111320.0)
        target_lon = home[1]
        print(f"Goto 10m North ({target_lat}, {target_lon})...")
        intent_goto = FlightIntent(IntentSource.MANUAL, IntentAction.GOTO, params={'lat': target_lat, 'lon': target_lon, 'alt': 5.0})
        start = time.time()
        await writer.execute(intent_goto)
        print(f"Goto returned in {time.time()-start:.3f}s")

        print("Waiting for completion...")
        start = time.time()
        while True:
            t = await adapter.get_telemetry()
            dist = calculate_distance(t.latitude, t.longitude, target_lat, target_lon)
            if dist < 2.0:
                break
            await asyncio.sleep(0.5)
        print(f"Reached destination in {time.time()-start:.3f}s")

        print("RTL...")
        intent_rtl = FlightIntent(IntentSource.MANUAL, IntentAction.RTL)
        start = time.time()
        await writer.execute(intent_rtl)
        print(f"RTL returned in {time.time()-start:.3f}s")
        
        print("Waiting for DISARMED...")
        start = time.time()
        while (await adapter.get_telemetry()).armed_state != "DISARMED":
            await asyncio.sleep(0.5)
        print(f"Disarmed after {time.time()-start:.3f}s")

    finally:
        print("Disconnecting...")
        await adapter.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pkg", type=str, default="DroneOS", help="Package name (e.g. DroneOS)")
    parser.add_argument("--vehicle", type=str, default="Drone1", help="Vehicle name in AirSim")
    args = parser.parse_args()
    asyncio.run(run_smoke_test(args))
