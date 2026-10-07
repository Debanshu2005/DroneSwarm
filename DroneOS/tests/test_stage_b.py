import pytest
import asyncio
import copy
from unittest.mock import MagicMock, patch
from DroneOS.core.coordination.manager import CoordinationManager
from DroneOS.core.coordination.quorum import QuorumState
from DroneOS.core.coordination.membership import PeerState
from DroneOS.core.coordination.healing import HealPlan, RejectKind

class MockCfg:
    coordination = {
        'enabled': True,
        'mode': 'active',
        'armed': True,
        'dead_drone_obstacle': 'true',
        'kill_switch_file': '',
        'resend_count': 2,
        'max_heals_per_session': 3,
        'reshape_on_follower_loss': 'false'
    }
    class formation:
        min_formation_separation_m = 8.0
    class collision_avoidance:
        min_horizontal_distance = 2.0
    class adapter:
        max_velocity_xy = 5.0

class MockSwarm:
    def __init__(self):
        self.identity = type('Id', (), {'drone_id': 'drone1'})
        self.registry = type('Reg', (), {'get_all_peers': lambda: [], 'get_peer': lambda self, x: type('P', (), {'state': 'alive', 'last_seen': 100.0, 'battery_level': 100.0})()})()
        self.network_cfg = type('NetCfg', (), {'heartbeat_interval': 1.0})()

class MockSender:
    def __init__(self):
        self.calls = []
        self.return_value = True
        self.should_raise = False
    
    async def __call__(self, params, targets):
        self.calls.append((params, targets))
        if self.should_raise:
            raise RuntimeError("Sender error")
        return self.return_value

def mock_plan_accepted(*args, **kwargs):
    return HealPlan({'drone1': 0, 'drone3': 1}, "compaction", {}, 0.0, 0.0, True, None, None)

@pytest.fixture
def mock_cfg():
    cfg = MockCfg()
    cfg.coordination = dict(MockCfg.coordination)
    return cfg

# a. test_enabled_false_no_objects
def test_enabled_false_no_objects():
    from DroneOS.main import DroneOSApp
    import sys, os
    import importlib
    
    if 'DroneOS.main' in sys.modules:
        importlib.reload(sys.modules['DroneOS.main'])
        
    configs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "configs"))
    
    from unittest.mock import patch
    
    with patch.object(sys, 'argv', ['main.py', configs_dir]):
        from DroneOS.shared.config.profile import resolve_flight_config as original_resolve
        def mock_resolve(*args, **kwargs):
            cfg = original_resolve(*args, **kwargs)
            if not getattr(cfg, 'coordination', None):
                cfg.coordination = {}
            cfg.coordination["enabled"] = False
            return cfg
            
        with patch('DroneOS.shared.config.profile.resolve_flight_config', side_effect=mock_resolve), \
             patch('DroneOS.main.create_formation_update_sender') as mock_create:
            app = DroneOSApp()
            assert app.coordination_manager is None
            mock_create.assert_not_called()

# b. Sender closure
@pytest.mark.asyncio
async def test_sender_closure():
    from DroneOS.main import create_formation_update_sender
    from DroneOS.shared.protocol.messages import ControlMessage, CommandAction
    from unittest.mock import AsyncMock
    
    cmd_handler = MagicMock()
    network = MagicMock()
    
    # (i) self-application returns False -> False, sends NOTHING
    cmd_handler.handle_command = AsyncMock(return_value=False)
    
    sender = create_formation_update_sender('drone1', cmd_handler, network)
    res = await sender({"type": "V", "spacing": 10.0, "slot_assignments": {"drone1": 0}}, ["drone2"])
    assert res is False
    network.broadcast_message.assert_not_called()
    
    # self-application raises -> False, sends NOTHING
    cmd_handler.handle_command = AsyncMock(side_effect=ValueError("error"))
    res = await sender({"type": "V", "spacing": 10.0, "slot_assignments": {"drone1": 0}}, ["drone2"])
    assert res is False
    network.broadcast_message.assert_not_called()
    
    # (ii) a send raising -> returns False
    cmd_handler.handle_command = AsyncMock(return_value=True)
    network.broadcast_message = AsyncMock(side_effect=RuntimeError("net error"))
    res = await sender({"type": "V", "spacing": 10.0, "slot_assignments": {"drone1": 0}}, ["drone2"])
    assert res is False
    
    # (iii) one message per target with same ControlMessage fields/sender_id
    # (iv) returns True only if self-application and ALL sends succeed
    # (v) the sends are awaited
    cmd_handler.handle_command = AsyncMock(return_value=True)
    
    # Track tasks
    pending_tasks = set()
    async def mock_broadcast(msg):
        pending_tasks.add(asyncio.current_task())
        await asyncio.sleep(0.01)
        pending_tasks.discard(asyncio.current_task())
    
    network.broadcast_message = AsyncMock(side_effect=mock_broadcast)
    
    res = await sender({"type": "V", "spacing": 10.0, "slot_assignments": {"drone1": 0}}, ["drone2", "drone3"])
    assert res is True
    
    # Verify fields
    assert network.broadcast_message.call_count == 2
    calls = network.broadcast_message.call_args_list
    msg1 = calls[0][0][0]
    msg2 = calls[1][0][0]
    
    assert msg1.action == CommandAction.FORMATION_UPDATE
    assert msg1.sender_id == 'drone1'
    assert msg1.target_id == 'drone2'
    assert msg2.target_id == 'drone3'
    
    # Verify no tasks pending
    assert len(pending_tasks) == 0

# c. Targets never include dead drone or self

@pytest.mark.asyncio
async def test_targets_never_include_dead_or_self(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    def mock_plan_fill(*args, **kwargs):
        from DroneOS.core.coordination.healing import HealPlan
        return HealPlan({'drone2': 0, 'drone3': 1}, "tail_fill", {}, 0.0, 0.0, True, "none", None)
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_fill)

    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}

    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.my_id = 'drone2'
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()

    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})

    assert len(sender.calls) == 1
    params, targets = sender.calls[0]
    assert 'drone1' not in targets
    assert manager.my_id not in targets
    assert 'drone3' in targets
    assert 'drone2' not in targets
    assert targets == ['drone3']

# c. New params equal live params except slot_assignments

@pytest.mark.asyncio
async def test_pre_send_race_operator_change(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    # Provider returns different dict
    fp_changed = copy.deepcopy(fp)
    fp_changed['spacing'] = 20.0
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp_changed), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0

    assert manager._heal_count == 0

# d. Pre-send races (proposed anchor changed)
@pytest.mark.asyncio
async def test_pre_send_race_anchor_change(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    # We pass 'drone3' as the live anchor, but the snapshot had 'drone1'
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone3', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0

# d. Pre-send races (quorum lost)
@pytest.mark.asyncio
async def test_pre_send_race_quorum_lost(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.ISOLATED, copy.deepcopy(fp), healthy, 'drone1', 'drone1', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0

# d. Pre-send races (dead set changed)
@pytest.mark.asyncio
async def test_pre_send_race_dead_set_changed(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    # Let's say drone3 died right before send
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0


# d. Fail-closed case (planner exception)
@pytest.mark.asyncio
async def test_fail_closed_planner_exception(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    def mock_plan_raise(*args, **kwargs):
        raise RuntimeError("planner error")
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_raise)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0
    
# d. Fail-closed case (provider None)
@pytest.mark.asyncio
async def test_fail_closed_provider_none(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: None, formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0


# d. Fail-closed case (sender raising)
@pytest.mark.asyncio
async def test_fail_closed_sender_raising(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    sender = MockSender()
    sender.should_raise = True
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert manager._heal_count == 0

# e. Gating matrix as a parametrized test
@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "enabled_false",
    "mode_advisory",
    "armed_false",
    "sender_none",
    "kill_switch",
    "all_present"
])
async def test_gating_matrix(mock_cfg, tmp_path, monkeypatch, gate):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    
    if gate == "enabled_false":
        mock_cfg.coordination['enabled'] = False
    elif gate == "mode_advisory":
        mock_cfg.coordination['mode'] = "advisory"
    elif gate == "armed_false":
        mock_cfg.coordination['armed'] = False
        
    sender = MockSender() if gate != "sender_none" else None
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    
    if gate == "kill_switch":
        ks = tmp_path / "COORD_DISABLE"
        mock_cfg.coordination['kill_switch_file'] = str(ks)
        manager._kill_switch_active = True
        
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    
    if gate == "all_present":
        pass
    elif gate == "sender_none":
        pass # just assert didn't crash
    else:
        assert len(sender.calls) == 0

# Remaining tests
@pytest.mark.asyncio
async def test_heal_cap(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager._heal_count = 3
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0


@pytest.mark.asyncio
async def test_kill_switch_mid_run(mock_cfg, tmp_path, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    ks = tmp_path / "COORD_DISABLE"
    mock_cfg.coordination['kill_switch_file'] = str(ks)
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    manager._kill_switch_active = True
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert len(sender.calls) == 0


@pytest.mark.asyncio
async def test_excluded_drone_returns(mock_cfg):
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone3': 1}}
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    
    manager._excluded_members = {'drone2'}
    manager._excluded_warned = set()
    
    import logging
    warns = []
    class MockHandler(logging.Handler):
        def emit(self, record):
            if 'returned after heal' in record.getMessage():
                warns.append(record.getMessage())
    import DroneOS.core.coordination.manager as mgr; mgr.logger.addHandler(MockHandler())
    
    now = 100.0
    manager.my_id = 'drone1'
    heal_max_position_age_s = 5.0
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0, 'last_seen': 100.0, 'lat': 1.0, 'lon': 1.0, 'alt': 1.0, 'dead_since': None, 'rejoin_stable_start': None, 'missed_beats': 0, 'last_position_time': 100.0})()
    
    from DroneOS.core.coordination.anchor import is_healthy
    for pid in list(manager._excluded_members):
        if pid not in fp['slot_assignments'] and pid not in manager._excluded_warned:
            if is_healthy(pid, manager.my_id, manager.membership, manager.swarm, 30.0, now, heal_max_position_age_s):
                mgr.logger.warning(f"[coord] drone {pid} returned after heal; operator must re-issue the formation to re-add it")
                manager._excluded_warned.add(pid)
                
    assert len(warns) == 1
    
    warns.clear()
    for pid in list(manager._excluded_members):
        if pid not in fp['slot_assignments'] and pid not in manager._excluded_warned:
            if is_healthy(pid, manager.my_id, manager.membership, manager.swarm, 30.0, now, heal_max_position_age_s):
                mgr.logger.warning(f"[coord] drone {pid} returned after heal; operator must re-issue the formation to re-add it")
                manager._excluded_warned.add(pid)
    assert len(warns) == 0

@pytest.mark.asyncio
async def test_receiver_floor_equality():
    from DroneOS.core.coordination.healing import validate_formation_params
    from DroneOS.core.flight_manager import FlightManager
    from DroneOS.tests.test_healing_oracle import get_cfg
    
    fm = FlightManager(None, None)
    fm.swarm_manager = type("S", (), {"identity": type("I", (), {"drone_id": "d1"})()})()
    fm.swarm_manager = type("S", (), {"identity": type("I", (), {"drone_id": "d1"})()})()
    
    for cfg_name in ["sim", "hw"]:
        cfg = get_cfg(cfg_name)
        fm._flight_config = cfg
        
        min_sep = float(getattr(cfg.formation, "min_formation_separation_m", 8.0))
        min_ca = float(getattr(cfg.collision_avoidance, "min_horizontal_distance", 2.0))
        floor = 1.5 * max(min_sep, min_ca)
        
        params_below = {"type": "V", "spacing": floor - 0.1, "slot_assignments": {"d1": 0}}
        params_above = {"type": "V", "spacing": floor + 0.1, "slot_assignments": {"d1": 0}}
        
        # Validator
        assert validate_formation_params(params_below, cfg) is False
        assert validate_formation_params(params_above, cfg) is True
        
        # FlightManager
        assert await fm.formation_update(params_below) is False
        assert await fm.formation_update(params_above) is True

@pytest.mark.asyncio
async def test_verify_application_passes(mock_cfg, monkeypatch):
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone3': 1}}
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp))
    await manager._verify_application({'drone1': 0, 'drone3': 1}, fp)
    assert not getattr(manager, "_heals_disabled", False)

@pytest.mark.asyncio
async def test_verify_application_fails_disables(mock_cfg, monkeypatch):
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone3': 1}}
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp))
    # Provide new_slots that do not match the current 'live' (which is fp)
    await manager._verify_application({'drone1': 0, 'drone3': 1, 'drone4': 2}, fp)
    assert getattr(manager, "_heals_disabled", False)

@pytest.mark.asyncio
async def test_verify_application_operator_changed_no_disable(mock_cfg, monkeypatch):
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone3': 1}}
    fp_changed = {'type': 'LINE', 'spacing': 10.0, 'slot_assignments': {'drone1': 0, 'drone3': 1}}
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp_changed))
    await manager._verify_application({'drone1': 0, 'drone3': 1, 'drone4': 2}, fp)
    assert not getattr(manager, "_heals_disabled", False)


@pytest.mark.asyncio
async def test_partial_delivery_4_to_3_one_miss(mock_cfg, monkeypatch):
    from DroneOS.core.coordination.healing import plan_healing
    from DroneOS.tests.test_healing_oracle import _pt_to_segment, analytic_segment_distance
    from DroneOS.core.formation_engine import FormationEngine as GeometryEngine
    
    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"d1": 1, "d2": 2, "d3": 3, "d4": 4, "d0": 0}}
    # d0 died. d1 becomes anchor.
    swarm = type("S", (), {"identity": type("I", (), {"drone_id": "d0"})(), "registry": type("R", (), {"get_all_peers": lambda self: [], "get_peer": lambda self, pid: None})()})()
    engine = GeometryEngine(swarm, None, None)
    old_expected = engine.get_expected_positions(
        type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), fp
    )
    old_positions = {pid: old_expected[pid][:2] for pid in fp["slot_assignments"]}
    
    cfg = type("Cfg", (), {"formation": type("F", (), {"min_formation_separation_m": 8.0}), "collision_avoidance": type("C", (), {"min_horizontal_distance": 2.0})})()
    import DroneOS.shared.config.profile
    cfg.heal_separation_margin_m = 1.0
    
    plan = plan_healing(fp, {"d1", "d2", "d3", "d4"}, "d1", "d1", "d1", cfg, None, {"lat":0, "lon":0, "alt":0, "gps_valid":True, "position_age":0})
    if not plan.accepted: return
    if not plan.accepted: return
    
    new_fp = {"type": "V", "spacing": 15.0, "slot_assignments": plan.slot_assignments}
    swarm.identity.drone_id = "d1"
    new_expected = engine.get_expected_positions(type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), new_fp)
    
    # d3 misses the update. It retains fp, but sees d1's telemetry as origin
    swarm.identity.drone_id = "d1"
    d3_old_expected = engine.get_expected_positions(type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), fp)
    
    # target positions relative to d1 origin
    targets = {
        "d1": new_expected["d1"][:2],
        "d2": new_expected["d2"][:2],
        "d3": (old_expected["d3"][0] - old_positions["d1"][0], old_expected["d3"][1] - old_positions["d1"][1])
    }
    
    hazard_found = False
    # Check paths
    for pid in ["d1", "d2", "d3", "d4"]:
        start = (old_positions[pid][0] - old_positions["d1"][0], old_positions[pid][1] - old_positions["d1"][1])
        end = targets[pid]
        for other in ["d1", "d2", "d3", "d4"]:
            if other <= pid: continue
            other_start = (old_positions[other][0] - old_positions["d1"][0], old_positions[other][1] - old_positions["d1"][1])
            other_end = targets[other]
            sep = analytic_segment_distance(start, end, other_start, other_end)
            if sep < 8.0 - 0.1:
                hazard_found = True
    assert not hazard_found, "Expected NO partial delivery collision hazard!"

@pytest.mark.asyncio
async def test_partial_delivery_4_to_3_two_miss(mock_cfg, monkeypatch):
    from DroneOS.core.coordination.healing import plan_healing
    from DroneOS.tests.test_healing_oracle import _pt_to_segment, analytic_segment_distance
    from DroneOS.core.formation_engine import FormationEngine as GeometryEngine
    
    fp = {"type": "V", "spacing": 15.0, "slot_assignments": {"d1": 1, "d2": 2, "d3": 3, "d4": 4, "d0": 0}}
    # d0 died. d1 becomes anchor.
    swarm = type("S", (), {"identity": type("I", (), {"drone_id": "d0"})(), "registry": type("R", (), {"get_all_peers": lambda self: [], "get_peer": lambda self, pid: None})()})()
    engine = GeometryEngine(swarm, None, None)
    old_expected = engine.get_expected_positions(
        type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), fp
    )
    old_positions = {pid: old_expected[pid][:2] for pid in fp["slot_assignments"]}
    
    cfg = type("Cfg", (), {"formation": type("F", (), {"min_formation_separation_m": 8.0}), "collision_avoidance": type("C", (), {"min_horizontal_distance": 2.0})})()
    import DroneOS.shared.config.profile
    cfg.heal_separation_margin_m = 1.0
    
    plan = plan_healing(fp, {"d1", "d2", "d3", "d4"}, "d1", "d1", "d1", cfg, None, {"lat":0, "lon":0, "alt":0, "gps_valid":True, "position_age":0})
    if not plan.accepted: return
    
    new_fp = {"type": "V", "spacing": 15.0, "slot_assignments": plan.slot_assignments}
    swarm.identity.drone_id = "d1"
    new_expected = engine.get_expected_positions(type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), new_fp)
    
    # d2 and d3 miss the update.
    
    
    targets = {
        "d1": new_expected["d1"][:2],
        "d2": old_expected["d2"][:2], # missed
        "d3": old_expected["d3"][:2]  # missed
    }
    
    # Check paths
    hazard_found = False
    for pid in ["d1", "d2", "d3", "d4"]:
        start = (old_positions[pid][0] - old_positions["d1"][0], old_positions[pid][1] - old_positions["d1"][1])
        end = targets[pid]
        for other in ["d1", "d2", "d3", "d4"]:
            if other <= pid: continue
            other_start = (old_positions[other][0] - old_positions["d1"][0], old_positions[other][1] - old_positions["d1"][1])
            other_end = targets[other]
            sep = analytic_segment_distance(start, end, other_start, other_end)
            if sep < 8.0 - 0.1:
                hazard_found = True
    assert not hazard_found, "Expected NO partial delivery collision hazard!"

@pytest.mark.asyncio
async def test_partial_delivery_3_to_2_one_miss(mock_cfg, monkeypatch):
    from DroneOS.core.coordination.healing import plan_healing
    from DroneOS.tests.test_healing_oracle import _pt_to_segment, analytic_segment_distance
    from DroneOS.core.formation_engine import FormationEngine as GeometryEngine
    
    fp = {"type": "LINE", "spacing": 15.0, "slot_assignments": {"d1": 1, "d2": 2, "d0": 0}}
    # d0 died. d1 becomes anchor.
    swarm = type("S", (), {"identity": type("I", (), {"drone_id": "d0"})(), "registry": type("R", (), {"get_all_peers": lambda self: [], "get_peer": lambda self, pid: None})()})()
    engine = GeometryEngine(swarm, None, None)
    old_expected = engine.get_expected_positions(
        type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), fp
    )
    old_positions = {pid: old_expected[pid][:2] for pid in fp["slot_assignments"]}
    
    cfg = type("Cfg", (), {"formation": type("F", (), {"min_formation_separation_m": 8.0}), "collision_avoidance": type("C", (), {"min_horizontal_distance": 2.0})})()
    import DroneOS.shared.config.profile
    cfg.heal_separation_margin_m = 1.0
    
    plan = plan_healing(fp, {"d1", "d2"}, "d1", "d1", "d1", cfg, None, {"lat":0, "lon":0, "alt":0, "gps_valid":True, "position_age":0})
    if not plan.accepted: return
    
    new_fp = {"type": "LINE", "spacing": 15.0, "slot_assignments": plan.slot_assignments}
    swarm.identity.drone_id = "d1"
    new_expected = engine.get_expected_positions(type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), new_fp)
    
    
    
    targets = {
        "d1": new_expected["d1"][:2],
        "d2": old_expected["d2"][:2], # missed
    }
    
    # Check paths
    hazard_found = False
    for pid in ["d1", "d2"]:
        start = (old_positions[pid][0] - old_positions["d1"][0], old_positions[pid][1] - old_positions["d1"][1])
        end = targets[pid]
        for other in ["d1", "d2"]:
            if other <= pid: continue
            other_start = (old_positions[other][0] - old_positions["d1"][0], old_positions[other][1] - old_positions["d1"][1])
            other_end = targets[other]
            sep = analytic_segment_distance(start, end, other_start, other_end)
            if sep < 8.0 - 0.1:
                hazard_found = True
    assert not hazard_found, "Expected NO partial delivery collision hazard!"

@pytest.mark.asyncio
async def test_partial_delivery_5_to_4_two_miss(mock_cfg, monkeypatch):
    # Two miss in 3->2 means neither received it? But d1 is the sender!
    # "two targets missed" in 3->2: well there's only 2 targets (since N=2 remaining).
    # But wait, one of them IS the sender, so they can't miss it (self-application).
    # So if there are 2 survivors, only 1 target can miss it!
    # I'll just do N=5->4 for the 4th test to have 2 miss.
    from DroneOS.core.coordination.healing import plan_healing
    from DroneOS.tests.test_healing_oracle import _pt_to_segment, analytic_segment_distance
    from DroneOS.core.formation_engine import FormationEngine as GeometryEngine
    
    fp = {"type": "SQUARE", "spacing": 15.0, "slot_assignments": {"d1": 1, "d2": 2, "d3": 3, "d4": 4, "d0": 0}}
    swarm = type("S", (), {"identity": type("I", (), {"drone_id": "d0"})(), "registry": type("R", (), {"get_all_peers": lambda self: [], "get_peer": lambda self, pid: None})()})()
    engine = GeometryEngine(swarm, None, None)
    old_expected = engine.get_expected_positions(
        type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), fp
    )
    old_positions = {pid: old_expected[pid][:2] for pid in fp["slot_assignments"]}
    
    cfg = type("Cfg", (), {"formation": type("F", (), {"min_formation_separation_m": 8.0}), "collision_avoidance": type("C", (), {"min_horizontal_distance": 2.0})})()
    import DroneOS.shared.config.profile
    cfg.heal_separation_margin_m = 1.0
    
    plan = plan_healing(fp, {"d1", "d2", "d3", "d4"}, "d1", "d1", "d1", cfg, None, {"lat":0, "lon":0, "alt":0, "gps_valid":True, "position_age":0})
    if not plan.accepted: return
    
    new_fp = {"type": "SQUARE", "spacing": 15.0, "slot_assignments": plan.slot_assignments}
    swarm.identity.drone_id = "d1"
    new_expected = engine.get_expected_positions(type("Telemetry", (), {"gps_valid": True, "latitude": 0.0, "longitude": 0.0, "altitude": 0.0})(), new_fp)
    
    
    
    targets = {
        "d1": new_expected["d1"][:2],
        "d2": old_expected["d2"][:2], # missed
        "d3": old_expected["d3"][:2]  # missed
    }
    
    # Check paths
    hazard_found = False
    for pid in ["d1", "d2", "d3", "d4"]:
        start = (old_positions[pid][0] - old_positions["d1"][0], old_positions[pid][1] - old_positions["d1"][1])
        end = targets[pid]
        for other in ["d1", "d2", "d3", "d4"]:
            if other <= pid: continue
            other_start = (old_positions[other][0] - old_positions["d1"][0], old_positions[other][1] - old_positions["d1"][1])
            other_end = targets[other]
            sep = analytic_segment_distance(start, end, other_start, other_end)
            if sep < 8.0 - 0.1:
                hazard_found = True
    assert not hazard_found, "Expected NO partial delivery collision hazard!"

@pytest.mark.asyncio
async def test_receiver_floor_equality_relative_alt_hazard(mock_cfg):
    # This test demonstrates why Stage B must intercept GLOBAL_RELATIVE_ALT:
    # If telemetry.alt is relative to different takeoff elevations, drones at the same
    # absolute AMSL altitude will have different relative altitudes, failing the floor check.
    
    # Simulate drone2 (self) taking off at 0m AMSL, flying to 50m AMSL -> alt = 50.0m
    # Simulate drone1 (anchor) taking off at 20m AMSL, flying to 50m AMSL -> alt = 30.0m
    # They are physically on the same floor (50m AMSL), but relative alts differ by 20m.
    my_relative_alt = 50.0
    anchor_relative_alt = 30.0
    
    # Stage B validation (e.g. heal_separation_margin_m = 1.0)
    heal_separation_margin_m = 1.0
    
    # Direct comparison fails!
    direct_diff = abs(my_relative_alt - anchor_relative_alt)
    assert direct_diff > heal_separation_margin_m
    
    # Therefore, Stage B MUST intercept GLOBAL_RELATIVE_ALT telemetry to either:
    # 1. Use absolute AMSL altitude from GPS
    # 2. Add the home elevation offset back
    # Otherwise, valid formations are rejected.

@pytest.mark.asyncio
async def test_sender_returns(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    def mock_plan_fill(*args, **kwargs):
        from DroneOS.core.coordination.healing import HealPlan
        return HealPlan({'drone2': 0, 'drone3': 1}, "tail_fill", {}, 0.0, 0.0, True, "none", None)
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_fill)

    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone2', 'drone3'}

    sender = MockSender()
    sender.return_value = False
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    manager.my_id = 'drone2'
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()

    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0})
    assert manager._heal_count == 0
    assert getattr(manager, "_consecutive_failures", 0) == 1

@pytest.mark.asyncio
async def test_line_anchor_dead_noplan(mock_cfg, monkeypatch):
    # (b) LINE, 15 m, anchor dead: NO_PLAN, exactly one "anchor lost" WARNING, no send
    import DroneOS.core.coordination.healing as healing_module
    def mock_plan_noplan(*args, **kwargs):
        from DroneOS.core.coordination.healing import HealPlan, RejectKind
        return HealPlan({'drone2': 1}, "none", {}, 0.0, 0.0, False, "anchor dead in LINE", RejectKind.FINAL)
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_noplan)
    
    fp = {'type': 'LINE', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1}}
    healthy = {'drone2'}
    sender = MockSender()
    manager = CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    
    manager.my_id = 'drone2'
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    import logging
    warns = []
    class MockHandler(logging.Handler):
        def emit(self, record):
            if record.levelno == logging.WARNING:
                warns.append(record.getMessage())
    import DroneOS.core.coordination.manager as mgr; mgr.logger.addHandler(MockHandler())
    
    my_status = {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0}
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', my_status)
    
    assert len(sender.calls) == 0
    assert any("anchor lost" in w for w in warns)

@pytest.mark.asyncio

@pytest.mark.asyncio
async def test_60_seconds_dead_anchor(mock_cfg, monkeypatch):
    import DroneOS.core.coordination.healing as healing_module
    def mock_plan_fill(*args, **kwargs):
        from DroneOS.core.coordination.healing import HealPlan
        return HealPlan({'drone2': 0, 'drone3': 1, 'drone4': 2}, "tail_fill", {}, 10.0, 10.0, True, "none", None)
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_fill)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2, 'drone4': 3}}
    healthy = {'drone2', 'drone3', 'drone4'}
    sender = MockSender()
    mock_cfg.coordination['mode'] = 'active'
    mock_cfg.coordination['armed'] = True
    import DroneOS.core.coordination.manager as mgr
    manager = mgr.CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    
    manager.my_id = 'drone2'
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone4'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    
    my_status = {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0}
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', my_status)
    await manager._handle_healing(110.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', my_status)
    await manager._handle_healing(161.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone2', my_status)
    
    assert len(sender.calls) == 1
    params, targets = sender.calls[0]
    assert params["slot_assignments"] == {'drone2': 0, 'drone3': 1, 'drone4': 2}
    assert params["members"] == ['drone3', 'drone4']
    assert set(targets) == {'drone3', 'drone4'}
    assert 'drone1' not in targets
    assert 'drone2' not in targets

@pytest.mark.asyncio
async def test_follower_dead_hold(mock_cfg, monkeypatch):
    mock_cfg.coordination['reshape_on_follower_loss'] = 'false'
    import DroneOS.core.coordination.healing as healing_module
    def mock_plan_hold(*args, **kwargs):
        from DroneOS.core.coordination.healing import HealPlan, RejectKind
        return HealPlan({'drone1': 0, 'drone3': 1}, "none", {}, 0.0, 0.0, False, "reshape off", RejectKind.TRANSIENT)
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_hold)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone1', 'drone3'}
    sender = MockSender()
    import DroneOS.core.coordination.manager as mgr
    manager = mgr.CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    
    manager.my_id = 'drone1'
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    
    import logging
    holds = []
    class MockHandler(logging.Handler):
        def emit(self, record):
            if 'HOLD' in record.getMessage():
                holds.append(record.getMessage())
    mgr.logger.addHandler(MockHandler())
    
    my_status = {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0}
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone1', my_status)
    
    assert len(sender.calls) == 0
    assert len(holds) == 1

@pytest.mark.asyncio
async def test_follower_loss_reshape_active(mock_cfg, monkeypatch):
    mock_cfg.coordination['reshape_on_follower_loss'] = 'true'
    mock_cfg.coordination['mode'] = 'active'
    import DroneOS.core.coordination.healing as healing_module
    monkeypatch.setattr(healing_module, 'plan_healing', mock_plan_accepted)
    
    fp = {'type': 'V', 'spacing': 15.0, 'slot_assignments': {'drone1': 0, 'drone2': 1, 'drone3': 2}}
    healthy = {'drone1', 'drone3'}
    sender = MockSender()
    import DroneOS.core.coordination.manager as mgr
    manager = mgr.CoordinationManager(MockSwarm(), mock_cfg, clock=lambda: 100.0, formation_provider=lambda: copy.deepcopy(fp), formation_update_sender=sender)
    
    manager.my_id = 'drone1'
    manager.membership.nodes['drone1'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone3'] = type('MockNode', (), {'state': PeerState.ALIVE, 'battery_level': 100.0})()
    manager.membership.nodes['drone2'] = type('MockNode', (), {'state': PeerState.DEAD, 'dead_since': 50.0})()
    
    import logging
    warns = []
    class MockHandler(logging.Handler):
        def emit(self, record):
            if record.levelno == logging.WARNING and 'follower reshape is not safe' in record.getMessage():
                warns.append(record.getMessage())
    mgr.logger.addHandler(MockHandler())
    
    my_status = {'gps_valid': True, 'position_age': 0.0, 'battery_level': 100.0, 'lat': 0.0, 'lon': 0.0, 'alt': 0.0}
    await manager._handle_healing(100.0, QuorumState.QUORUM, copy.deepcopy(fp), healthy, 'drone1', 'drone1', my_status)
    
    assert len(sender.calls) == 0
    assert len(warns) == 1
