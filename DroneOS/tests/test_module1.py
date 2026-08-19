import pytest
import time
import asyncio
from DroneOS.shared.protocol.messages import HeartbeatMessage, TelemetryMessage, TelemetryData
from DroneOS.shared.communication.serializers import JsonSerializer
from DroneOS.shared.communication.network_node import UdpNetworkAdapter

def test_json_serializer():
    serializer = JsonSerializer()
    
    # Test Heartbeat
    hb = HeartbeatMessage(sender_id="drone1", timestamp=time.time())
    serialized_hb = serializer.serialize(hb)
    
    assert isinstance(serialized_hb, bytes)
    
    deserialized_hb = serializer.deserialize(serialized_hb)
    assert isinstance(deserialized_hb, HeartbeatMessage)
    assert deserialized_hb.sender_id == "drone1"
    assert deserialized_hb.status == "active"
    
    # Test Telemetry
    tel = TelemetryMessage(
        sender_id="drone2",
        timestamp=time.time(),
        telemetry=TelemetryData(
            battery_level=95.5,
            altitude=10.0,
            latitude=47.641468,
            longitude=-122.140165,
            velocity_x=0.0,
            velocity_y=0.0,
            velocity_z=0.0,
            flight_mode="hover"
        )
    )
    serialized_tel = serializer.serialize(tel)
    deserialized_tel = serializer.deserialize(serialized_tel)
    
    assert isinstance(deserialized_tel, TelemetryMessage)
    assert deserialized_tel.telemetry.battery_level == 95.5
    assert deserialized_tel.telemetry.flight_mode == "hover"

@pytest.mark.asyncio
async def test_udp_network_adapter():
    serializer = JsonSerializer()
    
    # Need slightly different ports for testing locally to avoid conflicts if they bind to 0.0.0.0
    # Actually, with SO_REUSEADDR and SO_BROADCAST, we can bind to same port on some OS, but let's be safe.
    # For a true LAN broadcast, they'd use the same port. Here we just test the abstraction.
    
    node1 = UdpNetworkAdapter("drone1", "0.0.0.0", 14550, serializer)
    node2 = UdpNetworkAdapter("gs1", "0.0.0.0", 14550, serializer)
    
    await node1.start()
    await node2.start()
    
    received_messages = []
    
    async def msg_handler(msg):
        received_messages.append(msg)
        
    node2.register_callback(msg_handler)
    
    hb = HeartbeatMessage(sender_id="drone1", timestamp=time.time())
    await node1.broadcast_message(hb)
    
    # Yield to event loop to process UDP packet
    await asyncio.sleep(0.1)
    
    assert len(received_messages) == 1
    assert received_messages[0].sender_id == "drone1"
    assert received_messages[0].msg_type == "heartbeat"
    
    await node1.stop()
    await node2.stop()
