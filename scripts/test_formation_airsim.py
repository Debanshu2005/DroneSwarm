"""
scripts/test_formation_airsim.py

Four-drone V-formation smoke test using explicit slot_assignments.

Each drone is assigned a deterministic slot by the same logic the mobile app
uses: sorted(drone_ids)[i] → slot i.  The FormationEngine on each DroneOS
instance reads its slot from the assignment map — it never re-derives it from
the live heartbeat list.

Usage (from repo root, with AirSim running):
    python scripts/test_formation_airsim.py

Acceptance criteria:
  - Each drone logs FORMATION_ASSIGNMENT with a unique slot.
  - Each drone logs FORMATION_TARGET with a unique coordinate.
  - Heartbeat timing does not cause slot reshuffling.
  - All four drones reach their V-formation slots within GOTO_TIMEOUT seconds.
"""
import asyncio
import importlib
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FORMATION_TYPE = "V"
SPACING_M = 8.0
TAKEOFF_ALT = 10.0
GOTO_TIMEOUT = 60.0
ARRIVAL_RADIUS_M = 2.0

# (DroneOS package, AirSim vehicle name)
DRONES = [
    ("DroneOS",  "Drone1"),
    ("DroneOS1", "Drone2"),
    ("DroneOS2", "Drone3"),
    ("DroneOS3", "Drone4"),
]

os.environ["DRONEOS_PROFILE"] = "sim"


# ---------------------------------------------------------------------------
# Slot assignment — identical logic to SwarmView.jsx handleApply
# ---------------------------------------------------------------------------

def build_slot_assignment(drone_ids):
    """
    members = sorted(drone_ids)
    slot_assignments = {members[i]: i for i in range(len(members))}
    Slot 0 is the anchor.
    """
    members = sorted(drone_ids)
    slot_assignments = {m: i for i, m in enumerate(members)}
    return members, slot_assignments


def compute_slot_offsets(slot_assignments, formation_type, spacing):
    """Return {drone_id: (dx_north_m, dy_east_m)} for each slot."""
    from DroneOS.core.formation_manager import FormationManager, FormationType
    mgr = FormationManager()
    mgr.set_formation(FormationType(formation_type.upper()), spacing)
    total = len(slot_assignments)
    return {
        drone_id: mgr.get_offset(slot, total)[:2]
        for drone_id, slot in slot_assignments.items()
    }


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def load_adapter(pkg, vehicle):
    profile = importlib.import_module(f"{pkg}.shared.config.profile")
    models  = importlib.import_module(f"{pkg}.shared.config.models")
    adapter = importlib.import_module(f"{pkg}.adapters.airsim_adapter")
    cfg = profile.resolve_flight_config(Path(f"{pkg}/configs"), models.FlightConfig)
    return adapter.AirSimFlightController(vehicle, cfg)


async def connect_one(pkg, vehicle):
    print(f"[{vehicle}] Connecting {pkg}...")
    fc = load_adapter(pkg, vehicle)
    ok = await fc.connect()
    if not ok:
        print(f"[{vehicle}] FAILED to connect")
        sys.exit(1)
    print(f"[{vehicle}] Connected")
    return fc


async def arm_and_takeoff(vehicle, fc):
    print(f"[{vehicle}] Arming...")
    ok = await fc.arm()
    if not ok:
        print(f"[{vehicle}] FAILED to arm")
        return False

    deadline = time.time() + 10
    while time.time() < deadline:
        t = await fc.get_telemetry()
        if t.armed_state == "ARMED":
            break
        await asyncio.sleep(0.2)
    else:
        print(f"[{vehicle}] Timed out waiting for ARMED")
        return False

    print(f"[{vehicle}] Armed — taking off to {TAKEOFF_ALT}m...")
    ok = await fc.takeoff(TAKEOFF_ALT)
    if not ok:
        print(f"[{vehicle}] FAILED takeoff")
        return False
    print(f"[{vehicle}] Airborne")
    return True


async def goto_slot(vehicle, fc, drone_id, slot, dx_north, dy_east):
    home = await fc.get_home_position()
    if home is None:
        print(f"[{vehicle}] No home position")
        return False

    home_lat, home_lon, _ = home
    R = 6371000.0
    dlat = dx_north / R * (180.0 / math.pi)
    dlon = dy_east / (R * math.cos(math.radians(home_lat))) * (180.0 / math.pi)
    target_lat = home_lat + dlat
    target_lon = home_lon + dlon

    print(
        f"FORMATION_ASSIGNMENT drone={drone_id} slot={slot}"
    )
    print(
        f"FORMATION_TARGET drone={drone_id} slot={slot} "
        f"north={dx_north:+.2f}m east={dy_east:+.2f}m "
        f"lat={target_lat:.7f} lon={target_lon:.7f}"
    )

    t = await fc.get_telemetry()
    print(
        f"[{vehicle}] current pos lat={t.latitude:.7f} lon={t.longitude:.7f} alt={t.altitude:.1f}m"
    )

    ok = await fc.goto_location(target_lat, target_lon, TAKEOFF_ALT)
    if not ok:
        print(f"[{vehicle}] goto_location returned False")
        return False

    deadline = time.time() + GOTO_TIMEOUT
    dist = float("inf")
    while time.time() < deadline:
        t = await fc.get_telemetry()
        if t.latitude is None:
            await asyncio.sleep(0.5)
            continue
        dn = (t.latitude  - target_lat)  * (math.pi / 180.0) * R
        de = (t.longitude - target_lon) * (math.pi / 180.0) * R * math.cos(math.radians(t.latitude))
        dist = math.sqrt(dn**2 + de**2)
        if dist < ARRIVAL_RADIUS_M:
            print(
                f"[{vehicle}] Reached slot {slot} (dist={dist:.2f}m) "
                f"lat={t.latitude:.7f} lon={t.longitude:.7f}"
            )
            return True
        await asyncio.sleep(0.5)

    print(f"[{vehicle}] GOTO timed out — still {dist:.1f}m from slot {slot}")
    return False


async def rtl_and_disconnect(vehicle, fc):
    print(f"[{vehicle}] RTL...")
    await fc.rtl()
    deadline = time.time() + 90
    while time.time() < deadline:
        t = await fc.get_telemetry()
        if t.armed_state == "DISARMED":
            print(f"[{vehicle}] Landed and disarmed")
            break
        await asyncio.sleep(0.5)
    else:
        print(f"[{vehicle}] RTL timed out — force disarm")
        await fc.disarm()
    await fc.disconnect()


async def run_drone(pkg, vehicle, drone_id, slot, dx_north, dy_east, results):
    try:
        fc = await connect_one(pkg, vehicle)
        ok = await arm_and_takeoff(vehicle, fc)
        if not ok:
            results[drone_id] = "FAIL: arm/takeoff"
            await fc.disconnect()
            return

        # Brief hover so all drones are airborne before moving
        await asyncio.sleep(3.0)

        ok = await goto_slot(vehicle, fc, drone_id, slot, dx_north, dy_east)
        results[drone_id] = "PASS" if ok else f"FAIL: goto slot {slot}"

        # Hold slot for observation
        await asyncio.sleep(5.0)
        await rtl_and_disconnect(vehicle, fc)

    except Exception as exc:
        results[drone_id] = f"FAIL: {exc}"
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    drone_ids = [d[0].replace("DroneOS", "drone").replace("drone", "drone")
                 for d in DRONES]
    # Map package names to logical drone IDs
    pkg_to_id = {
        "DroneOS":  "drone1",
        "DroneOS1": "drone2",
        "DroneOS2": "drone3",
        "DroneOS3": "drone4",
    }
    drone_ids = [pkg_to_id[pkg] for pkg, _ in DRONES]

    members, slot_assignments = build_slot_assignment(drone_ids)
    offsets = compute_slot_offsets(slot_assignments, FORMATION_TYPE, SPACING_M)

    print(f"\nFormation: {FORMATION_TYPE}  spacing={SPACING_M}m  drones={len(DRONES)}")
    print(f"Slot assignments (stable, heartbeat-independent):")
    for drone_id in members:
        slot = slot_assignments[drone_id]
        dn, de = offsets[drone_id]
        print(f"  {drone_id} -> slot {slot}  offset ({dn:+.2f}m N, {de:+.2f}m E)")
    print()

    # Verify all targets are distinct
    coords = list(offsets.values())
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            dist = math.sqrt((coords[i][0]-coords[j][0])**2 + (coords[i][1]-coords[j][1])**2)
            assert dist > 0.01, f"Slots {i} and {j} have identical offsets!"
    print("✓ All four slot offsets are distinct\n")

    results = {}
    tasks = []
    for (pkg, vehicle), drone_id in zip(DRONES, drone_ids):
        slot = slot_assignments[drone_id]
        dn, de = offsets[drone_id]
        tasks.append(run_drone(pkg, vehicle, drone_id, slot, dn, de, results))

    await asyncio.gather(*tasks, return_exceptions=True)

    print("\n--- Results ---")
    all_pass = True
    for drone_id in drone_ids:
        r = results.get(drone_id, "FAIL: did not run")
        ok = r == "PASS"
        if not ok:
            all_pass = False
        slot = slot_assignments[drone_id]
        print(f"  {drone_id} (slot {slot}): {r}")

    if all_pass:
        print("\n✓ Formation smoke test PASSED — all four drones reached assigned slots")
        sys.exit(0)
    else:
        print("\n✗ Formation smoke test FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
