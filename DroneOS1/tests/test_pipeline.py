import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.flight_pipeline import Arbiter, SafetyFilter, CommandWriter, FlightPipeline

def test_arbiter_priority():
    arbiter = Arbiter()
    
    # Mission vs Formation
    mission_intent = FlightIntent(IntentSource.MISSION, IntentAction.GOTO)
    formation_intent = FlightIntent(IntentSource.FORMATION, IntentAction.GOTO)
    
    winner = arbiter.select_winner({
        IntentSource.MISSION: mission_intent,
        IntentSource.FORMATION: formation_intent
    })
    
    # Formation (30) > Mission (20)
    assert winner.source == IntentSource.FORMATION
    
    # Collision vs Formation
    collision_intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY)
    winner = arbiter.select_winner({
        IntentSource.MISSION: mission_intent,
        IntentSource.FORMATION: formation_intent,
        IntentSource.COLLISION: collision_intent
    })
    
    # Collision (40) > Formation (30)
    assert winner.source == IntentSource.COLLISION
    
    # Safety vs All
    safety_intent = FlightIntent(IntentSource.SAFETY, IntentAction.RTL)
    winner = arbiter.select_winner({
        IntentSource.MISSION: mission_intent,
        IntentSource.FORMATION: formation_intent,
        IntentSource.COLLISION: collision_intent,
        IntentSource.SAFETY: safety_intent
    })
    
    # Safety (50) wins
    assert winner.source == IntentSource.SAFETY

def test_intent_expiration():
    arbiter = Arbiter()
    
    # Expired collision intent vs fresh mission intent
    mission_intent = FlightIntent(IntentSource.MISSION, IntentAction.GOTO)
    collision_intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY, ttl_seconds=0.1)
    
    # Force expiration
    collision_intent.timestamp = time.time() - 1.0 
    
    winner = arbiter.select_winner({
        IntentSource.MISSION: mission_intent,
        IntentSource.COLLISION: collision_intent
    })
    
    # Mission should win because Collision is expired
    assert winner.source == IntentSource.MISSION

def test_safety_filter_limits():
    sf = SafetyFilter(config=None)
    
    # Test bounds -5 to +5 for vx/vy
    intent = FlightIntent(IntentSource.MANUAL, IntentAction.MOVE_VELOCITY, params={"vx": 10.0, "vy": -10.0, "vz": 5.0})
    safe_intent = sf.validate(intent, None)
    
    assert safe_intent.params["vx"] == 5.0
    assert safe_intent.params["vy"] == -5.0
    assert safe_intent.params["vz"] == 3.0 # max vz is 3.0

@pytest.mark.asyncio
async def test_command_ownership():
    mock_fc = AsyncMock()
    cw = CommandWriter(mock_fc)
    
    # Move velocity
    intent = FlightIntent(IntentSource.MISSION, IntentAction.MOVE_VELOCITY, params={"vx": 1.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.0})
    await cw.execute(intent)
    mock_fc.move_velocity.assert_called_once_with(1.0, 0.0, 0.0, 0.1, 0.0)
    
    # Emergency Kill
    intent = FlightIntent(IntentSource.SAFETY, IntentAction.EMERGENCY_KILL)
    await cw.execute(intent)
    mock_fc.kill.assert_called_once()
