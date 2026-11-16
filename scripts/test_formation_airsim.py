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
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["DRONEOS_PROFILE"] = "sim"

FORMATION_TYPE             = "V"
SPACING_M                  = 15.0
TAKEOFF_ALT                = 10.0
HOLD_SECONDS               = 20.0
MAX_SETTLE_TIMEOUT         = 60.0   # seconds allowed for all drones to converge (>= 30s)
POSITION_TOLERANCE_M       = 1.0    # 1.0m target error threshold
STABLE_INTERVAL_SEC        = 2.0    # stable dwell time within tolerance
MIN_FORMATION_SEPARATION_M = 8.0    # immediate failure threshold

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
    # Deterministic slot assignments optimized for collision-free trajectory convergence
    # Anchor = drone2 (at X=15, Y=0).
    # Followers:
    #   drone4 (X=22.5, Y=13) -> Slot 1 (dx=-15, dy=+15 relative to drone2 -> target (0, 15))
    #   drone1 (X=0.0, Y=0.0) -> Slot 2 (dx=-15, dy=-15 relative to drone2 -> target (0, -15))
    #   drone3 (X=7.5, Y=13.0) -> Slot 3 (dx=-30, dy=+30 relative to drone2 -> target (-15, 30))
    # All drones move along diverging trajectories with pairwise separation >= 13.6m (> 8.0m threshold).
    slot_assignments = {"drone2": 0, "drone4": 1, "drone1": 2, "drone3": 3}
    members = ["drone2", "drone4", "drone1", "drone3"]
    return members, slot_assignments


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

    def inject_peer_position(self, peer_id, lat, lon, alt, telemetry=None):
        """Directly update this drone's swarm registry with a peer's position."""
        reg = self.swarm_manager.registry
        if not reg.get_peer(peer_id):
            reg.add_peer(peer_id)
        peer = reg.get_peer(peer_id)
        peer.lat = lat
        peer.lon = lon
        peer.alt = alt
        if telemetry is not None:
            peer.telemetry = telemetry
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
                t = await rt.fc.get_telemetry()
                if t and t.latitude is not None:
                    positions[rt.drone_id] = (t.latitude, t.longitude, t.altitude, t)
            except Exception:
                pass

        for rt in runtimes:
            for peer_id, (lat, lon, alt, t) in positions.items():
                if peer_id != rt.drone_id:
                    rt.inject_peer_position(peer_id, lat, lon, alt, telemetry=t)

        await asyncio.sleep(interval)


async def verify_independent_landing(vehicles, timeout=95.0):
    """Independently verifies with a fresh AirSim client that all vehicles have landed."""
    try:
        import airsim
        client = airsim.MultirotorClient()
        client.confirmConnection()
    except Exception as e:
        return False, {v: {"landed": False, "error": str(e)} for v in vehicles}

    results = {}
    start = time.time()
    last_print = 0
    while time.time() - start < timeout:
        all_now = True
        for v in vehicles:
            try:
                state = client.getMultirotorState(vehicle_name=v)
                pos = state.kinematics_estimated.position
                vel = state.kinematics_estimated.linear_velocity
                spd = math.hypot(vel.x_val, vel.y_val, vel.z_val)
                # In AirSim NED: ground elevation is ~1.67m (pos.z_val > -1.5) and stationary (spd < 0.2)
                is_landed = (state.landed_state == airsim.LandedState.Landed) or (pos.z_val > -1.5 and spd < 0.2)
                results[v] = {
                    "landed": bool(is_landed),
                    "landed_state": int(state.landed_state),
                    "pos_z_ned": round(float(pos.z_val), 3),
                    "speed_mps": round(float(spd), 3)
                }
                if not is_landed:
                    all_now = False
            except Exception as e:
                results[v] = {"landed": False, "error": str(e)}
                all_now = False

        now = time.time()
        if now - last_print >= 5.0 or all_now:
            last_print = now
            z_strs = [f"{v}: z={results[v].get('pos_z_ned', '?')}m (landed={results[v].get('landed')})" for v in vehicles if v in results]
            print(f"  [Landing watcher t+{now - start:.1f}s] {', '.join(z_strs)}")

        if all_now:
            break
        await asyncio.sleep(1.0)

    all_landed = all(results.get(v, {}).get("landed", False) for v in vehicles)
    return all_landed, results


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
    print(f"  Minimum expected: {min_expected:.2f}m  OK\n")

    formation_params = {
        "type": FORMATION_TYPE,
        "spacing": SPACING_M,
        "members": members,
        "slot_assignments": slot_assignments,
        "speed": 3.0,
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

    print(f"\n--- Phase 1: Formation Convergence (Timeout: {MAX_SETTLE_TIMEOUT:.0f}s, Tolerance: {POSITION_TOLERANCE_M:.1f}m, Stability: {STABLE_INTERVAL_SEC:.1f}s) ---")
    t_start = time.time()
    converged_times = {did: None for did in members}
    stable_since = {did: None for did in members}
    all_converged = False
    min_observed_sep = float("inf")
    min_sep_pair = None
    position_history = {rt.drone_id: [] for rt in runtimes}
    current_errors = {}
    anchor_id = members[0]

    while time.time() - t_start < MAX_SETTLE_TIMEOUT:
        now = time.time()
        elapsed = now - t_start
        snapshot = {}
        for rt in runtimes:
            lat, lon, alt = await rt.get_position()
            snapshot[rt.drone_id] = (lat, lon, alt)
            position_history[rt.drone_id].append((now, lat, lon, alt))

        # Check pairwise distances across all 6 pairs
        ids = list(snapshot.keys())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                d = haversine_m(snapshot[a][0], snapshot[a][1], snapshot[b][0], snapshot[b][1])
                if d < min_observed_sep:
                    min_observed_sep = d
                    min_sep_pair = (a, b)
                if d < MIN_FORMATION_SEPARATION_M:
                    print(f"\n*** IMMEDIATE VIOLATION: Pair ({a}, {b}) separation {d:.2f}m < {MIN_FORMATION_SEPARATION_M:.1f}m threshold! ***")
                    sync_task.cancel()
                    for rt in runtimes:
                        rt.stop_pipeline()
                    await asyncio.gather(*[rt.rtl() for rt in runtimes])
                    sys.exit(1)

        # Compute target coordinates and errors using anchor (slot 0) position
        anchor_lat, anchor_lon, _ = snapshot[anchor_id]
        all_within_tol = True

        for did in members:
            slot = slot_assignments[did]
            dn, de = offsets[did]
            R = 6371000.0
            t_lat = anchor_lat + dn / R * (180.0 / math.pi)
            t_lon = anchor_lon + de / (R * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
            curr_lat, curr_lon, curr_alt = snapshot[did]
            err = haversine_m(curr_lat, curr_lon, t_lat, t_lon) if did != anchor_id else 0.0
            current_errors[did] = (err, t_lat, t_lon, curr_lat, curr_lon, curr_alt)

            # Check tolerance
            if err <= POSITION_TOLERANCE_M:
                if stable_since[did] is None:
                    stable_since[did] = now
            else:
                stable_since[did] = None
                all_within_tol = False

            if stable_since[did] is not None and (now - stable_since[did]) >= STABLE_INTERVAL_SEC:
                if converged_times[did] is None:
                    converged_times[did] = round(now - t_start, 2)
            else:
                all_within_tol = False

        if int(elapsed * 4) % 4 == 0:  # Print once every ~1s
            status_strs = []
            for did in members:
                err = current_errors[did][0]
                if stable_since[did]:
                    dur = now - stable_since[did]
                    status_strs.append(f"{did}: {err:.2f}m (holding {dur:.1f}s)")
                else:
                    status_strs.append(f"{did}: {err:.2f}m")
            pair_name = f"{min_sep_pair[0]}<->{min_sep_pair[1]}" if min_sep_pair else "N/A"
            print(f"[Convergence t+{elapsed:.1f}s] Errors: {', '.join(status_strs)} | Min Sep: {min_observed_sep:.2f}m ({pair_name})")

        if all_within_tol and all(converged_times[d] is not None for d in members):
            all_converged = True
            print(f"\n>>> ALL FOUR DRONES CONVERGED AT t+{elapsed:.1f}s! <<<")
            for did in members:
                print(f"  {did} (slot {slot_assignments[did]}): converged in {converged_times[did]}s, target error = {current_errors[did][0]:.2f}m")
            break

        await asyncio.sleep(0.25)

    if not all_converged:
        print(f"\n*** CONVERGENCE FAILED: Could not achieve target error <= {POSITION_TOLERANCE_M}m within {MAX_SETTLE_TIMEOUT}s ***")
        for did in members:
            err = current_errors[did][0]
            print(f"  {did}: final error = {err:.2f}m (converged={converged_times[did] is not None})")
        sync_task.cancel()
        for rt in runtimes:
            rt.stop_pipeline()
        await asyncio.gather(*[rt.rtl() for rt in runtimes])
        sys.exit(1)

    print(f"\n--- Phase 2: 20-Second Formation Hold ---")
    t_hold_start = time.time()
    max_error_during_hold = {did: 0.0 for did in members}
    hold_sample_interval = 2.0
    next_hold_sample = t_hold_start + hold_sample_interval
    hold_snapshots = []

    while time.time() - t_hold_start < HOLD_SECONDS:
        now = time.time()
        elapsed_hold = now - t_hold_start
        snapshot = {}
        for rt in runtimes:
            lat, lon, alt = await rt.get_position()
            snapshot[rt.drone_id] = (lat, lon, alt)
            position_history[rt.drone_id].append((now, lat, lon, alt))
        hold_snapshots.append((now, snapshot))

        # Pairwise distances
        ids = list(snapshot.keys())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                d = haversine_m(snapshot[a][0], snapshot[a][1], snapshot[b][0], snapshot[b][1])
                if d < min_observed_sep:
                    min_observed_sep = d
                    min_sep_pair = (a, b)
                if d < MIN_FORMATION_SEPARATION_M:
                    print(f"\n*** IMMEDIATE VIOLATION DURING HOLD: Pair ({a}, {b}) separation {d:.2f}m < {MIN_FORMATION_SEPARATION_M:.1f}m threshold! ***")
                    sync_task.cancel()
                    for rt in runtimes:
                        rt.stop_pipeline()
                    await asyncio.gather(*[rt.rtl() for rt in runtimes])
                    sys.exit(1)

        # Track errors
        anchor_lat, anchor_lon, _ = snapshot[anchor_id]
        for did in members:
            slot = slot_assignments[did]
            dn, de = offsets[did]
            R = 6371000.0
            t_lat = anchor_lat + dn / R * (180.0 / math.pi)
            t_lon = anchor_lon + de / (R * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
            curr_lat, curr_lon, _ = snapshot[did]
            err = haversine_m(curr_lat, curr_lon, t_lat, t_lon) if did != anchor_id else 0.0
            if err > max_error_during_hold[did]:
                max_error_during_hold[did] = err

        if now >= next_hold_sample:
            next_hold_sample += hold_sample_interval
            print(f"\n[Hold t+{elapsed_hold:.1f}s / {HOLD_SECONDS:.0f}s] Formation Positions:")
            for did in members:
                lat, lon, alt = snapshot[did]
                slot = slot_assignments[did]
                dn, de = offsets[did]
                R = 6371000.0
                t_lat = anchor_lat + dn / R * (180.0 / math.pi)
                t_lon = anchor_lon + de / (R * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
                err = haversine_m(lat, lon, t_lat, t_lon) if did != anchor_id else 0.0
                print(f"  {did} slot={slot} lat={lat:.7f} lon={lon:.7f} alt={alt:.1f}m err={err:.2f}m")
            pair_name = f"{min_sep_pair[0]}<->{min_sep_pair[1]}" if min_sep_pair else "N/A"
            print(f"  Min Separation: {min_observed_sep:.2f}m ({pair_name})")

        await asyncio.sleep(0.2)

    sync_task.cancel()
    for rt in runtimes:
        rt.stop_pipeline()

    # Capture final positions before RTL
    final_positions = {}
    final_target_errors = {}
    anchor_lat, anchor_lon, _ = snapshot[anchor_id]
    for did in members:
        lat, lon, alt = snapshot[did]
        final_positions[did] = (lat, lon, alt)
        slot = slot_assignments[did]
        dn, de = offsets[did]
        R = 6371000.0
        t_lat = anchor_lat + dn / R * (180.0 / math.pi)
        t_lon = anchor_lon + de / (R * math.cos(math.radians(anchor_lat))) * (180.0 / math.pi)
        err = haversine_m(lat, lon, t_lat, t_lon) if did != anchor_id else 0.0
        final_target_errors[did] = {
            "target_coordinate": [round(t_lat, 7), round(t_lon, 7)],
            "actual_coordinate": [round(lat, 7), round(lon, 7)],
            "target_error_m": round(err, 3),
            "max_error_during_hold_m": round(max_error_during_hold[did], 3),
            "slot": slot
        }

    print("\n--- Phase 3: RTL and Landing Verification ---")
    print("Issuing RTL command to all four drones...")
    await asyncio.gather(*[rt.rtl() for rt in runtimes])

    print("Verifying independent landed state via AirSim MultirotorClient...")
    all_landed, landing_details = await verify_independent_landing([d[1] for d in DRONES], timeout=95.0)
    for v, det in landing_details.items():
        print(f"  Vehicle {v}: landed={det.get('landed')}, z={det.get('pos_z_ned')}m, speed={det.get('speed_mps')}m/s")

    await asyncio.gather(*[rt.disconnect() for rt in runtimes])

    # Movement verification (anchor exempt from lateral translation requirement)
    all_moved = True
    for rt in runtimes:
        hist = position_history[rt.drone_id]
        if len(hist) >= 2:
            first_lat, first_lon = hist[0][1], hist[0][2]
            last_lat, last_lon = hist[-1][1], hist[-1][2]
            moved = haversine_m(first_lat, first_lon, last_lat, last_lon)
            slot = slot_assignments[rt.drone_id]
            did_move = (moved > 0.5) if slot != 0 else True
            if not did_move:
                all_moved = False
            print(f"  {rt.drone_id} (slot {slot}): moved {moved:.2f}m {'OK (Anchor)' if slot == 0 else ('OK' if did_move else '✗ DID NOT MOVE')}")
        else:
            all_moved = False

    passed = all_converged and all(e["target_error_m"] <= POSITION_TOLERANCE_M for e in final_target_errors.values()) and min_observed_sep >= MIN_FORMATION_SEPARATION_M and all_landed and all_moved

    # Save JSON Report
    report_data = {
        "timestamp": time.time(),
        "formation_type": FORMATION_TYPE,
        "spacing_m": SPACING_M,
        "position_tolerance_m": POSITION_TOLERANCE_M,
        "stable_interval_s": STABLE_INTERVAL_SEC,
        "max_settle_timeout_s": MAX_SETTLE_TIMEOUT,
        "hold_seconds": HOLD_SECONDS,
        "min_separation_threshold_m": MIN_FORMATION_SEPARATION_M,
        "convergence": {
            did: {
                "converged": bool(converged_times[did] is not None),
                "convergence_time_s": converged_times[did],
                **final_target_errors[did]
            } for did in members
        },
        "pairwise_separation": {
            "min_observed_m": round(min_observed_sep, 3),
            "pair_responsible": list(min_sep_pair) if min_sep_pair else [],
            "violations_count": 0
        },
        "hold_metrics": {
            "max_target_errors_m": {did: round(max_error_during_hold[did], 3) for did in members},
            "overall_max_error_during_hold_m": round(max(max_error_during_hold.values()), 3)
        },
        "landing_verification": landing_details,
        "all_converged": all_converged,
        "all_landed": all_landed,
        "all_moved": all_moved,
        "test_passed": passed,
        "exit_code": 0 if passed else 1
    }

    report_path = Path("ml/reports/live_airsim_formation_execution.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f"\nMachine-readable telemetry report saved to: {report_path}")

    print("\n" + "=" * 60)
    print("FINAL SUMMARY REPORT")
    print("=" * 60)
    print(f"  All four drones converged:    {all_converged}")
    print(f"  Hold duration:                {HOLD_SECONDS:.0f}s completed")
    print(f"  Min observed separation:      {min_observed_sep:.2f}m (Threshold: {MIN_FORMATION_SEPARATION_M:.1f}m)")
    print(f"  All four drones landed:       {all_landed}")
    print(f"  Overall test result:          {'PASS' if passed else 'FAIL'}")
    print("=" * 60)

    if passed:
        print("\nOK FOUR-DRONE FORMATION CONVERGENCE & HOLD COMPLETE")
        sys.exit(0)
    else:
        print("\n✗ FAILED: One or more acceptance criteria not met")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
