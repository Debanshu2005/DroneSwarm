import math
import os
import random
import time
from collections import Counter

import pytest

from DroneOS.core.coordination.healing import plan_healing
from DroneOS.core.coordination.manager import CoordinationManager
from DroneOS.core.formation_engine import FormationEngine
from DroneOS.core.formation_manager import convert_local_offset_to_global, global_offset_local_m
from DroneOS.shared.config.models import FlightConfig
from DroneOS.shared.config.profile import resolve_flight_config


FORMATIONS = ["V", "LINE", "SQUARE", "COLUMN", "ECHELON_LEFT",
              "ECHELON_RIGHT", "DIAMOND", "GRID", "CIRCLE"]
_ORACLE_SEED = 0xA11CE
    

def _pt_to_segment(p, a, b):
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    l2 = dx*dx + dy*dy
    if l2 == 0:
        return math.hypot(p[0]-a[0], p[1]-a[1])
    t = max(0, min(1, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / l2))
    return math.hypot(p[0] - (a[0] + t*dx), p[1] - (a[1] + t*dy))

def _ccw(A, B, C):
    return (C[1]-A[1]) * (B[0]-A[0]) > (B[1]-A[1]) * (C[0]-A[0])

def _intersect(A, B, C, D):
    return _ccw(A,C,D) != _ccw(B,C,D) and _ccw(A,B,C) != _ccw(A,B,D)

def analytic_segment_distance(A, B, C, D):
    if _intersect(A, B, C, D):
        return 0.0
    return min(
        _pt_to_segment(A, C, D), _pt_to_segment(B, C, D),
        _pt_to_segment(C, A, B), _pt_to_segment(D, A, B)
    )

def test_analytic_segment_distance_correctness():
    rng = random.Random(12345)
    for _ in range(500):
        A = (rng.uniform(-50, 50), rng.uniform(-50, 50))
        B = (rng.uniform(-50, 50), rng.uniform(-50, 50))
        C = (rng.uniform(-50, 50), rng.uniform(-50, 50))
        D = (rng.uniform(-50, 50), rng.uniform(-50, 50))
        
        # Dense sampling
        min_d = float('inf')
        steps = 100
        for i in range(steps + 1):
            t1 = i / steps
            p1 = (A[0] + t1*(B[0]-A[0]), A[1] + t1*(B[1]-A[1]))
            for j in range(steps + 1):
                t2 = j / steps
                p2 = (C[0] + t2*(D[0]-C[0]), C[1] + t2*(D[1]-C[1]))
                d = math.hypot(p1[0]-p2[0], p1[1]-p2[1])
                if d < min_d: min_d = d
                
        analytic = analytic_segment_distance(A, B, C, D)
        assert abs(analytic - min_d) < 1.0  # Dense sampling has error bound, 1m is safe for 100 steps on 100m grid


class MockSwarm:
    def __init__(self):
        self.identity = type("Identity", (), {"drone_id": "d0"})()
        self.registry = type("Registry", (), {
            "get_all_peers": lambda self: [],
            "get_peer": lambda self, _pid: None,
        })()
        self.network_cfg = type("Network", (), {"heartbeat_interval": 1.0})()


class FakeNode:
    def __init__(self, north, east, timestamp):
        self.lat, self.lon, _ = convert_local_offset_to_global(0.0, 0.0, 0.0, north, east)
        self.alt = 0.0
        self.last_position_time = timestamp


def get_cfg(name):
    os.environ["DRONEOS_PROFILE"] = "sim" if name == "sim" else "hw"
    return resolve_flight_config(os.path.join(os.getcwd(), "DroneOS/configs"), FlightConfig)


def _new_stats():
    return {
        "accepted": 0, "rejected": 0, "violations": 0,
        "min_margins": {"sim": float("inf"), "hw": float("inf")},
        "max_path_length": 0.0, "reasons": Counter(),
    }


baseline_stats = _new_stats()
perturbed_stats = _new_stats()


def _offset_within_radius(rng, radius):
    """Fixed-seed, bounded, non-zero planar offset in metres."""
    angle = rng.uniform(0.0, 2.0 * math.pi)
    distance = rng.uniform(radius * 0.1, radius)
    return distance * math.cos(angle), distance * math.sin(angle)


def _reject_bucket(plan):
    reason = plan.reject_reason
    if reason.startswith("collision with dead drone"):
        reason = "collision with dead drone"
    elif reason.startswith("min separation"):
        reason = "min separation"
    elif reason.startswith("PRECONDITION FAILED: not settled"):
        reason = "not settled"
    return f"{plan.reject_kind.name}: {reason}"


@pytest.mark.parametrize("cfg_name", ["sim", "hw"])
@pytest.mark.parametrize("f_type", FORMATIONS)
@pytest.mark.parametrize("old_N", [4, 3])
@pytest.mark.parametrize("dead_slot", [0, 1, 2, 3])
@pytest.mark.parametrize("spacing_multiplier", [0.5, 15.0, 20.0])
@pytest.mark.parametrize("perturbed", [False, True])
def test_healing_oracle(cfg_name, f_type, old_N, dead_slot, spacing_multiplier, perturbed):
    if dead_slot >= old_N:
        pytest.skip("invalid slot for N")

    cfg = get_cfg(cfg_name)
    swarm = MockSwarm()
    manager = CoordinationManager(swarm, cfg)
    if perturbed:
        manager.mode = "active"
    stats = perturbed_stats if perturbed else baseline_stats

    min_form = float(getattr(cfg.formation, "min_formation_separation_m", 8.0))
    min_ca = float(getattr(cfg.collision_avoidance, "min_horizontal_distance", 2.0))
    margin = float(manager.config_dict.get("heal_separation_margin_m", 1.0))
    position_margin = float(manager.config_dict.get("heal_position_margin_m", 2.0))
    min_required_sep = max(min_form, min_ca) + margin + position_margin
    spacing = max(min_form, min_ca) + margin + 0.5 if spacing_multiplier == 0.5 else spacing_multiplier

    drones = [f"d{i}" for i in range(old_N)]
    slots = {pid: slot for slot, pid in enumerate(drones)}
    dead_drone = f"d{dead_slot}"
    survivors = {pid for pid in drones if pid != dead_drone}
    current_anchor = "d0"
    prop_anchor = "d1" if dead_slot == 0 else "d0"

    engine = FormationEngine(swarm, None, None)
    telemetry = type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})()
    expected = engine.get_expected_positions(telemetry, {"type": f_type, "spacing": spacing, "slot_assignments": slots})
    old_positions = {
        pid: global_offset_local_m(0.0, 0.0, lat, lon)
        for pid, (lat, lon) in expected.items()
    }

    # Every survivor receives a reproducible physical offset bounded by the
    # configured settle radius.  These modified coordinates are given to the
    # planner and are also the starts used for the oracle geometry.
    if perturbed:
        rng = random.Random(_ORACLE_SEED)
        radius = float(manager.config_dict.get("heal_settled_radius_m", 3.0))
        now = time.monotonic()
        for pid in sorted(survivors):
            north, east = old_positions[pid]
            dn, de = _offset_within_radius(rng, radius)
            assert math.hypot(dn, de) <= radius
            old_positions[pid] = (north + dn, east + de)
        for pid, (north, east) in old_positions.items():
            manager.membership.nodes[pid] = FakeNode(north, east, now)

    dead_position = old_positions[dead_drone]
    my_status = {}
    if perturbed:
        north, east = old_positions[prop_anchor]
        node = manager.membership.nodes[prop_anchor]
        my_status = {"lat": node.lat, "lon": node.lon, "alt": node.alt,
                     "gps_valid": True, "position_age": 0.0}

    plan = plan_healing(
        {"type": f_type, "spacing": spacing, "slot_assignments": slots},
        survivors, current_anchor, prop_anchor, prop_anchor, cfg,
        manager.membership, my_status,
    )
    if not plan or not plan.accepted:
        stats["rejected"] += 1
        assert plan and plan.reject_kind is not None and plan.reject_reason
        stats["reasons"][_reject_bucket(plan)] += 1
        return

    stats["accepted"] += 1
    swarm.identity.drone_id = prop_anchor
    new_expected = engine.get_expected_positions(
        telemetry,
        {"type": f_type, "spacing": spacing, "slot_assignments": plan.slot_assignments},
    )
    anchor_origin = old_positions[prop_anchor]
    physical_moves = {}
    for pid in survivors:
        north, east = global_offset_local_m(0.0, 0.0, *new_expected[pid][:2])
        physical_moves[pid] = (old_positions[pid], (anchor_origin[0] + north, anchor_origin[1] + east))

    for pid, (start, end) in physical_moves.items():
        stats["max_path_length"] = max(stats["max_path_length"], math.dist(start, end))
        if not perturbed and pid == prop_anchor:
            assert math.dist(start, end) < 1e-4
        dead_distance = _pt_to_segment(dead_position, start, end)
        stats["min_margins"][cfg_name] = min(stats["min_margins"][cfg_name], dead_distance - min_required_sep)
        if dead_distance + 0.1 < min_required_sep:
            stats["violations"] += 1
            pytest.fail(f"dead obstacle: {pid} is {dead_distance:.2f}m away")

        for other, (other_start, other_end) in physical_moves.items():
            if other == pid:
                continue
            assert math.dist(end, other_start) + 0.1 >= min_required_sep
            assert math.dist(end, other_end) + 0.1 >= min_required_sep
            if other > pid:
                separation = analytic_segment_distance(start, end, other_start, other_end)
                stats["min_margins"][cfg_name] = min(stats["min_margins"][cfg_name], separation - min_required_sep)
                if separation + 0.1 < min_required_sep:
                    stats["violations"] += 1
                    pytest.fail(f"segment separation: {pid}/{other} is {separation:.2f}m")


@pytest.fixture(scope="session", autouse=True)
def print_stats():
    yield
    for label, stats in (("UNPERTURBED", baseline_stats), ("PERTURBED", perturbed_stats)):
        print(f"{label} SUMMARY: accepted={stats['accepted']} rejected={stats['rejected']} violations={stats['violations']}")
        print(f"{label} smallest margin: sim={stats['min_margins']['sim']:.2f} hw={stats['min_margins']['hw']:.2f}")
        print(f"{label} maximum path length: {stats['max_path_length']:.2f}m")
        print(f"{label} reject breakdown: {dict(sorted(stats['reasons'].items()))}")
