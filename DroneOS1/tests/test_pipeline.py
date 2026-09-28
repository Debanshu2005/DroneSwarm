import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.flight_pipeline import Arbiter, SafetyFilter, CommandWriter, FlightPipeline
from DroneOS1.adapters.airsim_adapter import AirSimFlightController
from DroneOS1.core.command_handler import CommandHandler
from DroneOS1.shared.protocol.messages import ControlMessage, CommandAction, MessageType

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


@pytest.mark.parametrize(
    ("action", "fc_method", "expected_args"),
    [
        (IntentAction.TAKEOFF, "takeoff", (7.0,)),
        (IntentAction.LAND, "land", ()),
        (IntentAction.RTL, "rtl", ()),
    ],
)
@pytest.mark.asyncio
async def test_critical_manual_preempts_formation_and_reaches_airsim_adapter(action, fc_method, expected_args):
    """Proves the selected intent invokes the AirSim adapter method."""
    arbiter = Arbiter()
    mock_airsim_adapter = AsyncMock(spec=AirSimFlightController)
    getattr(mock_airsim_adapter, fc_method).return_value = True
    writer = CommandWriter(mock_airsim_adapter)

    formation = FlightIntent(IntentSource.FORMATION, IntentAction.MOVE_VELOCITY)
    critical = FlightIntent(
        IntentSource.MANUAL,
        action,
        params={"altitude": 7.0} if action == IntentAction.TAKEOFF else {},
    )
    winner = arbiter.select_winner({
        IntentSource.FORMATION: formation,
        IntentSource.MISSION: FlightIntent(IntentSource.MISSION, IntentAction.GOTO),
        IntentSource.MANUAL: critical,
    })

    assert winner is critical
    assert await writer.execute(winner) is True
    getattr(mock_airsim_adapter, fc_method).assert_awaited_once_with(*expected_args)


def test_collision_and_safety_outrank_critical_manual():
    arbiter = Arbiter()
    manual = FlightIntent(IntentSource.MANUAL, IntentAction.RTL)
    formation = FlightIntent(IntentSource.FORMATION, IntentAction.MOVE_VELOCITY)
    collision = FlightIntent(IntentSource.COLLISION, IntentAction.HOVER)
    safety = FlightIntent(IntentSource.SAFETY, IntentAction.LAND)

    winner = arbiter.select_winner({
        IntentSource.MANUAL: manual,
        IntentSource.FORMATION: formation,
        IntentSource.COLLISION: collision,
    })
    assert winner is collision

    winner = arbiter.select_winner({
        IntentSource.MANUAL: manual,
        IntentSource.FORMATION: formation,
        IntentSource.COLLISION: collision,
        IntentSource.SAFETY: safety,
    })
    assert winner is safety


def test_stale_move_or_hover_cannot_replace_critical_manual():
    state_store = FlightStateStore()
    critical = FlightIntent(IntentSource.MANUAL, IntentAction.LAND)
    assert state_store.submit_intent(critical) is True
    assert state_store.submit_intent(FlightIntent(IntentSource.MANUAL, IntentAction.MOVE_VELOCITY)) is False
    assert state_store.submit_intent(FlightIntent(IntentSource.MANUAL, IntentAction.HOVER)) is False
    assert state_store.get_intents()[IntentSource.MANUAL] is critical
    assert state_store.complete_intent(critical) is True


@pytest.mark.asyncio
async def test_command_handler_waits_for_pipeline_result_before_accepted():
    handler = CommandHandler(node_id="drone1")
    stages = []
    handler._send_lifecycle = lambda _sender, _action, stage, **_kwargs: stages.append(stage)

    async def submit_takeoff(params):
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.TAKEOFF, params=params)

        async def pipeline_result():
            await asyncio.sleep(0)
            await handler.on_pipeline_intent_dispatched(intent)
            await handler.on_pipeline_intent_result(intent, True)

        asyncio.create_task(pipeline_result())
        return True

    handler.register_handler(CommandAction.TAKEOFF, submit_takeoff)
    message = ControlMessage(
        msg_type=MessageType.CONTROL,
        sender_id="gcs",
        target_id="drone1",
        timestamp=time.time(),
        action=CommandAction.TAKEOFF,
        params={"altitude_m": 1.0},
        cmd_id="lifecycle-takeoff",
    )

    assert await handler.handle_command(message) is True
    assert stages == ["BACKEND_RECEIVED", "SENDING", "AIRSIM_DISPATCHED", "AIRSIM_RESULT", "ACCEPTED"]


@pytest.mark.asyncio
async def test_command_handler_does_not_accept_failed_pipeline_result():
    handler = CommandHandler(node_id="drone1")
    stages = []
    handler._send_lifecycle = lambda _sender, _action, stage, **_kwargs: stages.append(stage)

    async def submit_land(params):
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.LAND, params=params)

        async def pipeline_result():
            await asyncio.sleep(0)
            await handler.on_pipeline_intent_dispatched(intent)
            await handler.on_pipeline_intent_result(intent, False)

        asyncio.create_task(pipeline_result())
        return True

    handler.register_handler(CommandAction.LAND, submit_land)
    message = ControlMessage(
        msg_type=MessageType.CONTROL,
        sender_id="gcs",
        target_id="drone1",
        timestamp=time.time(),
        action=CommandAction.LAND,
        params={},
        cmd_id="lifecycle-land",
    )

    assert await handler.handle_command(message) is False
    assert stages == ["BACKEND_RECEIVED", "SENDING", "AIRSIM_DISPATCHED", "AIRSIM_RESULT", "FAILED"]

def test_intent_expiration():
    arbiter = Arbiter()
    
    # Expired collision intent vs fresh mission intent
    mission_intent = FlightIntent(IntentSource.MISSION, IntentAction.GOTO)
    collision_intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY, ttl_seconds=0.1)
    
    # Force expiration
    collision_intent.timestamp = time.monotonic() - 1.0 
    
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

