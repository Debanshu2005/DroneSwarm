import math
import copy
from typing import Dict, Set, Tuple, List, Optional, Any

from DroneOS.core.formation_manager import FormationManager, FormationType, global_offset_local_m
from DroneOS.core.formation_engine import FormationEngine


def _get_slot_offset(f_type: str, slot: int, spacing: float, total: int) -> Tuple[float, float, float]:
    """Uses FormationEngine to compute the exact target offset relative to the anchor's physical position."""
    class DummySwarm:
        class Identity:
            drone_id = ""
        identity = Identity()
    engine = FormationEngine(DummySwarm(), None, None)
    try:
        engine.form_mgr.set_formation(FormationType(f_type), spacing)
    except ValueError:
        engine.form_mgr.set_formation(FormationType.V, spacing)
    return engine._anchor_relative_offset(slot, total)

def _point_to_segment(p: Tuple[float, float], s1: Tuple[float, float], s2: Tuple[float, float]) -> float:
    l2 = (s1[0]-s2[0])**2 + (s1[1]-s2[1])**2
    if l2 == 0:
        return math.hypot(p[0]-s1[0], p[1]-s1[1])
    t = max(0, min(1, ((p[0]-s1[0])*(s2[0]-s1[0]) + (p[1]-s1[1])*(s2[1]-s1[1])) / l2))
    proj = (s1[0] + t*(s2[0]-s1[0]), s1[1] + t*(s2[1]-s1[1]))
    return math.hypot(p[0]-proj[0], p[1]-proj[1])

def _segment_intersection(p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float], p4: Tuple[float, float]) -> bool:
    def ccw(A, B, C): return (C[1]-A[1])*(B[0]-A[0]) > (B[1]-A[1])*(C[0]-A[0])
    return ccw(p1,p3,p4) != ccw(p2,p3,p4) and ccw(p1,p2,p3) != ccw(p1,p2,p4)

def _segment_distance(p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float], p4: Tuple[float, float]) -> float:
    if _segment_intersection(p1, p2, p3, p4):
        return 0.0
    return min(
        _point_to_segment(p1, p3, p4),
        _point_to_segment(p2, p3, p4),
        _point_to_segment(p3, p1, p2),
        _point_to_segment(p4, p1, p2)
    )

from enum import Enum
class RejectKind(Enum):
    FINAL = 1
    TRANSIENT = 2
    CONFIG = 3

class HealPlan:
    def __init__(self, slot_assignments: Dict[str, int], reason: str, moves: Dict[str, Tuple[int, int]],
                 total_travel: float, min_separation: float, accepted: bool = True, reject_reason: str = "",
                 reject_kind: Optional[RejectKind] = None, unsettled: Optional[Dict[str, float]] = None):
        self.slot_assignments = slot_assignments
        self.reason = reason
        self.moves = moves
        self.total_travel = total_travel
        self.min_separation = min_separation
        self.accepted = accepted
        self.reject_reason = reject_reason
        self.reject_kind = reject_kind
        self.is_hold = False
        self.hold_slot = -1
        self.unsettled = unsettled

class EvaluatedPlan:
    def __init__(self, accepted: bool, min_separation: float, total_travel: float, reject_reason: str = "", reject_kind: Optional[RejectKind] = None):
        self.accepted = accepted
        self.min_separation = min_separation
        self.total_travel = total_travel
        self.reject_reason = reject_reason
        self.reject_kind = reject_kind

def _evaluate_plan(
    new_assignments: Dict[str, int],
    start_positions: Dict[str, Tuple[float, float]],
    dead_positions: Dict[str, Tuple[float, float]],
    f_type: str,
    spacing: float,
    min_required_sep: float,
    dead_drone_obstacle: bool,
    dead_obstacle_radius_m: float,
    method_name: str,
    prop_anchor: str
) -> EvaluatedPlan:
    """
    Evaluates a candidate slot assignment for safety.
    All positions are in the OLD anchor frame (d0 at origin).
    """
    total_drones = len(new_assignments) + len(dead_positions)
    EPSILON = 1e-4

    # The new anchor stays exactly at its start position in the physical world.
    if prop_anchor not in new_assignments:
        return EvaluatedPlan(False, 0.0, 0.0, "promoted anchor not in assignments")
    
    prop_anchor_start = start_positions[prop_anchor]
    
    # But wait, what if the proposed anchor is not in slot 0? The proposal logic puts it in slot 0.
    if new_assignments[prop_anchor] != 0:
        return EvaluatedPlan(False, 0.0, 0.0, "promoted anchor must be assigned slot 0")

    # We know the new anchor's physical position is prop_anchor_start.
    # We want the new anchor's offset to evaluate to prop_anchor_start.
    # The new anchor is assigned slot 0, so _get_slot_offset(..., 0, ...) is (0,0).
    # Thus the frame shift is exactly prop_anchor_start.
    origin_shift_vec = prop_anchor_start

    phys_moves = {}  # pid -> (start_pos, end_pos)
    for pid, new_slot in new_assignments.items():
        start = start_positions[pid]
        end_rel = _get_slot_offset(f_type, new_slot, spacing, len(new_assignments))
        end_abs = (origin_shift_vec[0] + end_rel[0], origin_shift_vec[1] + end_rel[1])
        phys_moves[pid] = (start, end_abs)

    total_travel = sum(math.hypot(start[0]-end[0], start[1]-end[1]) for start, end in phys_moves.values())

    min_dist = float('inf')
    reject_reason = ""

    survivor_pids = list(phys_moves.keys())
    
    # For every pair of survivors, check segment-to-segment distance.
    for i in range(len(survivor_pids)):
        for j in range(i+1, len(survivor_pids)):
            pid1 = survivor_pids[i]
            pid2 = survivor_pids[j]
            s1, e1 = phys_moves[pid1]
            s2, e2 = phys_moves[pid2]
            
            # Check timing-independent segment distance
            d = _segment_distance(s1, e1, s2, e2)
            if d < min_dist:
                min_dist = d
            if d < min_required_sep - EPSILON:
                reject_reason = f"min separation {d:.2f} < {min_required_sep:.2f} between {pid1} and {pid2}"
                return EvaluatedPlan(False, min_dist, total_travel, reject_reason)
                
            # Extra safety check: reject if one drone's TARGET is within min_req of another's START position
            # This protects drones that hover in place from drones that fly over them.
            d_target_to_start1 = math.hypot(e1[0]-s2[0], e1[1]-s2[1])
            if d_target_to_start1 < min_required_sep - EPSILON:
                return EvaluatedPlan(False, d_target_to_start1, total_travel, f"target of {pid1} too close to start of {pid2}")
            d_target_to_start2 = math.hypot(e2[0]-s1[0], e2[1]-s1[1])
            if d_target_to_start2 < min_required_sep - EPSILON:
                return EvaluatedPlan(False, d_target_to_start2, total_travel, f"target of {pid2} too close to start of {pid1}")

    # Check against dead drones
    if dead_drone_obstacle:
        for pid1, (s1, e1) in phys_moves.items():
            for dead_pid, dead_pos in dead_positions.items():
                d = _point_to_segment(dead_pos, s1, e1)
                if d < min_dist:
                    min_dist = d
                if d < dead_obstacle_radius_m - EPSILON:
                    reject_reason = f"collision with dead drone {dead_pid} at distance {d:.2f}"
                    return EvaluatedPlan(False, min_dist, total_travel, reject_reason)
                    
                # Also check target distance specifically
                d_target = math.hypot(e1[0]-dead_pos[0], e1[1]-dead_pos[1])
                if d_target < dead_obstacle_radius_m - EPSILON:
                    return EvaluatedPlan(False, d_target, total_travel, f"target of {pid1} hits dead drone {dead_pid}")


    # PARTIAL DELIVERY CHECK
    # Check all combinations of partial delivery among the non-anchor survivors.
    # The anchor (prop_anchor) always receives the update.
    import itertools
    non_anchor_survivors = [pid for pid in survivor_pids if pid != prop_anchor]
    
    # Iterate over all possible subsets of drones that MISSED the update (i.e. stay at start)
    for r in range(1, len(non_anchor_survivors) + 1):
        for missed_subset in itertools.combinations(non_anchor_survivors, r):
            missed_set = set(missed_subset)
            # For this scenario, drones in missed_set stay at their start pos.
            # Drones NOT in missed_set move from start to target.
            
            # Check collisions between moving drones and missed drones
            for moving_pid in survivor_pids:
                if moving_pid in missed_set:
                    continue
                s_move, e_move = phys_moves[moving_pid]
                
                for missed_pid in missed_set:
                    # Missed drone is stationary at its start position
                    pos_missed = phys_moves[missed_pid][0]
                    
                    # Distance from moving drone's path to the stationary missed drone
                    d = _point_to_segment(pos_missed, s_move, e_move)
                    if d < min_required_sep - EPSILON:
                        reject_reason = f"partial delivery hazard: {moving_pid} hits frozen {missed_pid} (missed set: {missed_subset}) dist={d:.2f}"
                        return EvaluatedPlan(False, d, total_travel, reject_reason)

    return EvaluatedPlan(True, min_dist, total_travel, "")

def min_required_sep(flight_cfg, config_dict) -> float:
    form_cfg = getattr(flight_cfg, "formation", None)
    ca_cfg = getattr(flight_cfg, "collision_avoidance", None)
    
    from DroneOS.core.formation_engine import _DEFAULT_MIN_SEP_M
    from DroneOS.shared.config.models import CollisionAvoidanceConfig
    
    default_ca = CollisionAvoidanceConfig().min_horizontal_distance
    min_form_sep = float(getattr(form_cfg, "min_formation_separation_m", _DEFAULT_MIN_SEP_M)) if form_cfg else _DEFAULT_MIN_SEP_M
    min_ca_dist = float(getattr(ca_cfg, "min_horizontal_distance", default_ca)) if ca_cfg else default_ca
    margin = float(config_dict.get("heal_separation_margin_m", 1.0))
    pos_margin = float(config_dict.get("heal_position_margin_m", 2.0))
    
    base_min_req = max(min_form_sep, min_ca_dist)
    return base_min_req + margin + pos_margin

def plan_healing(
    current_params: Dict[str, Any],
    healthy_members: Set[str],
    current_anchor: Optional[str],
    prop_anchor: Optional[str],
    my_id: str,
    flight_cfg: Any,
    membership_view: Any = None,
    my_status: Dict[str, Any] = None
) -> Optional[HealPlan]:
    """
    Core algorithm for healing formations.
    Assumption: dead_drone_model is "hover", meaning dead drones remain statically at their last known positions.
    """
    from DroneOS.core.coordination.manager import logger
    
    old_slots = current_params.get("slot_assignments", {})
    if not old_slots:
        return None

    if len(healthy_members) < 2:
        return None  # Cannot form a formation with 1 drone

    if my_id != prop_anchor:
        return None  # Only the proposed anchor plans


    f_type = current_params.get("type", "V")
    spacing = float(current_params.get("spacing", 10.0))
    total_drones = len(old_slots)

    # Resolve config
    config_dict = getattr(flight_cfg, "coordination", {}) if flight_cfg else {}
    if not isinstance(config_dict, dict):
        try:
            config_dict = dict(config_dict)
        except:
            config_dict = {}

    # Config thresholds
    min_req_sep = min_required_sep(flight_cfg, config_dict)
    
    dead_drone_obstacle = str(config_dict.get("dead_drone_obstacle", "true")).lower() == "true"
    dead_obstacle_radius_m = min_req_sep
    
    is_advisory = config_dict.get("mode", "advisory").lower() == "advisory"
    if not is_advisory and not dead_drone_obstacle:
        return HealPlan(old_slots, "none", {}, 0.0, 0.0, False, "dead_drone_obstacle=false not allowed in active mode", RejectKind.CONFIG)
        
    advisory_suffix = " (dead drone not modelled)" if (is_advisory and not dead_drone_obstacle) else ""

    # Identify dead members
    dead_members = [pid for pid in old_slots if pid not in healthy_members]
    if not dead_members:
        return None

    # Get real positions if available
    anchor_lat, anchor_lon = 0.0, 0.0  # default for tests without membership_view
    anchor_id_old = None
    for pid, s in old_slots.items():
        if int(s) == 0:
            anchor_id_old = pid
            break
            
    if membership_view and anchor_id_old:
        if anchor_id_old == my_id and my_status and my_status.get("lat") is not None and my_status.get("lon") is not None:
            anchor_lat, anchor_lon = my_status["lat"], my_status["lon"]
        else:
            node = membership_view.nodes.get(anchor_id_old)
            if node and getattr(node, "lat", None) is not None and getattr(node, "lon", None) is not None:
                anchor_lat, anchor_lon = node.lat, node.lon
            
    start_positions = {}
    dead_positions = {}
    
    is_advisory = config_dict.get("mode", "advisory").lower() == "advisory"
    settled_radius = float(config_dict.get("heal_settled_radius_m", 3.0))
    heal_max_position_age_s = float(config_dict.get("heal_max_position_age_s", 5.0))
    used_nominal_for = []
    
    for pid, old_s in old_slots.items():
        nom_pos_3d = _get_slot_offset(f_type, int(old_s), spacing, total_drones)
        nom_pos = (nom_pos_3d[0], nom_pos_3d[1])
        
        actual_pos = nom_pos
        has_real_pos = False
        
        node_lat, node_lon, node_alt = None, None, None
        age = None
        gps_valid = True
        
        if pid == my_id and my_status:
            node_lat = my_status.get("lat")
            node_lon = my_status.get("lon")
            node_alt = my_status.get("alt")
            if node_lat is None or node_lon is None:
                node = membership_view.nodes.get(pid) if membership_view else None
                if node:
                    node_lat = getattr(node, "lat", None)
                    node_lon = getattr(node, "lon", None)
                    node_alt = getattr(node, "alt", None)
            age = my_status.get("position_age", 0.0)
            gps_valid = my_status.get("gps_valid", True)
        elif membership_view:
            node = membership_view.nodes.get(pid)
            if node:
                node_lat = getattr(node, "lat", None)
                node_lon = getattr(node, "lon", None)
                node_alt = getattr(node, "alt", None)
                if getattr(node, "last_position_time", None) is not None:
                    age = membership_view.clock() - node.last_position_time
                else:
                    age = 0.0
                    
        if (anchor_lat is not None and anchor_lon is not None and
                node_lat is not None and node_lon is not None):
            dn, de = global_offset_local_m(anchor_lat, anchor_lon, node_lat, node_lon)
            gps_offset_dist = math.hypot(dn, de)
            is_anchor = (pid == anchor_id_old)
            if is_anchor or gps_offset_dist > 0.5:
                actual_pos = (dn, de)
                has_real_pos = True

        if pid in healthy_members:
            is_stale = age is None or age < 0 or (heal_max_position_age_s > 0 and age > heal_max_position_age_s)
            is_invalid_gps = pid == my_id and not gps_valid
            
            if not is_advisory:
                if not has_real_pos:
                    return HealPlan(old_slots, "none", {}, 0.0, 0.0, False, f"missing or invalid telemetry for {pid}", RejectKind.TRANSIENT)
                if is_invalid_gps:
                    return HealPlan(old_slots, "none", {}, 0.0, 0.0, False, f"gps invalid for {pid}", RejectKind.TRANSIENT)
                if is_stale:
                    return HealPlan(old_slots, "none", {}, 0.0, 0.0, False, f"stale telemetry for {pid} (age={age})", RejectKind.TRANSIENT)
            else:
                if not has_real_pos or is_invalid_gps or is_stale:
                    used_nominal_for.append(pid)
                    
            dist_to_nom = math.hypot(actual_pos[0]-nom_pos[0], actual_pos[1]-nom_pos[1])
            if dist_to_nom > settled_radius:
                return HealPlan(old_slots, "none", {}, 0.0, 0.0, False, f"PRECONDITION FAILED: not settled ({pid} dist={dist_to_nom:.2f}m > {settled_radius:.2f}m)", RejectKind.TRANSIENT, unsettled={"pid": pid, "dist": dist_to_nom, "radius": settled_radius})
            
            start_positions[pid] = actual_pos
        else:
            dead_positions[pid] = actual_pos
            
    if used_nominal_for:
        advisory_suffix += f" (nominal positions used for {','.join(used_nominal_for)})"

    plans: List[HealPlan] = []

    def generate_candidate(method: str) -> Dict[str, int]:
        new_s = {}
        
        if method == "anchor_promotion":
            if current_anchor in healthy_members:
                return {} # skip if anchor alive
            if prop_anchor in healthy_members:
                new_s[prop_anchor] = 0
            remaining = [p for p in healthy_members if p not in new_s]
            for p in remaining:
                new_s[p] = old_slots[p]
            return new_s

        if current_anchor not in healthy_members and prop_anchor in healthy_members:
            new_s[prop_anchor] = 0
        else:
            if current_anchor in healthy_members:
                new_s[current_anchor] = 0
                
        remaining = [p for p in healthy_members if p not in new_s]

        if method == "compaction":
            sorted_rem = sorted(remaining, key=lambda p: old_slots[p])
            next_slot = 1
            for p in sorted_rem:
                while next_slot in new_s.values():
                    next_slot += 1
                new_s[p] = next_slot
                next_slot += 1

        elif method == "tail_fill":
            for p in remaining:
                new_s[p] = old_slots[p]
            req = set(range(len(healthy_members)))
            filled = set(new_s.values())
            holes = sorted(req - filled)
            sorted_by_slot = sorted(new_s.keys(), key=lambda p: new_s[p], reverse=True)
            hi = 0
            for p in sorted_by_slot:
                if hi < len(holes) and new_s[p] > (holes[-1] if holes else -1):
                    new_s[p] = holes[hi]
                    hi += 1
                    
        return new_s

    for method in ["compaction", "tail_fill", "anchor_promotion"]:
        new_assignments = generate_candidate(method)
        if new_assignments:
            eval_res = _evaluate_plan(
                new_assignments, start_positions, dead_positions,
                f_type, spacing, min_req_sep,
                dead_drone_obstacle, dead_obstacle_radius_m, method, prop_anchor
            )
            moves = {pid: (old_slots[pid], new_assignments[pid]) for pid in healthy_members}
            plans.append(HealPlan(
                new_assignments, method + advisory_suffix, moves,
                eval_res.total_travel, eval_res.min_separation,
                eval_res.accepted, eval_res.reject_reason, eval_res.reject_kind
            ))
            
    accepted_plans = [p for p in plans if p.accepted]
    
    if not accepted_plans:
        # NO_PLAN: all rejected
        best_rejected = plans[0] if plans else HealPlan({}, "NO_PLAN", {}, 0.0, 0.0, False, "no strategies generated")
        best_rejected.reason = "NO_PLAN"
        best_rejected.is_hold = False
        return best_rejected
        
    accepted_plans.sort(key=lambda p: p.total_travel)
    best = accepted_plans[0]
    
    survivor_pids = set(start_positions.keys())
    all_noop = all(old_s == new_s for pid, (old_s, new_s) in best.moves.items() if pid in survivor_pids)
    if all_noop and best.total_travel == 0.0:
        best.is_hold = True
        best.hold_slot = old_slots[dead_members[0]]
    
    return best

def validate_formation_params(params: dict, flight_cfg: any) -> bool:
    slots = params.get("slot_assignments")
    if not isinstance(slots, dict) or not slots:
        return False
        
    try:
        from DroneOS.core.formation_manager import FormationType
        _ = FormationType(params.get("type", "V").upper())
    except (ValueError, TypeError, AttributeError):
        return False
        
    try:
        spacing = float(params.get("spacing", 0.0))
        min_sep = 8.0
        min_ca = 2.0
        if flight_cfg is not None:
            if getattr(flight_cfg, "formation", None):
                min_sep = float(getattr(flight_cfg.formation, "min_formation_separation_m", min_sep))
            if getattr(flight_cfg, "collision_avoidance", None):
                min_ca = float(getattr(flight_cfg.collision_avoidance, "min_horizontal_distance", min_ca))
        min_viable = 1.5 * max(min_sep, min_ca)
        if spacing < min_viable:
            return False
    except (TypeError, ValueError):
        return False
        
    return True




