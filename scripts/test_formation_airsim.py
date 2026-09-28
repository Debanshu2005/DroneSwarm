"""
Formation smoke test for AirSim - four drones.

Uses the existing FormationManager.get_offset API to compute GRID offsets,
then commands each drone to its slot via goto_location on the AirSim adapter.
No UDP networking required; runs all four adapters in-process concurrently.

Usage (from repo root, with AirSim running):
    python scripts/test_formation_airsim.py
"""
import asyncio
import importlib
import os
import sys
import time
import math
from pathlib import Path

FORMATION_TYPE = "GRID"
SPACING_M = 8.0
TAKEOFF_ALT = 5.0
GOTO_TIMEOUT = 45.0

DRONES = [
    ("DroneOS",  "Drone1"),
    ("DroneOS1", "Drone2"),
    ("DroneOS2", "Drone3"),
    ("DroneOS3", "Drone4"),
]

os.environ["DRONEOS_PROFILE"] = "sim"


def load_adapter(pkg, vehicle):
    pkg_profile = importlib.import_module(f"{pkg}.shared.config.profile")
    pkg_models  = importlib.import_module(f"{pkg}.shared.config.models")
    pkg_adapter = importlib.import_module(f"{pkg}.adapters.airsim_adapter")

    cfg = pkg_profile.resolve_flight_config(Path(f"{pkg}/configs"), pkg_models.FlightConfig)
    return pkg_adapter.AirSimFlightController(vehicle, cfg)


def compute_grid_offsets(n, spacing):
    """Return (dx_north, dy_east) metres for each index 0..n-1 in GRID formation."""
    from DroneOS.core.formation_manager import FormationManager, FormationType
    mgr = FormationManager()
    mgr.set_formation(FormationType.GRID, spacing)
    return [mgr.get_offset(i, n)[:2] for i in range(n)]


async def connect_one(pkg, vehicle):
    print(f"[{pkg}] Connecting...")
    adapter = load_adapter(pkg, vehicle)
    ok = await adapter.connect()
    if not ok:
        print(f"[{pkg}] FAILED to connect")
        sys.exit(1)
    print(f"[{pkg}] Connected")
    return adapter


async def arm_and_takeoff(pkg, adapter):
    print(f"[{pkg}] Arming...")
    ok = await adapter.arm()
    if not ok:
        print(f"[{pkg}] FAILED to arm")
        return False

    t = await adapter.get_telemetry()
    deadline = time.time() + 10
    while t.armed_state != "ARMED" and time.time() < deadline:
        await asyncio.sleep(0.2)
        t = await adapter.get_telemetry()

    if t.armed_state != "ARMED":
        print(f"[{pkg}] Timed out waiting for ARMED")
        return False
    print(f"[{pkg}] Armed")

    print(f"[{pkg}] Taking off to {TAKEOFF_ALT}m...")
    ok = await adapter.takeoff(TAKEOFF_ALT)
    if not ok:
        print(f"[{pkg}] FAILED takeoff")
        return False
    print(f"[{pkg}] Airborne")
    return True


async def goto_slot(pkg, adapter, dx_north, dy_east):
    home = await adapter.get_home_position()
    if home is None:
        print(f"[{pkg}] No home position - cannot goto")
        return False

    home_lat, home_lon, home_alt = home
    EARTH_R = 6371000.0
    dlat = dx_north / EARTH_R * (180.0 / math.pi)
    dlon = dy_east / (EARTH_R * math.cos(math.radians(home_lat))) * (180.0 / math.pi)
    target_lat = home_lat + dlat
    target_lon = home_lon + dlon

    print(f"[{pkg}] GOTO slot ({dx_north:+.1f}m N, {dy_east:+.1f}m E) -> "
          f"lat={target_lat:.6f} lon={target_lon:.6f}")
    ok = await adapter.goto_location(target_lat, target_lon, TAKEOFF_ALT)
    if not ok:
        print(f"[{pkg}] goto_location returned False")
        return False

    # Wait for arrival (within 2m) or timeout
    deadline = time.time() + GOTO_TIMEOUT
    while time.time() < deadline:
        t = await adapter.get_telemetry()
        if t.latitude is None or t.longitude is None:
            await asyncio.sleep(0.5)
            continue
        dn = (t.latitude  - target_lat)  * (math.pi / 180.0) * EARTH_R
        de = (t.longitude - target_lon) * (math.pi / 180.0) * EARTH_R * math.cos(math.radians(t.latitude))
        dist = math.sqrt(dn**2 + de**2)
        if dist < 2.0:
            print(f"[{pkg}] Reached slot (dist={dist:.2f}m)")
            return True
        await asyncio.sleep(0.5)

    print(f"[{pkg}] GOTO timed out (still {dist:.1f}m away)")
    return False


async def rtl_and_disconnect(pkg, adapter):
    print(f"[{pkg}] RTL...")
    await adapter.rtl()
    deadline = time.time() + 90
    while time.time() < deadline:
        t = await adapter.get_telemetry()
        if t.armed_state == "DISARMED":
            print(f"[{pkg}] Landed and disarmed")
            break
        await asyncio.sleep(0.5)
    else:
        print(f"[{pkg}] RTL timed out - force disarm")
        await adapter.disarm()
    await adapter.disconnect()
    print(f"[{pkg}] Disconnected")


async def run_drone(pkg, vehicle, dx_north, dy_east, results):
    try:
        adapter = await connect_one(pkg, vehicle)

        ok = await arm_and_takeoff(pkg, adapter)
        if not ok:
            results[pkg] = "FAIL: arm/takeoff"
            await adapter.disconnect()
            return

        # Brief hover to let all drones get airborne
        await asyncio.sleep(3.0)

        ok = await goto_slot(pkg, adapter, dx_north, dy_east)
        results[pkg] = "PASS" if ok else "FAIL: goto"

        # Hold slot for 5 s so you can observe in Unreal
        await asyncio.sleep(5.0)

        await rtl_and_disconnect(pkg, adapter)

    except Exception as exc:
        results[pkg] = f"FAIL: exception: {exc}"
        raise


async def main():
    n = len(DRONES)
    offsets = compute_grid_offsets(n, SPACING_M)

    print(f"\nFormation: {FORMATION_TYPE}, spacing={SPACING_M}m, {n} drones")
    print(f"Vehicle mapping:")
    for i, ((pkg, vehicle), (dn, de)) in enumerate(zip(DRONES, offsets)):
        print(f"  [{i}] {pkg} -> {vehicle}  slot ({dn:+.1f}m N, {de:+.1f}m E)")
    print()

    results = {}
    tasks = [
        run_drone(pkg, vehicle, dn, de, results)
        for (pkg, vehicle), (dn, de) in zip(DRONES, offsets)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    print("\n--- Results ---")
    all_pass = True
    for pkg, _ in DRONES:
        r = results.get(pkg, "FAIL: did not run")
        status = "PASS" if r == "PASS" else "FAIL"
        if status != "PASS":
            all_pass = False
        print(f"  {pkg}: {r}")

    if all_pass:
        print("\nFormation smoke test PASSED")
        sys.exit(0)
    else:
        print("\nFormation smoke test FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
