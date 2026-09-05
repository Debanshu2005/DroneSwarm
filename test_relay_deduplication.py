import asyncio
import json
import socket
import time
import pytest
from relay.relay import UdpWebsocketRelay

@pytest.mark.asyncio
async def test_relay_deduplication():
    # Start the relay on random ports
    relay = UdpWebsocketRelay(ws_port=0, udp_bind_port=0)
    
    # We don't want to run the whole server loop, just test the method
    relay.seen_messages = {}
    relay.known_endpoints = {}
    
    # Mock active_websockets and transport
    class MockWS:
        def __init__(self):
            self.sent_messages = []
        async def send(self, data):
            self.sent_messages.append(data)
            
    ws = MockWS()
    relay.active_websockets.add(ws)
    
    now = time.time()
    
    # Message 1 (Logical identity)
    msg1 = {
        "msg_type": "telemetry",
        "sender_id": "drone3",
        "timestamp": now,
        "telemetry": {}
    }
    
    # Encode and send as if from 127.0.0.1
    data1 = json.dumps(msg1).encode('utf-8')
    await relay.forward_udp_to_ws(data1, ("127.0.0.1", 14551))
    
    # Ensure it was sent
    assert len(ws.sent_messages) == 1
    
    # Send the exact same message but from LAN IP
    await relay.forward_udp_to_ws(data1, ("10.0.0.12", 14551))
    
    # Ensure it was DROPPED (length should still be 1)
    assert len(ws.sent_messages) == 1
    
    # Send a new message from the same drone, slightly later
    msg2 = {
        "msg_type": "telemetry",
        "sender_id": "drone3",
        "timestamp": now + 0.5,
        "telemetry": {}
    }
    data2 = json.dumps(msg2).encode('utf-8')
    await relay.forward_udp_to_ws(data2, ("127.0.0.1", 14551))
    
    # Ensure it was sent
    assert len(ws.sent_messages) == 2
    
    print("Deduplication test passed!")
    
if __name__ == "__main__":
    asyncio.run(test_relay_deduplication())
