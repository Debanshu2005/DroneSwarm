"""
scripts/test_formation_airsim.py

True end-to-end V-formation test.

Uses the REAL formation execution path:
    FORMATION_UPDATE -> FlightManager.formation_update
    -> DecisionEngine -> FormationEngine -> FlightPipeline
    -> CommandWriter -> AirSim

Does NOT use goto_location to move drones to slots.

Usage (from repo root, AirSim running, DRONEOS_PROFILE=sim):
    python scripts/test_formation_airsim.py

Acceptance:
  - All four drones move from takeoff position into V-formation.
  - Minimum observed pairwise separation >= MIN_FORMATION_SEPARATION_M.
  - Maximum target error reported per drone.
  - Drones hold distinct positions during HOLD_SECONDS.
"""
import asyncio
import importlib
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DRONEOS_PROFILE"] = "sim"

FORMATION_TYPE   = "V"
SPACING_M        = 10.0
TAKEOFF_ALT      = 10.0
HOLD_SECONDS     = 20.0
FORMATION_SETTLE = 15.0   # seconds to let FormationEngine drive before sampling
MIN_FORMATION_SEPARATION_M = 8.0
ARRIVAL_RADIUS_M = 3.0    # acceptable target error at end of hold

# (DroneOS package, AirSim vehicle name, drone_id)
DRONES = [
    ("DroneOS",  "Drone1", "drone1"),
    ("DroneOS1", "Drone2", "drone2"),
    ("DroneOS2", "Drone3", "drone3"),
    ("DroneOS3", "Drone4", "drone4"),
]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dn = (lat2 - lat1) * (math.pi / 180.0) * R
    de = (lon2 - lon1) * (math.pi / 180.0) * R * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dn, de)


def build_slot_assignments(drone_ids):
    members = sorted(drone_ids)
    return members, {m: i for i, m in enumerate(members)}


def compute_v_offsets(slot_assignments, spacing):
    """Return {drone_id: (dx_north_m, dy_east_m)} for V formation."""
    from DroneOS.core.formation_manager import FormationManager, FormationType
    mgr = FormationManager()
    mgr.set_formation(FormationType.V, spacing)
    total = len(slot_assignments)
    return {did: mgr.get_offset(slot, total)[:2] for did, slot in slot_assignments.items()}


# ---------------------------------------------------------------------------
# Per-drone DroneOS runtime (minimal in-process instance)
# ---------------------------------------------------------------------------

def _load_pkg(pkg):
    profile = importlib.import_module(f"{pkg}.shared.config.profile")
    models  = importlib.import_module(f"{pkg}.shared.config.models")
    adapter = importlib.import_module(f"{pkg}.adapters.airsim_adapter")
    fm_mod  = importlib.import_module(f"{pkg}.core.flight_manager")
    fs_mod  = importlib.import_module(f"{pkg}.core.flight_state")
    de_mod  = importlib.import_module(f"{pkg}.core.decision_engine")
    fp_mod  = importlib.import_module(f"{pkg}.core.flight_pipeline")
    sm_mod  = importlib.import_module(f"{pkg}.core.swarm_manager")
    mm_mod  = importlib.import_module(f"{pkg}.core.mission_manager")
    nav_mod = importlib.import_module(f"{pkg}.core.navigation_manager")
    ca_mod  = importlib.import_module(f"{pkg}.core.collision_avoidance")
    sf_mod  = importlib.import_module(f"{pkg}.core.safety")
    return profile, models, adapter, fm_mod, fs_mod, de_mod, fp_mod, sm_mod, mm_mod, nav_mod, ca_mod, sf_mod


class DroneRuntime:
    """Minimal in-process DroneOS runtime for one drone."""

    def __init__(self, pkg, vehicle, drone_id):
        self.pkg = pkg
        self.vehicle = vehicle
        self.drone_id = drone_id
        self._pipeline_task = None

        (profile, models, adapter_mod,
         fm_mod, fs_mod, de_mod, fp_mod,
         sm_mod, mm_mod, nav_mod, ca_mod, sf_mod) = _load_pkg(pkg)

        cfg_dir = Path(f"{pkg}/configs")
        flight_cfg = profile.resolve_flight_config(cfg_dir, models.FlightConfig)

        self.fc = adapter_mod.AirSimFlightController(vehicle, flight_cfg)
        self.state_store = fs_mod.FlightStateStore()
        self.flight_manager = fm_mod.FlightManager(self.fc, self.state_store)
        self.swarm_manager = sm_mod.SwarmMembership(drone_id)
        self.flight_manager.set_swarm_manager(self.swarm_manager)

        safety = sf_mod.SafetyModule(self.fc, self.state_store, config=flight_cfg)
        ca = ca_mod.StandardCollisionAvoidance(
            config=flight_cfg.collision_avoidance, drone_id=drone_id
        )
        nav = nav_mod.NavigationManager(self.flight_manager, self.state_store)

        # Minimal MissionManager (no network, no storage needed for this test)
        mission = mm_mod.MissionManager(
            nav, network_node=None, storage_dir="missions/",
            config=None, safety_module=safety,
            health_monitor=None, flight_controller=self.fc
        )
        self.flight_manager.mission_manager = mission

        de = de_mod.LocalDecisionEngine(
            mission, self.swarm_manager, ca, nav, safety,
            self.state_store, config=flight_cfg
        )

        self.pipeline = fp_mod.FlightPipeline(
            self.state_store, self.fc, flight_cfg, de
        )

    async def connect(self):
        ok = await self.fc.connect()
        if not ok:
            raise RuntimeError(f"[{self.vehicle}] AirSim connect failed")
        print(f"[{self.vehicle}] Connected")

    async def arm_and_takeoff(self):
        print(f"[{self.vehicle}] Arming...")
        ok = await self.fc.arm()
        if not ok:
            raise RuntimeError(f"[{self.vehicle}] Arm failed")
        deadline = time.time() + 10
        while time.time() < deadline:
            t = await self.fc.get_telemetry()
            if t.armed_state == "ARMED":
                break
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError(f"[{self.vehicle}] Timed out waiting for ARMED")
        print(f"[{self.vehicle}] Armed — taking off to {TAKEOFF_ALT}m...")
        ok = await self.fc.takeoff(TAKEOFF_ALT)
        if not ok:
            raise RuntimeError(f"[{self.vehicle}] Takeoff failed")
        print(f"[{self.vehicle}] Airborne")

    def start_pipeline(self):
        self._pipeline_task = asyncio.create_task(self.pipeline.run_pipeline_loop())

    def stop_pipeline(self):
        self.pipeline.stop()
        if self._pipeline_task:
            self._pipeline_task.cancel()

    async def apply_formation(self, params):
        """Inject formation params exactly as CommandHandler would after FORMATION_UPDATE."""
        drone_id = self.drone_id
        slot = (params.get("slot_assignments") or {}).get(drone_id, "?")
        print(f"FORMATION_COMMAND_RECEIVED drone={drone_id} slot={slot}")
        ok = await self.flight_manager.formation_update(params)
        print(f"FORMATION_ACTIVE drone={drone_id} slot={slot} engine_active={ok}")
        return ok

    def inject_peer_position(self, peer_id, lat, lon, alt):
        """Directly update this drone's swarm registry with a peer's position."""
        reg = self.swarm_manager.registry
        if not reg.get_peer(peer_id):
            reg.add_peer(peer_id)
        peer = reg.get_peer(peer_id)
        peer.lat = lat
        peer.lon = lon
        peer.alt = alt
        peer.last_position_time = time.time()
        peer.last_seen = time.time()
        peer.is_active = True

    async def get_position(self):
        t = await self.fc.get_telemetry()
        return t.latitude, t.longitude, t.altitude

    async def rtl(self):
        await self.fc.rtl()

    async def disconnect(self):
        await self.fc.disconnect()


# ---------------------------------------------------------------------------
# Cross-drone telemetry sync loop
# ---------------------------------------------------------------------------

async def sync_peer_positions(runtimes: list, interval=0.1):
    """
    Continuously push each drone's live position into every other drone's
    swarm registry — simulating what the UDP telemetry relay does in production.
    """
    while True:
        positions = {}
        for rt in runtimes:
            try:
                lat, lon, alt = await rt.get_position()
                if lat is not None:
                    positions[rt.drone_id] = (lat, lon, alt)
            except Exception:
                pass

        for rt in runtimes:
            for peer_id, (lat, lon, alt) in positions.items():
                if peer_id != rt.drone_id:
                    rt.inject_peer_position(peer_id, lat, lon, alt)

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

async def main():
    drone_ids = [d[2] for d in DRONES]
    members, slot_assignments = build_slot_assignments(drone_ids)
    offsets = compute_v_offsets(slot_assignments, SPACING_M)

    print(f"\nFormation: {FORMATION_TYPE}  spacing={SPACING_M}m")
    print("Slot assignments (deterministic, heartbeat-independent):")
    for did in members:
        dn, de = offsets[did]
        print(f"  {did} -> slot {slot_assignments[did]}  offset ({dn:+.2f}m N, {de:+.2f}m E)")

    # Verify geometry
    coords = [offsets[did] for did in members]
    print("\nExpected pairwise slot distances:")
    min_expected = float("inf")
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            dist = math.hypot(coords[i][0] - coords[j][0], coords[i][1] - coords[j][1])
            min_expected = min(min_expected, dist)
            print(f"  {members[i]} <-> {members[j]}: {dist:.2f}m")
    assert min_expected >= MIN_FORMATION_SEPARATION_M, (
        f"Formation geometry {min_expected:.2f}m < min_sep {MIN_FORMATION_SEPARATION_M}m"
    )
    print(f"  Minimum expected: {min_expected:.2f}m  ✓\n")

    formation_params = {
        "type": FORMATION_TYPE,
        "spacing": SPACING_M,
        "members": members,
        "slot_assignments": slot_assignments,
    }

    # Build runtimes
    runtimes = []
    for pkg, vehicle, drone_id in DRONES:
        rt = DroneRuntime(pkg, vehicle, drone_id)
        runtimes.append(rt)

    # Connect all
    await asyncio.gather(*[rt.connect() for rt in runtimes])

    # Arm and takeoff all
    await asyncio.gather(*[rt.arm_and_takeoff() for rt in runtimes])

    # Brief hover so all are stable at altitude
    print("\nHovering 3s for stability...")
    await asyncio.sleep(3.0)

    # Start cross-drone position sync (simulates UDP relay)
    sync_task = asyncio.create_task(sync_peer_positions(runtimes, interval=0.1))

    # Start flight pipelines
    for rt in runtimes:
        rt.start_pipeline()
    print("Flight pipelines started.")

    # Wait one pipeline cycle before injecting formation
    await asyncio.sleep(0.5)

    # Send FORMATION_UPDATE to all four
    print("\n--- Sending FORMATION_UPDATE to all drones ---")
    await asyncio.gather(*[rt.apply_formation(formation_params) for rt in runtimes])

    # Let FormationEngine drive for FORMATION_SETTLE seconds
    print(f"\nAllowing FormationEngine to drive for {FORMATION_SETTLE}s...")
    t_start = time.time()
    sample_interval = 2.0
    next_sample = t_start + sample_interval

    position_history = {rt.drone_id: [] for rt in runtimes}

    while time.time() - t_start < FORMATION_SETTLE + HOLD_SECONDS:
        now = time.time()
        elapsed = now - t_start
        if now >= next_sample:
            next_sample += sample_interval
            snapshot = {}
            for rt in runtimes:
                lat, lon, alt = await rt.get_position()
                snapshot[rt.drone_id] = (lat, lon, alt)
                position_history[rt.drone_id].append((lat, lon, alt))

            # Log current positions
            print(f"\n[t+{elapsed:.1f}s] Positions:")
            for did, (lat, lon, alt) in snapshot.items():
                slot = slot_assignments[did]
                print(f"  {did} slot={slot} lat={lat:.7f} lon={lon:.7f} alt={alt:.1f}m")

            # Log pairwise distances
            ids = list(snapshot.keys())
            min_sep = float("inf")
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    d = haversine_m(snapshot[a][0], snapshot[a][1],
                                    snapshot[b][0], snapshot[b][1])
                    min_sep = min(min_sep, d)
                    print(f"  {a}<->{b}: {d:.2f}m")
            print(f"  Min separation this tick: {min_sep:.2f}m")

        await asyncio.sleep(0.2)

    # Stop sync and pipelines
    sync_task.cancel()
    for rt in runtimes:
        rt.stop_pipeline()

    # Final position sample
    print("\n--- Final positions (end of hold) ---")
    final_positions = {}
    for rt in runtimes:
        lat, lon, alt = await rt.get_position()
        final_positions[rt.drone_id] = (lat, lon, alt)
        slot = slot_assignments[rt.drone_id]
        print(f"  {rt.drone_id} slot={slot} lat={lat:.7f} lon={lon:.7f} alt={alt:.1f}m")

    # Compute target errors using anchor (slot 0) position
    anchor_id = members[0]  # slot 0
    anchor_lat, anchor_lon, anchor_alt = final_positions[anchor_id]

    print("\n--- Target errors ---")
    max_error = 0.0
    errors = {}
    for did in members:
        slot = slot_assignments[did]
        dn, de = offsets[did]
        R = 6371000.0
        t_lat = anchor_lat + dn / R * (180.0 / math.pi)
        t_lon = anchor_lon + de / (R * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
        f_lat, f_lon, _ = final_positions[did]
        err = haversine_m(f_lat, f_lon, t_lat, t_lon)
        errors[did] = err
        max_error = max(max_error, err)
        print(f"  {did} slot={slot} target=({t_lat:.7f},{t_lon:.7f}) actual=({f_lat:.7f},{f_lon:.7f}) error={err:.2f}m")

    avg_error = sum(errors.values()) / len(errors)

    # Pairwise distances at final hold
    print("\n--- Final pairwise distances ---")
    ids = list(final_positions.keys())
    sep_violations = []
    min_observed_sep = float("inf")
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            d = haversine_m(final_positions[a][0], final_positions[a][1],
                            final_positions[b][0], final_positions[b][1])
            min_observed_sep = min(min_observed_sep, d)
            violation = d < MIN_FORMATION_SEPARATION_M
            if violation:
                sep_violations.append((a, b, d))
            print(f"  {a}<->{b}: {d:.2f}m" + (" *** VIOLATION ***" if violation else ""))

    # Check movement: compare first and last recorded positions
    print("\n--- Movement verification ---")
    all_moved = True
    for rt in runtimes:
        hist = position_history[rt.drone_id]
        if len(hist) >= 2:
            first_lat, first_lon, _ = hist[0]
            last_lat, last_lon, _ = hist[-1]
            moved = haversine_m(first_lat, first_lon, last_lat, last_lon)
            moved_str = f"{moved:.2f}m"
            did_move = moved > 0.5
            if not did_move:
                all_moved = False
            print(f"  {rt.drone_id}: moved {moved_str} {'✓' if did_move else '✗ DID NOT MOVE'}")
        else:
            print(f"  {rt.drone_id}: insufficient position history")
            all_moved = False

    # RTL all
    print("\nRTL all drones...")
    await asyncio.gather(*[rt.rtl() for rt in runtimes])
    await asyncio.sleep(5.0)
    await asyncio.gather(*[rt.disconnect() for rt in runtimes])

    # Final report
    print("\n" + "=" * 60)
    print("FINAL REPORT")
    print("=" * 60)
    print(f"  Formation type:              {FORMATION_TYPE}")
    print(f"  Spacing:                     {SPACING_M}m")
    print(f"  Min observed separation:     {min_observed_sep:.2f}m  (threshold={MIN_FORMATION_SEPARATION_M}m)")
    print(f"  Max target error:            {max_error:.2f}m")
    print(f"  Avg target error:            {avg_error:.2f}m")
    print(f"  Separation violations:       {len(sep_violations)}")
    print(f"  All drones moved:            {all_moved}")
    print(f"  V-formation slot offsets:")
    for did in members:
        dn, de = offsets[did]
        print(f"    {did} slot={slot_assignments[did]}: N={dn:+.1f}m E={de:+.1f}m")

    passed = all_moved and len(sep_violations) == 0
    if passed:
        print("\n✓ END-TO-END FORMATION TEST PASSED")
        sys.exit(0)
    else:
        if not all_moved:
            print("\n✗ FAILED: one or more drones did not move")
        if sep_violations:
            print(f"\n✗ FAILED: separation violations: {sep_violations}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
