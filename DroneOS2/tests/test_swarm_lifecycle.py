import pytest
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from DroneOS2.shared.protocol.messages import (
    DroneJoinMessage, DroneLeaveMessage, MessageType,
    SwarmStateMessage, PeerStateMessage, SwarmHeartbeatMessage, TelemetryData
)
from DroneOS2.core.swarm_manager import SwarmMembership
from DroneOS2.core.telemetry_publisher import TelemetryPublisher
from DroneOS2.core.flight_pipeline import FlightPipeline
from DroneOS2.core.intents import FlightIntent, IntentSource, IntentAction

class MockMessage:
    def __init__(self, sender_id, msg_type):
        self.sender_id = sender_id
        self.msg_type = msg_type

@pytest.fixture
def swarm_mgr():
    return SwarmMembership("drone_test")

def test_drone_join_message_construction():
    msg = DroneJoinMessage(
        sender_id="drone_01",
        timestamp=time.time(),
        drone_ip="127.0.0.1",
        drone_port=8080,
        capabilities={"camera": True}
    )
    assert msg.msg_type == MessageType.DRONE_JOIN
    assert msg.drone_ip == "127.0.0.1"
    assert msg.capabilities["camera"] is True

def test_handle_join_and_leave(swarm_mgr):
    join_msg = DroneJoinMessage(
        sender_id="drone_02",
        timestamp=time.time(),
        drone_ip="127.0.0.1",
        drone_port=8081
    )
    swarm_mgr.discovery.handle_join(join_msg)
    peer = swarm_mgr.registry.get_peer("drone_02")
    assert peer is not None

    leave_msg = DroneLeaveMessage(
        sender_id="drone_02",
        timestamp=time.time(),
        reason="shutdown"
    )
    swarm_mgr.removal.handle_leave(leave_msg)
    peer = swarm_mgr.registry.get_peer("drone_02")
    assert peer is None

@pytest.mark.asyncio
async def test_swarm_state_message_construction(swarm_mgr):
    # Setup dependencies
    network = AsyncMock()
    fc = AsyncMock()
    fm = MagicMock()
    mission = MagicMock()
    
    # Fake telemetry with valid GPS so formation engine works
    tel = TelemetryData(
        flight_mode="HOLD",
        gps_valid=True,
        latitude=37.0,
        longitude=-122.0,
        altitude=10.0
    )
    fc.get_telemetry.return_value = tel
    
    # Formation params indicate active formation
    fm.formation_params = {"type": "V", "spacing": 5.0}
    
    # Add an active peer to the registry
    join_msg = DroneJoinMessage(
        sender_id="drone_02",
        timestamp=time.time(),
        drone_ip="127.0.0.1",
        drone_port=8081
    )
    swarm_mgr.discovery.handle_join(join_msg)
    
    # Need to update peer with valid pos
    hb = SwarmHeartbeatMessage(sender_id="drone_02", timestamp=time.time(), status="active", battery_level=90)
    swarm_mgr.heartbeat_mgr.handle_swarm_heartbeat(hb)
    peer2 = swarm_mgr.registry.get_peer("drone_02")
    peer2.lat, peer2.lon, peer2.alt = 37.0001, -122.0001, 10.0
    peer2.last_position_time = time.time()
    
    publisher = TelemetryPublisher(
        node_id="drone_test",
        network_adapter=network,
        flight_controller=fc,
        flight_manager=fm,
        mission_manager=mission,
        swarm_manager=swarm_mgr,
        state_store=MagicMock()
    )
    
    # Run one iteration of the swarm state loop
    publisher._running = True
    async def side_effect(*args, **kwargs):
        publisher._running = False
        raise asyncio.CancelledError()
        
    with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = side_effect
        try:
            await publisher._publish_swarm_state_loop()
        except asyncio.CancelledError:
            pass
        
    network.broadcast_message.assert_called_once()
    msg = network.broadcast_message.call_args[0][0]
    
    assert isinstance(msg, SwarmStateMessage)
    assert msg.formation_type == "V"
    assert len(msg.target_waypoints) > 0

@pytest.mark.asyncio
async def test_peer_state_message_fires_on_intent_change():
    # Setup FlightPipeline
    state_store = MagicMock()
    state_store.smart_rtl_active = False
    fc = AsyncMock()
    config = MagicMock()
    config.pipeline_hz = 10
    decision_engine = AsyncMock()
    
    # Provide sequence of intents: first IDLE, then FORMATION, then FORMATION
    intent_idle = FlightIntent(IntentSource.IDLE, IntentAction.IDLE)
    intent_form = FlightIntent(IntentSource.FORMATION, IntentAction.MOVE_VELOCITY)
    
    # Simulate state store returning these intents over ticks
    # Tick 1: IDLE
    # Tick 2: FORMATION
    # Tick 3: FORMATION
    state_store.get_intents.side_effect = [
        {IntentSource.IDLE: intent_idle},
        {IntentSource.FORMATION: intent_form},
        {IntentSource.FORMATION: intent_form}
    ]
    
    pipeline = FlightPipeline(state_store, fc, config, decision_engine)
    pipeline.on_intent_change = AsyncMock()
    
    # Run the loop for 3 ticks
    pipeline._running = True
    ticks = 0
    async def sleep_side_effect(*args, **kwargs):
        nonlocal ticks
        ticks += 1
        if ticks >= 3:
            pipeline._running = False
            raise asyncio.CancelledError()
            
    with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = sleep_side_effect
        try:
            await pipeline.run_pipeline_loop()
        except asyncio.CancelledError:
            pass
        
    # Should be called exactly twice: once for IDLE (initial), once for FORMATION transition
    assert pipeline.on_intent_change.call_count == 2
    pipeline.on_intent_change.assert_any_call("IDLE")
    pipeline.on_intent_change.assert_any_call("FORMATION")

def test_swarm_heartbeat_manager(swarm_mgr):
    msg = SwarmHeartbeatMessage(
        sender_id="drone_03",
        timestamp=time.time(),
        status="active",
        battery_level=87.5
    )
    swarm_mgr.heartbeat_mgr.handle_swarm_heartbeat(msg)
    
    peer = swarm_mgr.registry.get_peer("drone_03")
    assert peer is not None
    assert peer.is_active is True
    assert peer.battery_level == 87.5
