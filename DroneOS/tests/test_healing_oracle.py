import pytest
import math
import os
import random
from DroneOS.shared.config.loader import load_yaml_config
from DroneOS.shared.config.models import FlightConfig
from DroneOS.shared.config.profile import resolve_flight_config
from DroneOS.core.coordination.healing import plan_healing, RejectKind
from DroneOS.core.coordination.manager import CoordinationManager
from DroneOS.core.formation_manager import FormationManager, FormationType, global_offset_local_m
from DroneOS.core.formation_engine import FormationEngine

FORMATIONS = [
    "V", "LINE", "SQUARE", "COLUMN", "ECHELON_LEFT", 
    "ECHELON_RIGHT", "DIAMOND", "GRID", "CIRCLE"
]

def get_cfg(name):
    os.environ['DRONEOS_PROFILE'] = 'sim' if name == 'sim' else 'hw'
    return resolve_flight_config(os.path.join(os.getcwd(), 'DroneOS/configs'), FlightConfig)

class MockSwarm:
    def __init__(self):
        self.identity = type('MockIdentity', (), {'drone_id': 'd0'})
        self.registry = type('MockReg', (), {'get_all_peers': lambda: [], 'get_peer': lambda pid: type('Peer', (), {'last_seen': 100.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0, 'last_position_time': 100.0})()})()
        self.network_cfg = type('MockNet', (), {'heartbeat_interval': 1.0})()

stats = {'accepted': 0, 'rejected': 0, 'violations': 0, 'min_margins': {'sim': float('inf'), 'hw': float('inf')}, 'perturbed': 0}

def dense_segment_distance(start1, end1, start2, end2):
    min_dist = float('inf')
    steps = 200
    for i in range(steps + 1):
        t1 = i / steps
        x1 = start1[0] + t1 * (end1[0] - start1[0])
        y1 = start1[1] + t1 * (end1[1] - start1[1])
        for j in range(steps + 1):
            t2 = j / steps
            x2 = start2[0] + t2 * (end2[0] - start2[0])
            y2 = start2[1] + t2 * (end2[1] - start2[1])
            dist = math.hypot(x1 - x2, y1 - y2)
            if dist < min_dist:
                min_dist = dist
    return min_dist

def dense_point_distance(point, start, end):
    min_dist = float('inf')
    steps = 200
    for i in range(steps + 1):
        t = i / steps
        x = start[0] + t * (end[0] - start[0])
        y = start[1] + t * (end[1] - start[1])
        dist = math.hypot(x - point[0], y - point[1])
        if dist < min_dist:
            min_dist = dist
    return min_dist

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
    ms = MockSwarm()
    manager = CoordinationManager(ms, cfg)
    
    if perturbed:
        manager.mode = "active"
    
    min_form = float(getattr(cfg.formation, 'min_formation_separation_m', 8.0)) if cfg.formation else 8.0
    min_ca = float(getattr(cfg.collision_avoidance, 'min_horizontal_distance', 2.0)) if cfg.collision_avoidance else 2.0
    sep_margin = float(manager.config_dict.get('heal_separation_margin_m', 1.0))
    pos_margin = float(manager.config_dict.get('heal_position_margin_m', 2.0))
    
    min_required_sep = max(min_form, min_ca) + sep_margin + pos_margin
    floor = max(min_form, min_ca) + sep_margin
    
    if spacing_multiplier == 0.5:
        spacing = floor + 0.5
    else:
        spacing = spacing_multiplier
        
    drones = [f"d{i}" for i in range(old_N)]
    slots = {f"d{i}": i for i in range(old_N)}
    dead_drone = f"d{dead_slot}"
    survivors = set([d for d in drones if d != dead_drone])
    
    current_anchor = "d0"
    prop_anchor = "d1" if dead_slot == 0 else "d0"
    
    fe = FormationEngine(ms, None, None)
    class FakeTelemetry:
        gps_valid = True
        latitude = 0.0
        longitude = 0.0
        altitude = 0.0
        
    ms.identity.drone_id = current_anchor
    old_layout_params = {'type': f_type, 'spacing': spacing, 'slot_assignments': slots}
    
    expected_latlons = fe.get_expected_positions(FakeTelemetry(), old_layout_params)
    old_pos = {}
    for pid, ll in expected_latlons.items():
        n, e = global_offset_local_m(0.0, 0.0, ll[0], ll[1])
        old_pos[pid] = (n, e)
    
    if perturbed:
        stats['perturbed'] += 1
        random.seed(hash((f_type, old_N, dead_slot, spacing_multiplier)))
        radius = float(manager.config_dict.get('heal_settled_radius_m', 3.0))
        for pid in survivors:
            dx = (random.random() * 2 - 1) * radius
            dy = (random.random() * 2 - 1) * radius
            old_pos[pid] = (old_pos[pid][0] + dx, old_pos[pid][1] + dy)
            class FakeNode:
                def __init__(self, n, e):
                    self.lat, self.lon = n, e
                    self.last_position_time = 100.0
            manager.membership.nodes[pid] = FakeNode(old_pos[pid][0], old_pos[pid][1])
            
    dead_pos = old_pos[dead_drone]
    
    plan = plan_healing({'type': f_type, 'spacing': spacing, 'slot_assignments': slots}, survivors, current_anchor, prop_anchor, prop_anchor, cfg, manager.membership)
    
    if not plan or not plan.accepted:
        stats['rejected'] += 1
        assert plan.reject_kind is not None, "Reject kind not set"
        assert plan.reject_reason, "Reject reason must be non-empty"
        return
        
    stats['accepted'] += 1
    
    new_N = len(survivors)
    ms.identity.drone_id = prop_anchor
    new_layout_params = {'type': f_type, 'spacing': spacing, 'slot_assignments': plan.slot_assignments}
    new_latlons = fe.get_expected_positions(FakeTelemetry(), new_layout_params)
    
    new_anchor_origin = old_pos[prop_anchor]
    phys = {}
    for pid in survivors:
        n, e = global_offset_local_m(0.0, 0.0, new_latlons[pid][0], new_latlons[pid][1])
        phys[pid] = (old_pos[pid], (new_anchor_origin[0] + n, new_anchor_origin[1] + e))
        
    for pid in survivors:
        start, end = phys[pid]
        
        if pid == prop_anchor and not perturbed:
            dist = math.hypot(end[0] - start[0], end[1] - start[1])
            assert dist < 1e-4, f"New anchor {pid} has non-zero path length {dist}"
            
        min_dead = dense_point_distance(dead_pos, start, end)
        margin_dead = min_dead - min_required_sep
        if margin_dead < stats['min_margins'][cfg_name]: stats['min_margins'][cfg_name] = margin_dead
        if min_dead + 0.1 < min_required_sep:
            stats['violations'] += 1
            pytest.fail(f"Obstacle violation: {pid} within {min_dead:.2f} of dead drone")
            
        for other in survivors:
            if other == pid: continue
            os_start, os_end = phys[other]
            
            if math.hypot(end[0] - os_start[0], end[1] - os_start[1]) + 0.1 < min_required_sep:
                stats['violations'] += 1
                pytest.fail(f"{pid} target too close to {other} start")
            if math.hypot(end[0] - os_end[0], end[1] - os_end[1]) + 0.1 < min_required_sep:
                stats['violations'] += 1
                pytest.fail(f"{pid} target too close to {other} target")
                
            if other <= pid: continue
            
            d = dense_segment_distance(start, end, os_start, os_end)
            margin = d - min_required_sep
            if margin < stats['min_margins'][cfg_name]: stats['min_margins'][cfg_name] = margin
            if d + 0.1 < min_required_sep:
                stats['violations'] += 1
                pytest.fail(f"Separation violation: {pid} and {other} pass within {d:.2f}")

@pytest.fixture(scope="session", autouse=True)
def print_stats():
    yield
    print(f"\nORACLE SUMMARY: Accepted={stats['accepted']}, Rejected={stats['rejected']}, Violations={stats['violations']}")
    print(f"Smallest clearance margins: sim={stats['min_margins']['sim']:.2f}, hw={stats['min_margins']['hw']:.2f}")
    print(f"Perturbed runs={stats['perturbed']}")
