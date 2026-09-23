import pytest
import asyncio
from unittest.mock import MagicMock
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from relay import UdpWebsocketRelay

@pytest.mark.asyncio
async def test_groundstation_heartbeat_gating():
    # Instantiate the relay
    relay = UdpWebsocketRelay(gs_heartbeat_interval=0.01)
    
    # Mock the UDP transport
    relay.transport = MagicMock()
    
    # Run one iteration of the loop's logic manually
    # Or just call the loop itself with a timeout/cancel.
    # To test exactly the gating condition:
    
    # 1. Start with empty clients
    assert len(relay.clients) == 0
    await relay._relay_groundstation_heartbeat_loop_iteration() if hasattr(relay, '_relay_groundstation_heartbeat_loop_iteration') else None
    
    # Let's test the logic in the loop directly, or just the gated function if we isolate it.
    # The loop just calls _send_relay_groundstation_heartbeat if self.clients.
    # Let's just run one iteration of the actual loop logic.
    if relay.clients:
        await relay._send_relay_groundstation_heartbeat()
    
    # Assert transport.sendto was not called
    relay.transport.sendto.assert_not_called()
    
    # 2. Simulate client connecting
    mock_ws = MagicMock()
    relay.clients.add(mock_ws)
    
    if relay.clients:
        await relay._send_relay_groundstation_heartbeat()
        
    # Assert transport.sendto WAS called
    assert relay.transport.sendto.call_count == 1
    relay.transport.sendto.reset_mock()
    
    # 3. Simulate client disconnecting
    relay.clients.discard(mock_ws)
    
    if relay.clients:
        await relay._send_relay_groundstation_heartbeat()
        
    # Assert transport.sendto was not called again
    relay.transport.sendto.assert_not_called()

# Alternatively, we can test the loop itself by using a short timeout.
@pytest.mark.asyncio
async def test_groundstation_heartbeat_loop_behavior():
    relay = UdpWebsocketRelay(gs_heartbeat_interval=0.01)
    relay.transport = MagicMock()
    
    # Run the loop in a background task
    task = asyncio.create_task(relay._relay_groundstation_heartbeat_loop())
    
    # Wait a bit, it should not send anything because clients is empty
    await asyncio.sleep(0.05)
    relay.transport.sendto.assert_not_called()
    
    # Simulate client connection
    mock_ws = MagicMock()
    relay.clients.add(mock_ws)
    
    # Wait a bit, it should now send heartbeats
    await asyncio.sleep(0.05)
    assert relay.transport.sendto.call_count >= 1
    relay.transport.sendto.reset_mock()
    
    # Simulate client disconnection
    relay.clients.discard(mock_ws)
    
    # Wait a bit, it should stop sending heartbeats
    await asyncio.sleep(0.05)
    relay.transport.sendto.assert_not_called()
    
    # Cleanup
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
