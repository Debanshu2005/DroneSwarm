import copy
import logging
import pytest
from DroneOS1.core.coordination.manager import CoordinationManager
from DroneOS1.core.coordination.membership import PeerState, MemberNode, MembershipView
from DroneOS1.core.coordination.quorum import QuorumState
from DroneOS1.core.coordination.healing import HealPlan, RejectKind
import DroneOS1.core.coordination.healing as healing_module


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
