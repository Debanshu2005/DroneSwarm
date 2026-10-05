import logging

from DroneOS.core.coordination.anchor import is_healthy
from DroneOS.core.coordination.healing import HealPlan, RejectKind, plan_healing
from DroneOS.core.coordination.membership import MembershipView, PeerState
from DroneOS.core.coordination.manager import CoordinationManager
from DroneOS.tests.test_quorum import MockPeerState, MockSwarm


class MockFlightCfg:
    def __init__(self, mode="advisory", **coordination):
        self.coordination = {"mode": mode, "dead_drone_obstacle": "true", **coordination}
        self.formation = type("Formation", (), {"min_formation_separation_m": 8.0})()
        self.collision_avoidance = type("Collision", (), {"min_horizontal_distance": 6.0})()


def _line_offset(monkeypatch):
    import DroneOS.core.coordination.healing as healing

    def offset(_anchor_lat, _anchor_lon, node_lat, _node_lon):
        if node_lat == 1.1:
            return healing._get_slot_offset("LINE", 1, 15.0, 3)[:2]
        return (0.0, 0.0)

    monkeypatch.setattr(healing, "global_offset_local_m", offset)


def _active_plan(mv, cfg, my_status=None):
    fp = {"type": "LINE", "spacing": 15.0,
          "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}
    return plan_healing(fp, {"d0", "d1"}, "d0", "d0", "d0", cfg,
                        membership_view=mv, my_status=my_status)


def test_freshness_clock_agrees_in_both_directions(monkeypatch):
    _line_offset(monkeypatch)
    clock = [1000.0]
    mv = MembershipView(config={}, clock=lambda: clock[0])
    swarm = MockSwarm()
    swarm.registry.peers["d1"] = MockPeerState(battery=50.0, last_pos=1.7e9)
    cfg = MockFlightCfg("active")
    status = {"lat": 1.0, "lon": 1.0, "alt": 1.0,
              "gps_valid": True, "position_age": 0.0}

    # The wire stamp is epoch-valued.  Membership stores its local monotonic
    # receipt time, so both consumers see a fresh position at t=1000.
    mv.update_from_peer("d1", 1.0, 50.0, 1.1, 1.0, 1.0, peer_position_stamp=1.7e9)
    assert mv.nodes["d1"].last_position_time == 1000.0
    assert is_healthy("d1", "d0", mv, swarm, 30.0, 1000.0, 5.0)
    assert _active_plan(mv, cfg, status).accepted

    # Fresh -> stale: the same position and stamp must not be refreshed by a
    # heartbeat alone.  Both the anchor gate and planner reject it.
    clock[0] = 1007.0
    mv.update_from_peer("d1", 2.0, 50.0, 1.1, 1.0, 1.0, peer_position_stamp=1.7e9)
    assert mv.nodes["d1"].last_position_time == 1000.0
    assert not is_healthy("d1", "d0", mv, swarm, 30.0, 1007.0, 5.0)
    stale = _active_plan(mv, cfg, status)
    assert stale.reject_kind == RejectKind.TRANSIENT
    assert stale.reject_reason == "stale telemetry for d1 (age=7.0)"

    # Stale -> fresh: a changed peer position stamp is a new position sample.
    clock[0] = 1008.0
    mv.update_from_peer("d1", 3.0, 50.0, 1.1, 1.0, 1.0, peer_position_stamp=1.7e9 + 1)
    assert mv.nodes["d1"].last_position_time == 1008.0
    assert is_healthy("d1", "d0", mv, swarm, 30.0, 1008.0, 5.0)
    assert _active_plan(mv, cfg, status).accepted


def test_hovering_peer_does_not_refresh_without_new_position_stamp():
    clock = [10.0]
    mv = MembershipView(config={}, clock=lambda: clock[0])
    mv.update_from_peer("d1", 1.0, 50.0, 1.0, 2.0, 3.0, peer_position_stamp=9.0)
    clock[0] = 17.0
    mv.update_from_peer("d1", 2.0, 50.0, 1.0, 2.0, 3.0, peer_position_stamp=9.0)
    assert mv.nodes["d1"].last_position_time == 10.0


def test_partial_none_is_not_a_position_and_is_transient_in_active_mode(monkeypatch):
    _line_offset(monkeypatch)
    clock = [10.0]
    mv = MembershipView(config={}, clock=lambda: clock[0])
    mv.update_from_peer("d1", 1.0, 50.0, None, 1.0, 1.0, peer_position_stamp=9.0)
    assert mv.nodes["d1"].last_position_time is None
    assert mv.nodes["d1"].lat is None

    plan = _active_plan(
        mv, MockFlightCfg("active"),
        {"lat": 1.0, "lon": 1.0, "alt": 1.0, "gps_valid": True, "position_age": 0.0},
    )
    assert plan.reject_kind == RejectKind.TRANSIENT
    assert plan.reject_reason == "missing or invalid telemetry for d1"


def test_active_mode_rejects_partial_self_telemetry(monkeypatch):
    _line_offset(monkeypatch)
    mv = MembershipView(config={}, clock=lambda: 1.0)
    mv.update_from_peer("d1", 1.0, 50.0, 1.1, 1.0, 1.0, peer_position_stamp=1.0)
    plan = _active_plan(
        mv, MockFlightCfg("active"),
        {"lat": 1.0, "lon": None, "alt": 1.0, "gps_valid": True, "position_age": 0.0},
    )
    assert plan.reject_kind == RejectKind.TRANSIENT
    assert plan.reject_reason == "missing or invalid telemetry for d0"


def test_advisory_nominal_label_lists_d1_d3_and_keeps_real_self():
    mv = MembershipView(config={}, clock=lambda: 1.0)
    cfg = MockFlightCfg("advisory", dead_drone_obstacle="false")
    params = {"type": "SQUARE", "spacing": 15.0,
              "slot_assignments": {"d0": 0, "d1": 1, "d2": 2, "d3": 3}}

    plan = plan_healing(
        params, {"d0", "d1", "d3"}, "d0", "d0", "d0", cfg, mv,
        {"lat": 1.0, "lon": 1.0, "alt": 1.0, "gps_valid": True, "position_age": 0.0},
    )
    assert plan.accepted, plan.reject_reason
    assert plan.reason.endswith("(nominal positions used for d1,d3)")


def test_min_required_sep_uses_the_runtime_defaults(monkeypatch):
    import DroneOS.core.formation_engine as formation_engine
    from DroneOS.shared.config.models import CollisionAvoidanceConfig

    cfg = MockFlightCfg("advisory", heal_separation_margin_m=0.0,
                        heal_position_margin_m=0.0)
    cfg.formation = type("Formation", (), {})()
    cfg.collision_avoidance = type("Collision", (), {})()
    params = {"type": "LINE", "spacing": 7.0,
              "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}

    before_min_required_sep = max(8.0, CollisionAvoidanceConfig().min_horizontal_distance)
    before = plan_healing(params, {"d0", "d1"}, "d0", "d0", "d0", cfg)
    assert before_min_required_sep == 8.0
    assert "8.00" in before.reject_reason  # formation-engine default

    monkeypatch.setattr(formation_engine, "_DEFAULT_MIN_SEP_M", 4.0)
    cfg.collision_avoidance = CollisionAvoidanceConfig(min_horizontal_distance=11.0)
    after_min_required_sep = max(4.0, cfg.collision_avoidance.min_horizontal_distance)
    after = plan_healing(params, {"d0", "d1"}, "d0", "d0", "d0", cfg)
    assert after_min_required_sep == 11.0
    assert "11.00" in after.reject_reason  # collision default/config now wins


def test_settle_diagnostics_are_per_peer_and_rate_limited(caplog, monkeypatch):
    class Swarm:
        identity = type("Identity", (), {"drone_id": "d0"})()
        registry = MockSwarm().registry

    cfg = MockFlightCfg("active", enabled=True, reshape_on_follower_loss="true",
                        heal_after_dead_s=0.0, reslot_cooldown_s=0.0)
    mgr = CoordinationManager(Swarm(), cfg, clock=lambda: 0.0)
    mgr.membership.nodes["d1"] = type("Node", (), {"state": PeerState.DEAD, "dead_since": -20.0})()
    mgr.membership.nodes["d2"] = type("Node", (), {"state": PeerState.DEAD, "dead_since": -20.0})()
    q_ok = type("Quorum", (), {"name": "QUORUM"})()
    fp = {"type": "LINE", "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}

    d1 = HealPlan({}, "none", {}, 0.0, 0.0, False,
                  "PRECONDITION FAILED: not settled (d1 dist=5.00m > 3.00m)", RejectKind.TRANSIENT)
    d2 = HealPlan({}, "none", {}, 0.0, 0.0, False,
                  "PRECONDITION FAILED: not settled (d2 dist=4.00m > 3.00m)", RejectKind.TRANSIENT)
    responses = iter([d1, d1, d2, d1])
    monkeypatch.setattr("DroneOS.core.coordination.healing.plan_healing", lambda *_args, **_kwargs: next(responses))

    with caplog.at_level(logging.INFO, logger="CoordinationManager"):
        for now in (0.0, 1.0, 1.0, 6.0):
            mgr._handle_healing(now, q_ok, fp, {"d0", "d1"}, "d0", "d0")

    messages = [record.message for record in caplog.records if "settle:" in record.message]
    assert messages == [
        "[coord] settle: d1 dist=5.00m radius=3.00m",
        "[coord] settle: d2 dist=4.00m radius=3.00m",
        "[coord] settle: d1 dist=5.00m radius=3.00m",
    ]
