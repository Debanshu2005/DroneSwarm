import pytest
from DroneOS.core.coordination.healing import plan_healing, validate_params

class MockConfig:
    min_formation_separation_m = 8.0
    min_horizontal_distance = 2.0
    heal_separation_margin_m = 1.0
    coordination = {"dead_drone_obstacle": "false"}

def _make_live_params(f_type="V", spacing=10.0, members=4):
    slots = {f"d{i}": i for i in range(members)}
    return {
        "type": f_type,
        "spacing": spacing,
        "slot_assignments": slots,
        "members": list(slots.keys()),
        "some_other_key": "preserved"
    }

def test_no_op_when_nothing_died():
    params = _make_live_params()
    healthy = {"d0", "d1", "d2", "d3"}
    # Anchor is d0
    plan = plan_healing(params, healthy, "d0", "d0", "d0", MockConfig())
    assert plan is None

def test_follower_dies():
    # N=4, V formation. d2 dies.
    params = _make_live_params("V", 20.0, 4)
    healthy = {"d0", "d1", "d3"}
    plan = plan_healing(params, healthy, "d0", "d0", "d0", MockConfig())
    assert plan is not None
    assert plan.accepted
    assert "d2" not in plan.slot_assignments
    assert set(plan.slot_assignments.values()) == {0, 1, 2}

def test_anchor_dies():
    params = _make_live_params("LINE", 20.0, 4)
    healthy = {"d1", "d2", "d3"}
    # Proposed anchor is d1
    plan = plan_healing(params, healthy, None, "d1", "d1", MockConfig())
    assert plan is not None
    assert plan.accepted
    assert plan.slot_assignments["d1"] == 0

def test_two_die():
    params = _make_live_params("SQUARE", 20.0, 4)
    healthy = {"d0", "d3"}
    plan = plan_healing(params, healthy, "d0", "d0", "d0", MockConfig())
    assert plan is not None
    assert plan.accepted
    assert set(plan.slot_assignments.values()) == {0, 1}

def test_separation_rejection():
    # Config says min is 8.0, but spacing is 5.0, so straight line path will fail separation.
    params = _make_live_params("V", 5.0, 3)
    healthy = {"d1", "d2"} # d0 dies, d1 promoted
    config = MockConfig()
    config.min_formation_separation_m = 8.0
    plan = plan_healing(params, healthy, None, "d1", "d1", config)
    # The plan should be rejected because spacing=5.0 < 8.0
    assert plan is not None
    assert not plan.accepted
    assert "min separation" in plan.reject_reason

def test_healer_only():
    params = _make_live_params("V", 10.0, 3)
    healthy = {"d0", "d2"}
    # d0 is anchor. I am d2. I should not plan.
    plan = plan_healing(params, healthy, "d0", "d0", "d2", MockConfig())
    assert plan is None

def test_line_formation_cross_anchor():
    params = _make_live_params("LINE", 20.0, 4)
    healthy = {"d0", "d2", "d3"} # d1 dies
    
    cfg = MockConfig()
    cfg.coordination = {"dead_drone_obstacle": "true"}
    
    plan = plan_healing(params, healthy, "d0", "d0", "d0", cfg)
    assert plan is not None
    # With dead_drone_obstacle=True, any strategy that fills slot 1 is rejected
    # because it crashes into the dead drone.
    # Therefore the plan should be rejected.
    assert plan.accepted is False

def test_validate_params():
    assert validate_params(_make_live_params())
    assert not validate_params({"type": "V"}) # missing spacing, slot_assignments
    
def test_all_formations_scaling():
    types = ["V", "LINE", "SQUARE", "COLUMN", "ECHELON_LEFT", "ECHELON_RIGHT", "DIAMOND", "GRID", "CIRCLE"]
    for t in types:
        for n in [2, 3, 4]:
            params = _make_live_params(t, 20.0, n)
            # follower dies
            healthy = {f"d{i}" for i in range(n) if i != n-1}
            if len(healthy) >= 2:
                plan = plan_healing(params, healthy, "d0", "d0", "d0", MockConfig())
                assert plan is not None
                assert plan.accepted

def test_dead_drone_obstacle_changes_outcome():
    class MockConfig:
        min_formation_separation_m = 3.0
        min_horizontal_distance = 2.0
        heal_separation_margin_m = 1.0
        coordination = {"dead_drone_obstacle": "false"}
    
    slots = {"d0": 0, "d1": 1, "d2": 2, "d3": 3}
    
    cfg_true = MockConfig()
    cfg_true.coordination = {"dead_drone_obstacle": "true"}
    
    cfg_false = MockConfig()
    cfg_false.coordination = {"dead_drone_obstacle": "false"}
    
    from DroneOS.core.coordination.healing import plan_healing
    
    # Use ECHELON_RIGHT formation, d2 dies.
    # d3 moves from 3 to 2.
    # In ECHELON_RIGHT, d3 is at the end of the diagonal, so moving to 2 does not cross anyone else.
    # If dead_drone_obstacle is True, d3 hits the dead drone at slot 2 (min sep = 0).
    # If False, d3 just safely occupies slot 2.
    
    plan_true = plan_healing({"type": "ECHELON_RIGHT", "spacing": 20.0, "slot_assignments": slots}, {"d0", "d1", "d3"}, "d0", "d0", "d0", cfg_true)
    plan_false = plan_healing({"type": "ECHELON_RIGHT", "spacing": 20.0, "slot_assignments": slots}, {"d0", "d1", "d3"}, "d0", "d0", "d0", cfg_false)
    
    print("PLAN FALSE:", plan_false.reject_reason if plan_false else "None")
    assert plan_true.accepted is False
    assert plan_false.accepted is True

def test_epsilon_edge_cases(monkeypatch):
    from DroneOS.core.coordination.healing import _evaluate_plan, RejectKind
    import DroneOS.core.coordination.healing as healing
    
    # EXACTLY equal should be accepted
    # d0 stays at 0,0. d1 goes from 10,10 to 10,0 (distance to 0,0 is exactly 10.0)
    monkeypatch.setattr(healing, "_get_slot_offset", lambda f, s, sp, t: (10.0, 0.0, 0.0) if s == 1 else (0.0, 0.0, 0.0))
    
    ev = _evaluate_plan(
        {"d0": 0, "d1": 1},
        {"d0": (0.0, 0.0), "d1": (10.0, 10.0)},
        {},
        "LINE", 10.0, 10.0, False, 10.0, "test", "d0"
    )
    assert ev.accepted is True
    
    # Just barely below EPSILON should be rejected
    monkeypatch.setattr(healing, "_get_slot_offset", lambda f, s, sp, t: (9.9998, 0.0, 0.0) if s == 1 else (0.0, 0.0, 0.0))
    ev_reject = _evaluate_plan(
        {"d0": 0, "d1": 1},
        {"d0": (0.0, 0.0), "d1": (9.9998, 10.0)},
        {},
        "LINE", 10.0, 10.0, False, 10.0, "test", "d0"
    )
    assert ev_reject.accepted is False
    assert ev_reject.reject_kind == RejectKind.FINAL

def test_active_mode_refuses_dead_drone_obstacle():
    from DroneOS.core.coordination.healing import plan_healing, RejectKind
    class MockConfig:
        coordination = {"mode": "active", "dead_drone_obstacle": "false"}
    
    slots = {"d0": 0, "d1": 1, "d2": 2}
    plan = plan_healing({"type": "V", "spacing": 15.0, "slot_assignments": slots}, {"d0", "d1"}, "d0", "d0", "d0", MockConfig())
    assert plan is not None
    assert plan.accepted is False
    assert plan.reject_kind == RejectKind.CONFIG
    assert "not allowed in active mode" in plan.reject_reason

def test_advisory_labels_dead_drone():
    from DroneOS.core.coordination.healing import plan_healing
    class MockConfig:
        coordination = {"mode": "advisory", "dead_drone_obstacle": "false"}
    slots = {"d0": 0, "d1": 1, "d2": 2}
    plan = plan_healing({"type": "V", "spacing": 15.0, "slot_assignments": slots}, {"d0", "d1"}, "d0", "d0", "d0", MockConfig())
    assert plan is not None
    assert plan.accepted is True
    assert "(dead drone not modelled)" in plan.reason

def test_advisory_labels_nominal_positions():
    from DroneOS.core.coordination.healing import plan_healing
    class MockConfig:
        coordination = {"mode": "advisory"}
    # No membership_view provided, meaning it will fall back to nominal positions.
    slots = {"d0": 0, "d1": 1, "d2": 2}
    plan = plan_healing({"type": "V", "spacing": 15.0, "slot_assignments": slots}, {"d0", "d1"}, "d0", "d0", "d0", MockConfig())
    assert plan is not None
    assert plan.accepted is True
    assert "(nominal positions used)" in plan.reason

def test_transient_rejection_missing_telemetry():
    from DroneOS.core.coordination.healing import plan_healing, RejectKind
    class MockConfig:
        coordination = {"mode": "active"}
    
    slots = {"d0": 0, "d1": 1, "d2": 2}
    plan = plan_healing({"type": "V", "spacing": 15.0, "slot_assignments": slots}, {"d0", "d1"}, "d0", "d0", "d0", MockConfig())
    assert plan is not None
    assert plan.accepted is False
    assert plan.reject_kind == RejectKind.TRANSIENT
    assert "missing or stale telemetry" in plan.reject_reason

def test_transient_rejection_unsettled():
    from DroneOS.core.coordination.healing import plan_healing, RejectKind
    class MockConfig:
        coordination = {"mode": "active"}
    
    slots = {"d0": 0, "d1": 1, "d2": 2}
    class MockNode:
        def __init__(self, lat, lon):
            self.lat = lat
            self.lon = lon
    class MockMembership:
        nodes = {"d0": MockNode(0.0, 0.0), "d1": MockNode(10.0, 10.0)}
    plan = plan_healing({"type": "V", "spacing": 15.0, "slot_assignments": slots}, {"d0", "d1"}, "d0", "d0", "d0", MockConfig(), MockMembership())
    assert plan is not None
    assert plan.accepted is False
    assert plan.reject_kind == RejectKind.TRANSIENT
    assert "not settled" in plan.reject_reason

import pytest

FORMATIONS = ["V", "LINE", "SQUARE", "COLUMN", "ECHELON_LEFT", "ECHELON_RIGHT", "DIAMOND", "GRID", "CIRCLE"]

@pytest.mark.parametrize("f_type", FORMATIONS)
@pytest.mark.parametrize("old_N", [4, 3])
@pytest.mark.parametrize("dead_slot", [0, 1, 2, 3])
def test_planner_equivalence_to_formation_engine(f_type, old_N, dead_slot):
    if dead_slot >= old_N:
        pytest.skip("invalid slot for N")
        
    from DroneOS.core.coordination.healing import plan_healing, _get_slot_offset
    from DroneOS.core.formation_engine import FormationEngine
    from DroneOS.core.formation_manager import global_offset_local_m

    class MockFlightCfg:
        class formation:
            min_formation_separation_m = 1.0
            velocity_gain = 1.0
        class collision_avoidance:
            min_horizontal_distance = 1.0
        class coordination:
            heal_separation_margin_m = 1.0
            heal_position_margin_m = 0.0
            dead_drone_obstacle = "false"
            mode = "advisory"
            heal_settled_radius_m = 9999.0

    class FakePeer:
        def __init__(self, lat, lon):
            self.lat = lat
            self.lon = lon
            self.last_position_time = 9999999999
            self.alt = 10.0

    class FakeRegistry:
        def __init__(self, peers):
            self.peers = peers
        def get_peer(self, pid):
            return self.peers.get(pid)

    class FakeIdentity:
        drone_id = "d0"
        
    engine = FormationEngine(None, None, MockFlightCfg())
    peers = {"d0": FakePeer(0.0, 0.0), "d1": FakePeer(0.0, 0.0)}
    engine.swarm_manager = type('obj', (object,), {'registry': FakeRegistry(peers), 'identity': FakeIdentity()})()
    
    class FakeTelemetry:
        gps_valid = True
        latitude = 0.0
        longitude = 0.0
        altitude = 10.0

    slots = {f"d{i}": i for i in range(old_N)}
    healthy = {f"d{i}" for i in range(old_N) if i != dead_slot}
    current_anchor = "d0"
    prop_anchor = "d1" if dead_slot == 0 else "d0"
    
    plan = plan_healing({"type": f_type, "spacing": 15.0, "slot_assignments": slots}, healthy, current_anchor, prop_anchor, prop_anchor, MockFlightCfg(), None)
    
    if not plan:
        return
    
    new_N = len(healthy)
    assert len(plan.slot_assignments) == new_N
    if plan.accepted:
        assert plan.min_separation >= 1.5
    
    engine_params = {
        "type": f_type,
        "spacing": 15.0,
        "slot_assignments": plan.slot_assignments
    }
    expected = engine.get_expected_positions(FakeTelemetry(), engine_params)
    
    for pid in healthy:
        ns = plan.slot_assignments[pid]
        heal_rel = _get_slot_offset(f_type, ns, 15.0, new_N)
        
        t_lat, t_lon = expected[pid]
        dn, de = global_offset_local_m(0.0, 0.0, t_lat, t_lon)
        
        diff_n = abs(dn - heal_rel[0])
        diff_e = abs(de - heal_rel[1])
        
        assert diff_n < 1e-3
        assert diff_e < 1e-3
