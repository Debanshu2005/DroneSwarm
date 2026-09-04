import pytest
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

from DroneOS.core.decision_engine import LocalDecisionEngine
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.core.flight_state import FlightStateStore

@pytest.fixture
def state_store():
    return FlightStateStore()

@pytest.fixture
def mock_deps(state_store):
    mission = MagicMock()
    mission.get_current_state.return_value = "RUNNING"
    wp = MagicMock()
    wp.delay = 5.0
    mission.get_current_waypoint.return_value = wp
    mission.executor.execute_waypoint = MagicMock(return_value=True) # WP Reached!
    
    swarm = MagicMock()
    swarm.registry.get_all_peers.return_value = []
    
    ca = MagicMock()
    ca.evaluate_threats.return_value = ("NORMAL", None, None, 0.0)
    
    nav = MagicMock()
    nav.flight_manager.formation_params = None
    
    safety = MagicMock()
    safety.is_failsafe_active = False

    return mission, swarm, ca, nav, safety, state_store

@pytest.mark.asyncio
async def test_non_blocking_waypoint_delay(mock_deps):
    mission, swarm, ca, nav, safety, state_store = mock_deps
    
    engine = LocalDecisionEngine(mission, swarm, ca, nav, safety, state_store, config=None)
    telemetry = TelemetryData(flight_mode="GUIDED", gps_valid=True)
    
    # Tick 1: Reaches waypoint
    await engine.evaluate_tick(telemetry)
    
    # Should start delay and emit HOVER
    assert getattr(engine, '_waypoint_delay_start', None) is not None
    intent = state_store.get_intents().get(IntentSource.MISSION)
    assert intent is not None
    assert intent.action == IntentAction.HOVER
    assert not mission.advance_waypoint.called
    
    # Tick 2: Midway through delay
    # We mock time.monotonic so it thinks 2.0s passed
    original_time = time.monotonic
    import time as real_time
    
    with pytest.MonkeyPatch.context() as m:
        m.setattr(real_time, 'monotonic', lambda: engine._waypoint_delay_start + 2.0)
        
        # Simulate a collision threat arriving
        ca.evaluate_threats.return_value = ("AVOIDANCE", {"north": 1.0, "east": 0.0, "down": 0.0, "yaw_rate": 0.0}, "drone2", 2.0)
        
        await engine.evaluate_tick(telemetry)
        
        # Collision intent should be submitted
        collision_intent = state_store.get_intents().get(IntentSource.COLLISION)
        assert collision_intent is not None
        assert collision_intent.action == IntentAction.MOVE_VELOCITY_NED
        
        # Since Arbiter (outside this engine) selects the highest priority, 
        # Collision > Mission, so the drone will evade despite the mission delay.
        assert not mission.advance_waypoint.called
        
    # Tick 3: Delay expires
    with pytest.MonkeyPatch.context() as m:
        m.setattr(real_time, 'monotonic', lambda: engine._waypoint_delay_start + 5.1)
        
        # Threat cleared
        ca.evaluate_threats.return_value = ("NORMAL", None, None, 0.0)
        
        await engine.evaluate_tick(telemetry)
        
        # Now it should advance the waypoint
        assert mission.advance_waypoint.called
        assert getattr(engine, '_waypoint_delay_start', None) is None


