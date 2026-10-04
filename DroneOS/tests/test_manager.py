import pytest
import asyncio
from unittest.mock import MagicMock, patch
from DroneOS.core.coordination.manager import CoordinationManager
from DroneOS.core.swarm_manager import SwarmMembership, DroneIdentityManager
from DroneOS.core.coordination.quorum import QuorumState

class MockConfig:
    coordination = {"enabled": True, "quorum_required": True}

class MockIdentity:
    def __init__(self, drone_id):
        self.drone_id = drone_id

import time as _time_module

class MockPeerState:
    """Minimal peer state that satisfies is_healthy() comparisons."""
    def __init__(self, t=None):
        self.battery_level = 90.0
        self.last_position_time = t or _time_module.monotonic()
        self.lat, self.lon, self.alt = 0.0, 0.0, 0.0
        self.last_seen = self.last_position_time

class MockRegistry:
    def __init__(self):
        self._peers = {}
    def get_all_peers(self):
        return list(self._peers.keys())
    def get_peer(self, pid):
        return self._peers.get(pid, MockPeerState())

class MockSwarmManager:
    def __init__(self, drone_id):
        self.identity = MockIdentity(drone_id)
        self.registry = MockRegistry()

@pytest.mark.asyncio
async def test_manager_tick_with_fake_providers():
    swarm = MockSwarmManager("d1")
    
    # Fake providers
    fp = {"slot_assignments": {"d1": 0, "d2": 1}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    
    mgr = CoordinationManager(swarm, MockConfig(), heartbeat_interval=0.1, 
                              formation_provider=lambda: fp, 
                              self_status_provider=lambda: sp)
                              
    mgr.is_running = True
    mgr.enabled = True
    
    async def stop_soon():
        await asyncio.sleep(0.3)
        mgr.is_running = False
        
    await asyncio.gather(mgr.run(), stop_soon())
    
    # ISOLATED because d2 is not alive
    assert mgr.last_quorum_state == QuorumState.ISOLATED
    assert mgr.last_anchor == "d1"

@pytest.mark.asyncio
async def test_manager_tick_provider_crash():
    swarm = MockSwarmManager("d1")
    
    def crash_provider():
        raise ValueError("Oops")
        
    mgr = CoordinationManager(swarm, MockConfig(), heartbeat_interval=0.1, 
                              formation_provider=crash_provider, 
                              self_status_provider=crash_provider)
                              
    mgr.is_running = True
    mgr.enabled = True
    
    async def stop_soon():
        await asyncio.sleep(0.3)
        mgr.is_running = False
        
    await asyncio.gather(mgr.run(), stop_soon())
    
    # Provider crash caught and manager gracefully exits with is_running = False
    assert not mgr.is_running
    assert not mgr.enabled

@pytest.mark.asyncio
async def test_manager_healing_conditions():
    """
    Tests the manager's heal trigger conditions:
    - heal_after_dead_s gate: drone dead long enough -> heal fires
    - reslot_cooldown_s gate: too soon after last heal -> no re-heal
    - no dead drones in params -> no heal

    Uses V/3 drones with ANCHOR dead (d1 slot 0), my_id=d2 (prop_anchor).
    Anchor promotion: d2 takes slot 0 (zero travel), d3 stays slot 2.
    With heal_separation_margin_m=0 and heal_position_margin_m=0 the
    nominal positions are used and the plan is trivially accepted.
    """
    swarm = MockSwarmManager("d2")  # d2 becomes prop_anchor when d1 (anchor) dies

    import time
    start_time = time.monotonic()

    fp = {"type": "V", "spacing": 50.0, "slot_assignments": {"d1": 0, "d2": 1, "d3": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}

    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False, "heal_after_dead_s": 10.0,
                        "reslot_cooldown_s": 0.0, "dead_drone_obstacle": "false",
                        "reshape_on_follower_loss": "true", "heal_settled_radius_m": 99999.0,
                        "heal_separation_margin_m": 0.0, "heal_position_margin_m": 0.0}

    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp,
                              self_status_provider=lambda: sp)

    mgr.is_running = True
    mgr.enabled = True

    from DroneOS.core.coordination.quorum import PeerState
    class MockNode:
        def __init__(self, state):
            self.state = state
            self.dead_since = None
            self.last_seen = start_time
            self.last_heartbeat_time = start_time
            self.battery_level = 90.0
            self.lat, self.lon, self.alt = 0.0, 0.0, 0.0

    mgr.membership.nodes["d1"] = MockNode(PeerState.DEAD)
    mgr.membership.nodes["d3"] = MockNode(PeerState.ALIVE)

    mgr.membership.nodes["d1"].dead_since = start_time - 11.0  # DEAD for >10s
    mgr._last_slot_change_time = start_time - 20.0  # past cooldown

    async def stop_soon():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop_soon())

    assert len(mgr._memoized_plans) == 1, "Expected heal to fire"

    # Tick 2: reslot_cooldown still active (set it large after the heal)
    mgr.is_running = True
    mgr._last_slot_change_time = time.monotonic()  # simulates cooldown window
    mgr.config_dict["reslot_cooldown_s"] = 9999.0

    async def stop_soon2():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop_soon2())

    assert len(mgr._memoized_plans) == 1, "Cooldown must block re-heal"

    # Tick 3: no dead drones -> no heal
    mgr.is_running = True
    mgr._last_heal_time = start_time - 20.0
    fp["slot_assignments"] = {"d2": 0}  # clean 1-drone formation
    async def stop_soon3():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop_soon3())

    assert len(mgr._memoized_plans) == 1, "No dead drones must not trigger heal"

@pytest.mark.asyncio
async def test_manager_second_healer_no_standdown():
    # d1 healed recently, then d1 dies. d2 becomes prop_anchor.
    # d2 should NOT stand down because d2's local _last_heal_time is 0.
    swarm = MockSwarmManager("d2")
    import time
    start_time = time.monotonic()
    fp = {"type": "V", "spacing": 20.0, "slot_assignments": {"d1": 0, "d2": 1, "d3": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False, "heal_after_dead_s": 10.0, "reslot_cooldown_s": 0.0, "dead_drone_obstacle": "false", "reshape_on_follower_loss": "true", "heal_settled_radius_m": 99999.0}
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp,
                              self_status_provider=lambda: sp)
    mgr.is_running = True
    mgr.enabled = True
    
    from DroneOS.core.coordination.quorum import PeerState
    class MockNode:
        def __init__(self, state):
            self.state = state
            self.dead_since = start_time - 11.0
            self.last_seen = start_time
            self.last_heartbeat_time = start_time
            self.battery_level = 90.0
            self.lat, self.lon, self.alt = 0.0, 0.0, 0.0
            
    # d1 is dead, d2 is us, d3 is alive
    mgr.membership.nodes["d1"] = MockNode(PeerState.DEAD)
    mgr.membership.nodes["d3"] = MockNode(PeerState.ALIVE)
    mgr._last_slot_change_time = start_time - 200.0  # far past any cooldown
    
    # Run loop
    async def stop_soon():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop_soon())
    
    assert len(mgr._memoized_plans) == 1

# ────────────────────────────────────────────────────────────────────────────
# Test (a): settle gate – new anchor dies inside cooldown; must wait before replanning
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manager_settle_gate_waits_for_cooldown():
    """After a heal, _last_slot_change_time is updated. If the new anchor then dies
    within the cooldown window, no replanning happens until cooldown elapses."""
    swarm = MockSwarmManager("d3")  # d3 is the survivor after both d1 and d2 die
    import time
    start_time = time.monotonic()

    fp = {"type": "V", "spacing": 20.0, "slot_assignments": {"d1": 0, "d2": 1, "d3": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False,
                        "heal_after_dead_s": 0.0, "reslot_cooldown_s": 9999.0,
                        "dead_drone_obstacle": "false", "max_heals_per_session": 10}
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp,
                              self_status_provider=lambda: sp)
    mgr.is_running = True
    mgr.enabled = True

    # Simulate: slot_assignments last changed 1s ago (inside cooldown)
    mgr._last_slot_change_time = start_time - 1.0
    mgr._last_slot_assignments = dict(fp["slot_assignments"])

    from DroneOS.core.coordination.quorum import PeerState
    class MockNode:
        def __init__(self, state):
            self.state = state
            self.dead_since = start_time - 5.0
            self.last_seen = start_time
            self.last_heartbeat_time = start_time
            self.battery_level = 90.0
            self.lat, self.lon, self.alt = 0.0, 0.0, 0.0

    mgr.membership.nodes["d1"] = MockNode(PeerState.DEAD)
    mgr.membership.nodes["d2"] = MockNode(PeerState.DEAD)

    async def stop_soon():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop_soon())

    assert len(mgr._memoized_plans) == 0

# ────────────────────────────────────────────────────────────────────────────
# Test (b): heal cap – third heal beyond max_heals_per_session produces no plan
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manager_heal_cap_blocks_after_max():
    """After max_heals_per_session heals, further healing is disabled."""
    swarm = MockSwarmManager("d1")
    import time
    start_time = time.monotonic()

    fp = {"type": "V", "spacing": 20.0, "slot_assignments": {"d1": 0, "d2": 1, "d3": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False,
                        "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0,
                        "dead_drone_obstacle": "false", "max_heals_per_session": 2}
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp,
                              self_status_provider=lambda: sp)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = start_time - 10.0  # well past cooldown

    from DroneOS.core.coordination.quorum import PeerState
    class MockNode:
        def __init__(self):
            self.state = PeerState.DEAD
            self.dead_since = start_time - 20.0
            self.last_seen = start_time
            self.last_heartbeat_time = start_time
            self.battery_level = 90.0
            self.lat, self.lon, self.alt = 0.0, 0.0, 0.0
    mgr.membership.nodes["d2"] = MockNode()

    swarm.registry.get_peer = lambda pid: type('P', (), {
        'battery_level': 90.0, 'last_position_time': start_time, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})()

    # Pre-exhaust the cap
    mgr._heal_count = 2  # already at max

    async def stop_soon():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop_soon())

    assert mgr._heals_disabled is True
    # _last_heal_time must not have advanced (no heal fired)
    assert mgr._last_heal_time == 0

# ────────────────────────────────────────────────────────────────────────────
# Test (c): HOLD outcome – never broadcasts, logs only once
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manager_hold_does_not_broadcast_and_logs_once(caplog):
    """When plan_healing returns is_hold=True, the manager must NOT update
    _last_slot_change_time, must NOT increment _heal_count, and must log
    the HOLD message exactly once even across multiple ticks."""
    import logging
    swarm = MockSwarmManager("d0")
    import time
    start_time = time.monotonic()

    # COLUMN slot-3 dead: d3 is the last drone. Compaction and tail_fill produce
    # identical assignments where d0/d1/d2 stay in slots 0/1/2 = no-op = HOLD.
    fp = {"type": "COLUMN", "spacing": 20.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2, "d3": 3}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False,
                        "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0,
                        "dead_drone_obstacle": "false", "max_heals_per_session": 10}
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp,
                              self_status_provider=lambda: sp)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = start_time - 10.0  # past cooldown

    from DroneOS.core.coordination.quorum import PeerState
    mgr.membership.nodes["d3"] = type('N', (), {
        'state': PeerState.DEAD, 'dead_since': start_time - 20.0,
        'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
    mgr.membership.nodes["d1"] = type('N', (), {
        'state': PeerState.ALIVE, 'dead_since': None,
        'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
    mgr.membership.nodes["d2"] = type('N', (), {
        'state': PeerState.ALIVE, 'dead_since': None,
        'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
    swarm.registry.get_peer = lambda pid: type('P', (), {
        'battery_level': 90.0, 'last_position_time': start_time, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})()

    with caplog.at_level(logging.INFO, logger="CoordinationManager"):
        # First tick
        async def stop1():
            await asyncio.sleep(0.15)
            mgr.is_running = False
        await asyncio.gather(mgr.run(), stop1())

        hold_logs_first = [r for r in caplog.records if "HOLD" in r.message]
        # Reset entire MembershipView so second run sees identical state (no new suspects)
        import time as _t
        _now = _t.monotonic()
        mgr.membership.nodes.clear()
        mgr.membership.nodes["d3"] = type('N', (), {
            'state': PeerState.DEAD, 'dead_since': _now - 20.0,
            'last_seen': _now, 'last_heartbeat_time': _now, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
        mgr.membership.nodes["d1"] = type('N', (), {
            'state': PeerState.ALIVE, 'dead_since': None,
            'last_seen': _now, 'last_heartbeat_time': _now, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
        mgr.membership.nodes["d2"] = type('N', (), {
            'state': PeerState.ALIVE, 'dead_since': None,
            'last_seen': _now, 'last_heartbeat_time': _now, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
        mgr.is_running = True

        # Second tick – HOLD must not be re-logged
        async def stop2():
            await asyncio.sleep(0.15)
            mgr.is_running = False
        await asyncio.gather(mgr.run(), stop2())

    hold_logs_all = [r for r in caplog.records if "HOLD" in r.message]

    assert len(hold_logs_first) == 1, "HOLD must be logged on first tick"
    assert len(hold_logs_all) == 1, "HOLD must not be logged again on second tick"
    assert mgr._heal_count == 0, "HOLD must not increment heal count"
    assert mgr._last_heal_time == 0, "HOLD must not update _last_heal_time"

# ────────────────────────────────────────────────────────────────────────────
# Test (d): NO_PLAN outcome – logs once, never broadcasts
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manager_no_plan_does_not_broadcast_and_logs_once(caplog):
    """When plan_healing returns NO_PLAN (accepted=False, is_hold=False), it must
    log exactly once, not broadcast, and not increment heal count."""
    import logging
    swarm = MockSwarmManager("d0")  # d0 is anchor, plans for d1 (follower) dead
    import time
    start_time = time.monotonic()

    # LINE slot-1 dead (d1 follower). spacing=2.0 < min_sep floor → all strategies rejected → NO_PLAN.
    fp = {"type": "LINE", "spacing": 2.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False,
                        "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0,
                        "dead_drone_obstacle": "true", "max_heals_per_session": 10,
                        "reshape_on_follower_loss": "true"}
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp,
                              self_status_provider=lambda: sp)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = start_time - 10.0

    from DroneOS.core.coordination.quorum import PeerState
    mgr.membership.nodes["d1"] = type('N', (), {'state': PeerState.DEAD, 'dead_since': start_time - 20.0, 'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.00001, 'lon': 0.00001})()
    mgr.membership.nodes["d2"] = type('N', (), {'state': PeerState.ALIVE, 'dead_since': None, 'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.00002, 'lon': 0.00002})()

    with caplog.at_level(logging.INFO, logger="CoordinationManager"):
        async def stop1():
            await asyncio.sleep(0.15)
            mgr.is_running = False
        await asyncio.gather(mgr.run(), stop1())
        noplan_logs_first = [r for r in caplog.records if "NO_PLAN:" in r.message]
        
        mgr.is_running = True
        async def stop2():
            await asyncio.sleep(0.15)
            mgr.is_running = False
        await asyncio.gather(mgr.run(), stop2())
        noplan_logs_all = [r for r in caplog.records if "NO_PLAN:" in r.message]

    assert len(noplan_logs_first) == 1, "NO_PLAN must be logged on first tick"
    assert len(noplan_logs_all) == 1, "NO_PLAN must not be logged again"
    assert mgr._heal_count == 0

# ────────────────────────────────────────────────────────────────────────────
# Test (e): reshape_on_follower_loss=false blocks follower healing
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manager_reshape_on_follower_loss_false(caplog):
    import logging
    swarm = MockSwarmManager("d0")
    import time
    start_time = time.monotonic()

    # V slot-1 dead (follower). Should trigger HOLD immediately.
    fp = {"type": "V", "spacing": 20.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": False,
                        "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0,
                        "reshape_on_follower_loss": "false"}
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1,
                              formation_provider=lambda: fp, self_status_provider=lambda: sp)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = start_time - 10.0

    from DroneOS.core.coordination.quorum import PeerState
    mgr.membership.nodes["d1"] = type('N', (), {'state': PeerState.DEAD, 'dead_since': start_time - 20.0, 'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()
    mgr.membership.nodes["d2"] = type('N', (), {'state': PeerState.ALIVE, 'dead_since': None, 'last_seen': start_time, 'last_heartbeat_time': start_time, 'battery_level': 90.0, 'lat': 0.0, 'lon': 0.0})()

    with caplog.at_level(logging.INFO, logger="CoordinationManager"):
        async def stop():
            await asyncio.sleep(0.15)
            mgr.is_running = False
        await asyncio.gather(mgr.run(), stop())

    hold_logs = [r for r in caplog.records if "follower died, reshape_on_follower_loss=false" in r.message]
    assert len(hold_logs) == 1
    assert mgr._heal_count == 0

@pytest.mark.asyncio
async def test_manager_default_v_anchor_dead_accepted():
    swarm = MockSwarmManager("d1")
    fake_time = 1000.0
    def fake_clock(): return fake_time
    
    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2, "d3": 3}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": "true", "dead_drone_obstacle": "true", "heal_settled_radius_m": 3.0, "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0, "mode": "active"}
    class FlightCfg:
        class formation:
            min_formation_separation_m = 8.0
        class collision_avoidance:
            min_horizontal_distance = 2.0
    cfg.formation = FlightCfg.formation
    cfg.collision_avoidance = FlightCfg.collision_avoidance
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1, formation_provider=lambda: fp, self_status_provider=lambda: sp, clock=fake_clock)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = fake_time - 10.0
    
    from DroneOS.core.coordination.quorum import PeerState
    from DroneOS.core.coordination.healing import _get_slot_offset
    from DroneOS.core.formation_manager import convert_local_offset_to_global
    
    # We must place d1, d2, d3 at exactly their nominal positions to pass settle gate
    for pid, slot in fp["slot_assignments"].items():
        rel = _get_slot_offset("V", slot, 15.0, 4)
        lat, lon, _ = convert_local_offset_to_global(0.0, 0.0, 0.0, rel[0], rel[1])
        state = PeerState.DEAD if pid == "d0" else PeerState.ALIVE
        node = type('N', (), {'state': state, 'dead_since': fake_time - 10.0, 'last_seen': fake_time, 'last_heartbeat_time': fake_time, 'battery_level': 90.0, 'lat': lat, 'lon': lon, 'alt': 10.0})()
        mgr.membership.nodes[pid] = node
        
    async def stop():
        await asyncio.sleep(0.15)
        mgr.is_running = False
    await asyncio.gather(mgr.run(), stop())
    
    # Memoized plan should be stored
    print("MEMOIZED:", mgr._memoized_plans)
    assert len(mgr._memoized_plans) == 1
    memo_key = list(mgr._memoized_plans.keys())[0]
    assert memo_key[0] == ("d0",)
    plan_obj = list(mgr._memoized_plans.values())[0]
    assert plan_obj is not True
    assert plan_obj.accepted is True
    assert plan_obj.slot_assignments == {"d1": 0, "d2": 2, "d3": 1}

@pytest.mark.asyncio
async def test_manager_default_line_anchor_dead_noplan(caplog):
    import logging
    swarm = MockSwarmManager("d1")
    fake_time = 1000.0
    def fake_clock(): return fake_time
    
    fp = {"type": "LINE", "spacing": 15.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2, "d3": 3}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}
    
    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": "true", "dead_drone_obstacle": "true", "heal_settled_radius_m": 3.0, "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0, "mode": "active"}
    class FlightCfg:
        class formation:
            min_formation_separation_m = 8.0
        class collision_avoidance:
            min_horizontal_distance = 2.0
    cfg.formation = FlightCfg.formation
    cfg.collision_avoidance = FlightCfg.collision_avoidance
    
    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1, formation_provider=lambda: fp, self_status_provider=lambda: sp, clock=fake_clock)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = fake_time - 10.0
    
    from DroneOS.core.coordination.quorum import PeerState
    from DroneOS.core.coordination.healing import _get_slot_offset
    from DroneOS.core.formation_manager import convert_local_offset_to_global
    
    for pid, slot in fp["slot_assignments"].items():
        rel = _get_slot_offset("LINE", slot, 15.0, 4)
        lat, lon, _ = convert_local_offset_to_global(0.0, 0.0, 0.0, rel[0], rel[1])
        state = PeerState.DEAD if pid == "d0" else PeerState.ALIVE
        node = type('N', (), {'state': state, 'dead_since': fake_time - 10.0, 'last_seen': fake_time, 'last_heartbeat_time': fake_time, 'battery_level': 90.0, 'lat': lat, 'lon': lon, 'alt': 10.0})()
        mgr.membership.nodes[pid] = node
        
    with caplog.at_level(logging.WARNING, logger="CoordinationManager"):
        async def stop():
            await asyncio.sleep(0.35)
            mgr.is_running = False
        await asyncio.gather(mgr.run(), stop())
    
    warnings = [r.message for r in caplog.records if "anchor lost" in r.message]
    assert len(warnings) == 1
    assert mgr._heal_count == 0
    assert len(mgr._memoized_plans) == 1


@pytest.mark.asyncio
async def test_manager_recovery_from_transient():
    swarm = MockSwarmManager("d1")
    fake_time = 1000.0
    def fake_clock(): return fake_time

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}

    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": "true", "dead_drone_obstacle": "true", "heal_settled_radius_m": 3.0, "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0, "mode": "active"}
    class FlightCfg:
        class formation:
            min_formation_separation_m = 8.0
        class collision_avoidance:
            min_horizontal_distance = 2.0
    cfg.formation = FlightCfg.formation
    cfg.collision_avoidance = FlightCfg.collision_avoidance

    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1, formation_provider=lambda: fp, self_status_provider=lambda: sp, clock=fake_clock)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = fake_time - 10.0

    from DroneOS.core.coordination.quorum import PeerState
    from DroneOS.core.coordination.healing import _get_slot_offset
    from DroneOS.core.formation_manager import convert_local_offset_to_global

    # Initial: d2 is unsettled (far from nominal)
    for pid, slot in fp["slot_assignments"].items():
        rel = _get_slot_offset("V", slot, 15.0, 3)
        lat, lon, _ = convert_local_offset_to_global(0.0, 0.0, 0.0, rel[0], rel[1])
        if pid == "d2":
            lat += 0.01  # Far away
        state = PeerState.DEAD if pid == "d0" else PeerState.ALIVE
        node = type('N', (), {'state': state, 'dead_since': fake_time - 10.0, 'last_seen': fake_time, 'last_heartbeat_time': fake_time, 'battery_level': 90.0, 'lat': lat, 'lon': lon, 'alt': 10.0})()
        mgr.membership.nodes[pid] = node

    async def run_once():
        await asyncio.sleep(0.15)
        mgr.is_running = False

    await asyncio.gather(mgr.run(), run_once())
    assert len(mgr._memoized_plans) == 0  # Rejected due to unsettled
    
    # Now settle d2
    mgr.is_running = True
    rel = _get_slot_offset("V", 2, 15.0, 3)
    lat, lon, _ = convert_local_offset_to_global(0.0, 0.0, 0.0, rel[0], rel[1])
    mgr.membership.nodes["d2"].lat = lat
    mgr.membership.nodes["d2"].lon = lon
    
    async def run_again():
        await asyncio.sleep(0.15)
        mgr.is_running = False
        
    await asyncio.gather(mgr.run(), run_again())
@pytest.mark.asyncio
async def test_manager_real_membership_view_triggers_healing():
    swarm = MockSwarmManager("d1")
    fake_time = 1000.0
    def fake_clock(): return fake_time

    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"d0": 0, "d1": 1, "d2": 2}}
    sp = {"battery_level": 90.0, "gps_valid": True, "position_age": 1.0}

    cfg = MockConfig()
    cfg.coordination = {"enabled": True, "quorum_required": "true", "dead_drone_obstacle": "false", "heal_settled_radius_m": 3.0, "heal_after_dead_s": 0.0, "reslot_cooldown_s": 0.0, "mode": "active"}
    class FlightCfg:
        class formation:
            min_formation_separation_m = 8.0
        class collision_avoidance:
            min_horizontal_distance = 2.0
    cfg.formation = FlightCfg.formation
    cfg.collision_avoidance = FlightCfg.collision_avoidance

    mgr = CoordinationManager(swarm, cfg, heartbeat_interval=0.1, formation_provider=lambda: fp, self_status_provider=lambda: sp, clock=fake_clock)
    mgr.is_running = True
    mgr.enabled = True
    mgr._last_slot_change_time = fake_time - 10.0
    
    # Send a real telemetry update for d0 and d2
    mgr.membership.update_from_peer("d0", fake_time, 90.0, 0.0, 0.0, 10.0)
    mgr.membership.update_from_peer("d2", fake_time, 90.0, 0.0, 0.0, 10.0)
    
    # Tick MembershipView to make them ALIVE
    mgr.membership.evaluate_tick(0.1)
    
    # Manually transition d0 to DEAD
    from DroneOS.core.coordination.quorum import PeerState
    mgr.membership.nodes["d0"].state = PeerState.DEAD
    mgr.membership.nodes["d0"].dead_since = fake_time - 10.0
    
    async def run_once():
        await asyncio.sleep(0.15)
        mgr.is_running = False

    await asyncio.gather(mgr.run(), run_once())
    assert len(mgr._memoized_plans) == 1

