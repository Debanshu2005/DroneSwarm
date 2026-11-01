import copy
import logging
import pytest
from DroneOS.core.coordination.manager import CoordinationManager
from DroneOS.core.coordination.membership import PeerState, MemberNode, MembershipView
from DroneOS.core.coordination.quorum import QuorumState
from DroneOS.core.coordination.healing import HealPlan, RejectKind
import DroneOS.core.coordination.healing as healing_module


class FakeClock:
    def __init__(self, start: float = 100.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float):
        self.now += dt


class MockSender:
    def __init__(self):
        self.calls = []
        self.return_value = True

    async def __call__(self, params, targets):
        self.calls.append((copy.deepcopy(params), list(targets)))
        return self.return_value


class MockIdentity:
    def __init__(self, drone_id: str):
        self.drone_id = drone_id


class MockRegistry:
    def __init__(self):
        self._peers = {}

    def get_all_peers(self):
        return list(self._peers.keys())

    def get_peer(self, pid):
        return self._peers.get(pid, None)


class MockSwarm:
    def __init__(self, drone_id: str = "drone1"):
        self.identity = MockIdentity(drone_id)
        self.registry = MockRegistry()
        self.network_cfg = type("NetCfg", (), {"heartbeat_interval": 1.0})()


class MockNode:
    def __init__(self, state=PeerState.ALIVE, dead_since=None, rejoin_stable_start=None, last_seen=100.0):
        self.state = state
        self.dead_since = dead_since
        self.rejoin_stable_start = rejoin_stable_start
        self.missed_beats = 0
        self.last_seen = last_seen
        self.last_heartbeat_time = last_seen
        self.battery_level = 100.0
        self.lat = 0.0
        self.lon = 0.0
        self.alt = 0.0
        self.last_position_time = last_seen


def mock_plan_compaction(current_params, healthy_members, current_anchor, prop_anchor, my_id, flight_cfg, membership_view=None, my_status=None):
    old_slots = current_params.get("slot_assignments", {})
    new_slots = {}
    if prop_anchor in healthy_members:
        new_slots[prop_anchor] = 0
    rem = sorted([p for p in healthy_members if p != prop_anchor], key=lambda x: old_slots.get(x, 99))
    next_s = 1
    for p in rem:
        new_slots[p] = next_s
        next_s += 1
    moves = {p: (old_slots[p], new_slots[p]) for p in healthy_members}
    return HealPlan(new_slots, "compaction", moves, 5.0, 10.0, accepted=True)


@pytest.mark.asyncio
async def test_follower_loss_active_armed_sends_update(monkeypatch):
    """1. Follower dies, reshape_on_follower_loss=true, mode=active, armed:
    formation update is sent with the follower removed and the anchor unchanged."""
    monkeypatch.setattr(healing_module, "plan_healing", mock_plan_compaction)
    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2}}
    cfg = type("Cfg", (), {
        "coordination": {
            "enabled": True, "mode": "active", "armed": True,
            "reshape_on_follower_loss": "true", "heal_after_dead_s": 10.0,
            "reslot_cooldown_s": 0.0, "quorum_required": False,
        },
        "formation": type("Form", (), {"min_formation_separation_m": 8.0})(),
        "collision_avoidance": type("CA", (), {"min_horizontal_distance": 2.0})(),
    })()

    mgr = CoordinationManager(swarm, cfg, clock=clock, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    mgr.my_id = "drone1"
    mgr.membership.nodes["drone1"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    mgr.membership.nodes["drone2"] = MockNode(PeerState.DEAD, dead_since=80.0, last_seen=80.0)
    mgr.membership.nodes["drone3"] = MockNode(PeerState.ALIVE, last_seen=100.0)

    healthy = {"drone1", "drone3"}
    my_status = {"gps_valid": True, "position_age": 0.0, "battery_level": 100.0, "lat": 0.0, "lon": 0.0, "alt": 0.0}

    await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, "drone1", "drone1", my_status)

    assert len(sender.calls) == 1
    sent_params, targets = sender.calls[0]
    # Follower drone2 removed, anchor drone1 unchanged at slot 0
    assert sent_params["slot_assignments"] == {"drone1": 0, "drone3": 1}
    assert sent_params["slot_assignments"]["drone1"] == 0
    assert "drone2" not in sent_params["slot_assignments"]
    assert targets == ["drone3"]


@pytest.mark.asyncio
async def test_follower_loss_flag_false_holds(monkeypatch):
    """2. Same with the flag false: HOLD, nothing sent."""
    monkeypatch.setattr(healing_module, "plan_healing", mock_plan_compaction)
    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2}}
    cfg = type("Cfg", (), {
        "coordination": {
            "enabled": True, "mode": "active", "armed": True,
            "reshape_on_follower_loss": "false", "heal_after_dead_s": 10.0,
            "reslot_cooldown_s": 0.0, "quorum_required": False,
        },
        "formation": type("Form", (), {"min_formation_separation_m": 8.0})(),
        "collision_avoidance": type("CA", (), {"min_horizontal_distance": 2.0})(),
    })()

    mgr = CoordinationManager(swarm, cfg, clock=clock, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    mgr.my_id = "drone1"
    mgr.membership.nodes["drone1"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    mgr.membership.nodes["drone2"] = MockNode(PeerState.DEAD, dead_since=80.0, last_seen=80.0)
    mgr.membership.nodes["drone3"] = MockNode(PeerState.ALIVE, last_seen=100.0)

    healthy = {"drone1", "drone3"}
    my_status = {"gps_valid": True, "position_age": 0.0, "battery_level": 100.0, "lat": 0.0, "lon": 0.0, "alt": 0.0}

    await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, "drone1", "drone1", my_status)

    assert len(sender.calls) == 0
    assert "drone2" in mgr._hold_logged


@pytest.mark.asyncio
async def test_follower_loss_advisory_mode_logs_and_sends_nothing(caplog, monkeypatch):
    """3. Advisory mode: logs 'would broadcast', sends nothing."""
    monkeypatch.setattr(healing_module, "plan_healing", mock_plan_compaction)
    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2}}
    cfg = type("Cfg", (), {
        "coordination": {
            "enabled": True, "mode": "advisory", "armed": False,
            "reshape_on_follower_loss": "true", "heal_after_dead_s": 10.0,
            "reslot_cooldown_s": 0.0, "quorum_required": False,
        },
        "formation": type("Form", (), {"min_formation_separation_m": 8.0})(),
        "collision_avoidance": type("CA", (), {"min_horizontal_distance": 2.0})(),
    })()

    mgr = CoordinationManager(swarm, cfg, clock=clock, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    mgr.my_id = "drone1"
    mgr.membership.nodes["drone1"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    mgr.membership.nodes["drone2"] = MockNode(PeerState.DEAD, dead_since=80.0, last_seen=80.0)
    mgr.membership.nodes["drone3"] = MockNode(PeerState.ALIVE, last_seen=100.0)

    healthy = {"drone1", "drone3"}
    my_status = {"gps_valid": True, "position_age": 0.0, "battery_level": 100.0, "lat": 0.0, "lon": 0.0, "alt": 0.0}

    with caplog.at_level(logging.INFO, logger="CoordinationManager"):
        await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, "drone1", "drone1", my_status)

    assert len(sender.calls) == 0
    assert any("would broadcast FORMATION_UPDATE" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_follower_stabilizing_not_healed(monkeypatch):
    """4. Follower returns and is stabilizing (DEAD with rejoin_stable_start set, heartbeats flowing):
    no heal is sent."""
    monkeypatch.setattr(healing_module, "plan_healing", mock_plan_compaction)
    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2}}
    cfg = type("Cfg", (), {
        "coordination": {
            "enabled": True, "mode": "active", "armed": True,
            "reshape_on_follower_loss": "true", "heal_after_dead_s": 10.0,
            "reslot_cooldown_s": 0.0, "quorum_required": False,
        },
        "formation": type("Form", (), {"min_formation_separation_m": 8.0})(),
        "collision_avoidance": type("CA", (), {"min_horizontal_distance": 2.0})(),
    })()

    mgr = CoordinationManager(swarm, cfg, clock=clock, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    mgr.my_id = "drone1"
    mgr.membership.nodes["drone1"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    # drone2 is DEAD, but has rejoin_stable_start set (heartbeats flowing)
    mgr.membership.nodes["drone2"] = MockNode(PeerState.DEAD, dead_since=80.0, rejoin_stable_start=95.0, last_seen=100.0)
    mgr.membership.nodes["drone3"] = MockNode(PeerState.ALIVE, last_seen=100.0)

    healthy = {"drone1", "drone3"}
    my_status = {"gps_valid": True, "position_age": 0.0, "battery_level": 100.0, "lat": 0.0, "lon": 0.0, "alt": 0.0}

    await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, "drone1", "drone1", my_status)

    assert len(sender.calls) == 0


@pytest.mark.asyncio
async def test_follower_dies_recovers_dies_again_healed_twice(monkeypatch):
    """5. Follower dies, recovers to ALIVE, dies again with the same formation:
    it is healed on the second death (memo cleared, dead_since reset)."""
    monkeypatch.setattr(healing_module, "plan_healing", mock_plan_compaction)
    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2}}
    cfg = type("Cfg", (), {
        "coordination": {
            "enabled": True, "mode": "active", "armed": True,
            "reshape_on_follower_loss": "true", "heal_after_dead_s": 10.0,
            "reslot_cooldown_s": 0.0, "quorum_required": False,
        },
        "formation": type("Form", (), {"min_formation_separation_m": 8.0})(),
        "collision_avoidance": type("CA", (), {"min_horizontal_distance": 2.0})(),
    })()

    mgr = CoordinationManager(swarm, cfg, clock=clock, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    mgr.my_id = "drone1"
    mgr.membership.nodes["drone1"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    mgr.membership.nodes["drone2"] = MockNode(PeerState.DEAD, dead_since=80.0, last_seen=80.0)
    mgr.membership.nodes["drone3"] = MockNode(PeerState.ALIVE, last_seen=100.0)

    healthy = {"drone1", "drone3"}
    my_status = {"gps_valid": True, "position_age": 0.0, "battery_level": 100.0, "lat": 0.0, "lon": 0.0, "alt": 0.0}

    # First death -> healed
    await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, "drone1", "drone1", my_status)
    assert len(sender.calls) == 1

    # Now drone2 recovers to ALIVE via membership
    clock.advance(10.0)
    mgr.clear_peer_recovery_state("drone2")
    mgr.membership.nodes["drone2"].state = PeerState.ALIVE
    mgr.membership.nodes["drone2"].dead_since = None
    assert len(mgr._memoized_plans) == 0

    # Drone2 dies again
    clock.advance(50.0)
    now2 = clock()
    mgr.membership.nodes["drone2"].state = PeerState.DEAD
    mgr.membership.nodes["drone2"].dead_since = now2 - 20.0  # dead for 20s

    await mgr._handle_healing(now2, QuorumState.QUORUM, copy.deepcopy(fp), healthy, "drone1", "drone1", my_status)
    assert len(sender.calls) == 2


def test_flapping_peer_one_heartbeat_does_not_make_alive():
    """6. Flapping peer: one heartbeat after a long silence does NOT make a DEAD peer ALIVE."""
    clock = FakeClock(100.0)
    view = MembershipView({"rejoin_stable_s": 10.0}, clock=clock)

    node = MemberNode("drone2", clock=clock)
    node.state = PeerState.DEAD
    node.last_heartbeat_time = 50.0
    view.nodes["drone2"] = node

    # A single heartbeat arrives at t=100.0
    view.update_from_peer("drone2", last_seen=100.0, battery=90.0, lat=0.0, lon=0.0, alt=0.0)
    assert node.state == PeerState.DEAD
    assert node.rejoin_stable_start == 100.0

    # Heartbeats stop! Advance time by 2.0s (> 1.5 * hb_interval)
    clock.advance(2.0)  # t=102.0
    view.evaluate_tick(1.0)
    assert node.rejoin_stable_start is None

    # Advance time to t=111.0 (> 10s after first heartbeat)
    clock.advance(9.0)  # t=111.0
    # A single heartbeat arrives now
    view.update_from_peer("drone2", last_seen=111.0, battery=90.0, lat=0.0, lon=0.0, alt=0.0)
    # Must NOT become ALIVE! It must restart stabilization from 111.0
    assert node.state == PeerState.DEAD
    assert node.rejoin_stable_start == 111.0


@pytest.mark.asyncio
async def test_returned_after_heal_warning_fires_exactly_once(caplog):
    """7. Returned-after-heal warning fires exactly once."""
    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"drone1": 0, "drone3": 1}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = type("Cfg", (), {
        "coordination": {
            "enabled": True, "mode": "active", "armed": True,
            "quorum_required": False,
        },
        "formation": type("Form", (), {"min_formation_separation_m": 8.0})(),
        "collision_avoidance": type("CA", (), {"min_horizontal_distance": 2.0})(),
    })()

    mgr = CoordinationManager(swarm, cfg, clock=clock, formation_provider=lambda: fp, self_status_provider=lambda: sp)
    mgr._excluded_members = {"drone2"}
    mgr.membership.nodes["drone1"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    mgr.membership.nodes["drone3"] = MockNode(PeerState.ALIVE, last_seen=100.0)
    # drone2 is ALIVE in membership and not in slot_assignments
    mgr.membership.nodes["drone2"] = MockNode(PeerState.ALIVE, last_seen=100.0)

    import asyncio
    with caplog.at_level(logging.WARNING, logger="CoordinationManager"):
        async def stop():
            await asyncio.sleep(0.3)
            mgr.is_running = False

        mgr.is_running = True
        await asyncio.gather(mgr.run(), stop())

    warn_logs = [r.message for r in caplog.records if "returned after heal" in r.message]
    assert len(warn_logs) == 1
    assert "drone drone2 returned after heal; operator must re-issue the formation to re-add it" in warn_logs[0]


def test_real_plan_healing_all_follower_losses():
    """8. Real plan_healing without mocks: loss of drone2, drone3, or drone4 in 4-drone V-formation."""
    import math
    from DroneOS.core.coordination.healing import plan_healing, _get_slot_offset
    from DroneOS.core.coordination.membership import MembershipView, MemberNode, PeerState

    cfg = type('Cfg', (), {
        'coordination': {
            'enabled': True, 'mode': 'active', 'armed': True,
            'reshape_on_follower_loss': 'true', 'dead_drone_obstacle': 'true',
            'heal_settled_radius_m': 3.0,
        },
        'formation': type('Form', (), {'min_formation_separation_m': 3.0})(),
        'collision_avoidance': type('CA', (), {'min_horizontal_distance': 4.0})()
    })()

    ref_lat, ref_lon = 47.641468, -122.140165

    # Test Drone 3 loss
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2, 'drone4': 3}}
    healthy3 = {'drone1', 'drone2', 'drone4'}
    view3 = MembershipView({}, lambda: 100.0)
    for d, s in fp['slot_assignments'].items():
        off = _get_slot_offset('V', s, 15.0, 4)
        lat = ref_lat + off[0] / 111319.5
        lon = ref_lon + off[1] / (111319.5 * math.cos(math.radians(ref_lat)))
        n = MemberNode(d)
        n.lat, n.lon, n.alt = lat, lon, 10.0
        n.last_position_time = 100.0
        n.state = PeerState.ALIVE if d in healthy3 else PeerState.DEAD
        view3.nodes[d] = n
    my_status = {'lat': ref_lat, 'lon': ref_lon, 'alt': 10.0, 'position_age': 0.1, 'gps_valid': True}
    plan3 = plan_healing(fp, healthy3, 'drone1', 'drone1', 'drone1', cfg, view3, my_status)
    assert plan3 is not None
    assert plan3.accepted is True
    assert plan3.slot_assignments == {'drone1': 0, 'drone2': 1, 'drone4': 2}

    # Test Drone 2 loss
    healthy2 = {'drone1', 'drone3', 'drone4'}
    view2 = MembershipView({}, lambda: 100.0)
    for d, s in fp['slot_assignments'].items():
        off = _get_slot_offset('V', s, 15.0, 4)
        lat = ref_lat + off[0] / 111319.5
        lon = ref_lon + off[1] / (111319.5 * math.cos(math.radians(ref_lat)))
        n = MemberNode(d)
        n.lat, n.lon, n.alt = lat, lon, 10.0
        n.last_position_time = 100.0
        n.state = PeerState.ALIVE if d in healthy2 else PeerState.DEAD
        view2.nodes[d] = n
    plan2 = plan_healing(fp, healthy2, 'drone1', 'drone1', 'drone1', cfg, view2, my_status)
    assert plan2 is not None
    assert plan2.accepted is True
    assert plan2.slot_assignments == {'drone1': 0, 'drone4': 1, 'drone3': 2}

    # Test Drone 4 loss
    healthy4 = {'drone1', 'drone2', 'drone3'}
    view4 = MembershipView({}, lambda: 100.0)
    for d, s in fp['slot_assignments'].items():
        off = _get_slot_offset('V', s, 15.0, 4)
        lat = ref_lat + off[0] / 111319.5
        lon = ref_lon + off[1] / (111319.5 * math.cos(math.radians(ref_lat)))
        n = MemberNode(d)
        n.lat, n.lon, n.alt = lat, lon, 10.0
        n.last_position_time = 100.0
        n.state = PeerState.ALIVE if d in healthy4 else PeerState.DEAD
        view4.nodes[d] = n
    plan4 = plan_healing(fp, healthy4, 'drone1', 'drone1', 'drone1', cfg, view4, my_status)
    assert plan4 is not None
    assert plan4.accepted is True
    assert plan4.slot_assignments == {'drone1': 0, 'drone2': 1, 'drone3': 2}

@pytest.mark.asyncio
@pytest.mark.parametrize("spacing", [10.0, 12.0, 15.0, 20.0])
@pytest.mark.parametrize("killed_drone", ["drone2", "drone3", "drone4"])
async def test_real_plan_healing_follower_loss_sends_update(spacing, killed_drone):
    """Regression test: Real plan_healing with DRONEOS_PROFILE=test config.
    Asserts FORMATION_UPDATE is sent with exactly the 3 survivors and anchor at slot 0."""
    import math
    import os
    from pathlib import Path
    from DroneOS.core.coordination.healing import _get_slot_offset
    from DroneOS.core.coordination.membership import PeerState, MemberNode
    from DroneOS.core.coordination.quorum import QuorumState
    from DroneOS.shared.config.models import FlightConfig
    from DroneOS.shared.config.profile import resolve_flight_config

    os.environ["DRONEOS_PROFILE"] = "test"
    config_dir = Path(__file__).resolve().parents[1] / "configs"
    flight_cfg = resolve_flight_config(config_dir, FlightConfig)

    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {
        "type": "V",
        "spacing": spacing,
        "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}
    }
    all_drones = ["drone1", "drone2", "drone3", "drone4"]
    survivors = {d for d in all_drones if d != killed_drone}

    mgr = CoordinationManager(
        swarm, flight_cfg, clock=clock,
        formation_provider=lambda: copy.deepcopy(fp),
        formation_update_sender=sender
    )
    mgr.my_id = "drone1"

    ref_lat, ref_lon = 47.641468, -122.140165
    for d, s in fp["slot_assignments"].items():
        off = _get_slot_offset("V", s, spacing, 4)
        lat = ref_lat + off[0] / 111319.5
        lon = ref_lon + off[1] / (111319.5 * math.cos(math.radians(ref_lat)))
        node = MemberNode(d, clock=clock)
        node.lat, node.lon, node.alt = lat, lon, 10.0
        node.last_position_time = 100.0
        if d in survivors:
            node.state = PeerState.ALIVE
            node.last_seen = 100.0
            node.last_heartbeat_time = 100.0
            node.battery_level = 100.0
        else:
            node.state = PeerState.DEAD
            node.dead_since = 80.0
            node.last_seen = 80.0
            node.last_heartbeat_time = 80.0
            node.battery_level = 100.0
        mgr.membership.nodes[d] = node

    my_status = {
        "lat": ref_lat, "lon": ref_lon, "alt": 10.0,
        "position_age": 0.0, "gps_valid": True, "battery_level": 100.0
    }

    await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), survivors, "drone1", "drone1", my_status)

    assert len(sender.calls) == 1
    new_params, targets = sender.calls[0]
    new_slots = new_params["slot_assignments"]
    assert set(new_slots.keys()) == survivors
    assert new_slots["drone1"] == 0
    assert set(new_slots.values()) == {0, 1, 2}


@pytest.mark.asyncio
async def test_real_plan_healing_follower_loss_reshape_false_holds(caplog):
    """Test follower loss when reshape_on_follower_loss=false: asserts HOLD and sends nothing."""
    import logging
    import math
    import os
    from pathlib import Path
    from DroneOS.core.coordination.healing import _get_slot_offset
    from DroneOS.core.coordination.membership import PeerState, MemberNode
    from DroneOS.core.coordination.quorum import QuorumState
    from DroneOS.shared.config.models import FlightConfig
    from DroneOS.shared.config.profile import resolve_flight_config

    os.environ["DRONEOS_PROFILE"] = "test"
    config_dir = Path(__file__).resolve().parents[1] / "configs"
    flight_cfg = resolve_flight_config(config_dir, FlightConfig)
    flight_cfg.coordination["reshape_on_follower_loss"] = "false"

    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    fp = {
        "type": "V",
        "spacing": 15.0,
        "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}
    }
    survivors = {"drone1", "drone2", "drone4"}

    mgr = CoordinationManager(
        swarm, flight_cfg, clock=clock,
        formation_provider=lambda: copy.deepcopy(fp),
        formation_update_sender=sender
    )
    mgr.my_id = "drone1"

    ref_lat, ref_lon = 47.641468, -122.140165
    for d, s in fp["slot_assignments"].items():
        off = _get_slot_offset("V", s, 15.0, 4)
        lat = ref_lat + off[0] / 111319.5
        lon = ref_lon + off[1] / (111319.5 * math.cos(math.radians(ref_lat)))
        node = MemberNode(d, clock=clock)
        node.lat, node.lon, node.alt = lat, lon, 10.0
        node.last_position_time = 100.0
        if d in survivors:
            node.state = PeerState.ALIVE
            node.last_seen = 100.0
            node.last_heartbeat_time = 100.0
            node.battery_level = 100.0
        else:
            node.state = PeerState.DEAD
            node.dead_since = 80.0
            node.last_seen = 80.0
            node.last_heartbeat_time = 80.0
            node.battery_level = 100.0
        mgr.membership.nodes[d] = node

    my_status = {
        "lat": ref_lat, "lon": ref_lon, "alt": 10.0,
        "position_age": 0.0, "gps_valid": True, "battery_level": 100.0
    }

    with caplog.at_level(logging.INFO, logger="CoordinationManager"):
        await mgr._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), survivors, "drone1", "drone1", my_status)

    assert len(sender.calls) == 0
    hold_logs = [r for r in caplog.records if "HOLD:" in r.message and "reshape_on_follower_loss=false" in r.message]
    assert len(hold_logs) == 1
@pytest.mark.asyncio
@pytest.mark.parametrize("shape_sequence,killed_drone", [
    (["V", "DIAMOND", "COLUMN"], "drone2"),
    (["V", "CIRCLE", "ECHELON_LEFT"], "drone2"),
    (["V", "SQUARE", "GRID"], "drone3"),
])
async def test_multi_shape_trigger_then_follower_loss_heals(shape_sequence, killed_drone):
    """Test switching through 2-3 shapes and then killing a follower drone:
    verifies that formation change tracking updates settle gate and heal succeeds."""
    import math
    import os
    from pathlib import Path
    from DroneOS.core.coordination.healing import _get_slot_offset
    from DroneOS.core.coordination.membership import PeerState, MemberNode
    from DroneOS.core.coordination.quorum import QuorumState
    from DroneOS.shared.config.models import FlightConfig
    from DroneOS.shared.config.profile import resolve_flight_config

    os.environ["DRONEOS_PROFILE"] = "test"
    config_dir = Path(__file__).resolve().parents[1] / "configs"
    flight_cfg = resolve_flight_config(config_dir, FlightConfig)

    clock = FakeClock(100.0)
    swarm = MockSwarm("drone1")
    sender = MockSender()

    current_fp = None
    mgr = CoordinationManager(
        swarm, flight_cfg, clock=clock,
        formation_provider=lambda: copy.deepcopy(current_fp) if current_fp else None,
        formation_update_sender=sender
    )
    mgr.my_id = "drone1"

    all_drones = ["drone1", "drone2", "drone3", "drone4"]
    ref_lat, ref_lon = 47.641468, -122.140165
    spacing = 10.0

    # Step 1: Cycle through shapes
    for shape in shape_sequence:
        clock.advance(10.0)
        now = clock()
        current_fp = {
            "type": shape,
            "spacing": spacing,
            "slot_assignments": {"drone1": 0, "drone2": 1, "drone3": 2, "drone4": 3}
        }
        for d, s in current_fp["slot_assignments"].items():
            off = _get_slot_offset(shape, s, spacing, 4)
            lat = ref_lat + off[0] / 111319.5
            lon = ref_lon + off[1] / (111319.5 * math.cos(math.radians(ref_lat)))
            node = mgr.membership.nodes.get(d)
            if not node:
                node = MemberNode(d, clock=clock)
                mgr.membership.nodes[d] = node
            node.lat, node.lon, node.alt = lat, lon, 10.0
            node.last_position_time = now
            node.state = PeerState.ALIVE
            node.last_seen = now
            node.last_heartbeat_time = now
            node.battery_level = 100.0

        my_status = {
            "lat": ref_lat, "lon": ref_lon, "alt": 10.0,
            "position_age": 0.0, "gps_valid": True, "battery_level": 100.0
        }
        await mgr._handle_healing(now, QuorumState.QUORUM, copy.deepcopy(current_fp), set(all_drones), "drone1", "drone1", my_status)

    # Step 2: Kill follower drone
    clock.advance(15.0)
    now = clock()
    survivors = {d for d in all_drones if d != killed_drone}

    dead_node = mgr.membership.nodes[killed_drone]
    dead_node.state = PeerState.DEAD
    dead_node.dead_since = now - 12.0

    for d in survivors:
        node = mgr.membership.nodes[d]
        node.last_seen = now
        node.last_position_time = now
        node.state = PeerState.ALIVE

    my_status = {
        "lat": ref_lat, "lon": ref_lon, "alt": 10.0,
        "position_age": 0.0, "gps_valid": True, "battery_level": 100.0
    }

    await mgr._handle_healing(now, QuorumState.QUORUM, copy.deepcopy(current_fp), survivors, "drone1", "drone1", my_status)

    assert len(sender.calls) == 1
    new_params, targets = sender.calls[0]
    new_slots = new_params["slot_assignments"]
    assert set(new_slots.keys()) == survivors
    assert new_slots["drone1"] == 0
    assert set(new_slots.values()) == {0, 1, 2}
