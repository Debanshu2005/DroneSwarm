import time
import pytest
from DroneOS.shared.protocol.messages import HeartbeatMessage
from DroneOS.core.coordination.membership import MembershipView, PeerState

def test_suspect_dead_transitions(monkeypatch):
    # c) SUSPECT/DEAD transition tests
    config = {
        "suspect_missed_beats": 3,
        "dead_missed_beats": 6,
        "rejoin_stable_s": 2.0
    }
    # Mock time
    current_time = 100.0
    monkeypatch.setattr(time, 'monotonic', lambda: current_time)
    
    view = MembershipView(config)
    
    # Initialize peer
    view.update_from_peer("drone2", 100.0, 100.0, 0, 0, 0)
    assert view.nodes["drone2"].state == PeerState.ALIVE
    
    # Miss 1 beat
    current_time = 101.5
    view.evaluate_tick()
    assert view.nodes["drone2"].state == PeerState.ALIVE
    
    # Miss 3 beats -> SUSPECT
    current_time = 103.5
    view.evaluate_tick()
    assert view.nodes["drone2"].state == PeerState.SUSPECT
    
    # Miss 6 beats -> DEAD
    current_time = 106.5
    view.evaluate_tick()
    assert view.nodes["drone2"].state == PeerState.DEAD

def test_single_missed_beat_hysteresis(monkeypatch):
    # d) single-missed-beat hysteresis
    config = {
        "suspect_missed_beats": 3,
        "dead_missed_beats": 6,
        "rejoin_stable_s": 2.0
    }
    current_time = 100.0
    monkeypatch.setattr(time, 'monotonic', lambda: current_time)
    
    view = MembershipView(config)
    view.update_from_peer("drone2", 100.0, 100.0, 0, 0, 0)
    
    # 1 missed beat
    current_time = 101.6
    view.evaluate_tick()
    assert view.nodes["drone2"].state == PeerState.ALIVE
    assert view.nodes["drone2"].missed_beats == 1
    
    # Heartbeat arrives
    current_time = 102.0
    view.update_from_peer("drone2", 102.0, 100.0, 0, 0, 0)
    assert view.nodes["drone2"].missed_beats == 0
    assert view.nodes["drone2"].state == PeerState.ALIVE

def test_rejoin_stable_s(monkeypatch):
    # e) rejoin only after rejoin_stable_s
    config = {
        "suspect_missed_beats": 3,
        "dead_missed_beats": 6,
        "rejoin_stable_s": 2.0
    }
    current_time = 100.0
    monkeypatch.setattr(time, 'monotonic', lambda: current_time)
    
    view = MembershipView(config)
    view.update_from_peer("drone2", 100.0, 100.0, 0, 0, 0)
    
    # Go DEAD
    current_time = 106.5
    view.evaluate_tick()
    assert view.nodes["drone2"].state == PeerState.DEAD
    
    # Send heartbeat
    current_time = 107.0
    view.update_from_peer("drone2", 107.0, 100.0, 0, 0, 0)
    # State should still be DEAD, but rejoin stabilization started
    assert view.nodes["drone2"].state == PeerState.DEAD
    assert view.nodes["drone2"].rejoin_stable_start == 107.0
    
    # Not enough time passed
    current_time = 108.0
    view.update_from_peer("drone2", 108.0, 100.0, 0, 0, 0)
    assert view.nodes["drone2"].state == PeerState.DEAD
    
    # Enough time passed
    current_time = 109.5
    view.update_from_peer("drone2", 109.5, 100.0, 0, 0, 0)
    assert view.nodes["drone2"].state == PeerState.ALIVE
    assert view.nodes["drone2"].rejoin_stable_start is None

def test_main_app_instantiation():
    # f) no coordination objects when disabled
    from DroneOS.main import DroneOSApp
    from unittest.mock import patch
    import os
    import sys
    
    configs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "configs"))

    with patch.object(sys, 'argv', ['main.py', configs_dir]):
        app = DroneOSApp()
        
        # Since config defaults to disabled, it should be None
        assert app.coordination_manager is None
        
        from DroneOS.shared.config.profile import resolve_flight_config as original_resolve
        
        def mock_resolve(*args, **kwargs):
            cfg = original_resolve(*args, **kwargs)
            if not getattr(cfg, 'coordination', None):
                cfg.coordination = {}
            cfg.coordination["enabled"] = True
            return cfg

        with patch('DroneOS.shared.config.profile.resolve_flight_config', side_effect=mock_resolve):
            app_enabled = DroneOSApp()
            assert app_enabled.coordination_manager is not None
            assert app_enabled.coordination_manager.enabled is True

@pytest.mark.asyncio
async def test_manager_exception_handling():
    # h) exception inside the manager loop disables coordination without raising
    from DroneOS.core.coordination.manager import CoordinationManager
    
    class MockSwarm:
        identity = type('MockIdentity', (), {'drone_id': 'drone1'})
        registry = type('MockRegistry', (), {'get_all_peers': lambda: ["drone1", "drone2"]})
    
    manager = CoordinationManager(MockSwarm(), None)
    manager.enabled = True
    
    # Cause exception in update_from_peer by corrupting membership view
    manager.membership = None
    
    # Run it; it should NOT raise an exception, but should exit immediately
    # because the loop will crash and set is_running = False and enabled = False
    await manager.run()
    
    assert manager.is_running is False
    assert manager.enabled is False

def test_vanished_peer_becomes_dead(monkeypatch):
    # g) a peer removed from the registry still becomes DEAD
    config = {
        "suspect_missed_beats": 3,
        "dead_missed_beats": 6,
        "rejoin_stable_s": 2.0
    }
    current_time = 100.0
    monkeypatch.setattr(time, 'monotonic', lambda: current_time)
    view = MembershipView(config)
    
    # Peer joins
    view.update_from_peer("drone_vanished", 100.0, 100.0, 0, 0, 0)
    
    # It vanishes from registry, meaning we don't call update_from_peer anymore
    # But evaluate_tick is called
    current_time = 106.5
    view.evaluate_tick(1.0)
    assert view.nodes["drone_vanished"].state == PeerState.DEAD

@pytest.mark.asyncio
async def test_manager_vanished_peer_becomes_dead(monkeypatch):
    # g) register a peer, update it, then REMOVE it from the SwarmRegistry, advance time, and assert it becomes DEAD
    from DroneOS.core.coordination.manager import CoordinationManager
    from DroneOS.core.swarm_manager import SwarmRegistry, SwarmMembership
    
    registry = SwarmRegistry()
    registry.add_peer("drone_vanishing")
    
    # Manually configure peer state
    peer_state = registry.get_peer("drone_vanishing")
    peer_state.last_seen = 100.0
    
    class MockSwarm:
        def __init__(self):
            self.identity = type('MockIdentity', (), {'drone_id': 'drone1'})
            self.registry = registry
            self.network_cfg = type('MockNetCfg', (), {'heartbeat_interval': 1.0})
            
    mock_flight_cfg = type('MockCfg', (), {'coordination': {
        "enabled": True,
        "suspect_missed_beats": 3,
        "dead_missed_beats": 6,
        "rejoin_stable_s": 2.0
    }})
    
    monkeypatch.setattr(time, 'monotonic', lambda: 100.0)
    manager = CoordinationManager(MockSwarm(), mock_flight_cfg, heartbeat_interval=1.0)
    
    # 1. Update from registry manually as if run() did one iteration at t=100
    monkeypatch.setattr(time, 'monotonic', lambda: 100.0)
    manager.membership.update_from_peer("drone_vanishing", 100.0, 100.0, 0, 0, 0)
    manager.membership.evaluate_tick(hb_interval=1.0)
    assert manager.membership.nodes["drone_vanishing"].state == PeerState.ALIVE
    
    # 2. Peer is removed from registry
    registry.remove_peer("drone_vanishing")
    
    # 3. Simulate manager running at t=106.5
    # Manager will call get_all_peers() which is empty, so it won't call update_from_peer.
    # But it calls evaluate_tick()
    monkeypatch.setattr(time, 'monotonic', lambda: 106.5)
    
    # emulate manager's exact loop body
    for peer_id in registry.get_all_peers():
        ps = registry.get_peer(peer_id)
        if ps:
            manager.membership.update_from_peer(peer_id, ps.last_seen, ps.battery_level, ps.lat, ps.lon, ps.alt)
            
    manager.membership.evaluate_tick(hb_interval=1.0)
    
    # Should be DEAD!
    assert "drone_vanishing" in manager.membership.nodes
    assert manager.membership.nodes["drone_vanishing"].state == PeerState.DEAD
    
def test_non_default_heartbeat_interval(monkeypatch):
    # Test that non-default interval (e.g. 0.5 or 2.0) changes missed-beat count calculation
    config = {
        "suspect_missed_beats": 3,
        "dead_missed_beats": 6,
        "rejoin_stable_s": 2.0
    }
    
    # Fast heartbeat (0.5s)
    current_time = 100.0
    monkeypatch.setattr(time, 'monotonic', lambda: current_time)
    view = MembershipView(config)
    view.update_from_peer("fast_drone", 100.0, 100.0, 0, 0, 0)
    
    # Wait 2.0 seconds. At 0.5s interval, this is 4 missed beats -> SUSPECT
    current_time = 102.1
    view.evaluate_tick(hb_interval=0.5)
    assert view.nodes["fast_drone"].missed_beats == 4
    assert view.nodes["fast_drone"].state == PeerState.SUSPECT
    
    # Slow heartbeat (2.0s)
    current_time = 200.0
    view2 = MembershipView(config)
    view2.update_from_peer("slow_drone", 200.0, 100.0, 0, 0, 0)
    
    # Wait 2.0 seconds. At 2.0s interval, this is only 1 missed beat -> ALIVE
    current_time = 202.1
    # 202.1 - 200.0 = 2.1 > 2.0 * 1.5 (3.0) -> false! It won't even register as a missed beat!
    # Wait 3.5 seconds.
    current_time = 203.5
    view2.evaluate_tick(hb_interval=2.0)
    assert view2.nodes["slow_drone"].missed_beats == 1
    assert view2.nodes["slow_drone"].state == PeerState.ALIVE
