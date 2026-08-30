import pytest
from unittest.mock import MagicMock, AsyncMock
from DroneOS.shared.communication.network_node import UdpNetworkAdapter
from DroneOS.shared.protocol.messages import HeartbeatMessage

@pytest.mark.asyncio
async def test_broadcast_message_sends_to_both_ports():
    # Setup mock serializer
    mock_serializer = MagicMock()
    mock_serializer.serialize.return_value = b'{"mock": "data"}'
    
    # Initialize adapter with a configured peer port
    adapter = UdpNetworkAdapter(
        node_id="test_node",
        host="0.0.0.0",
        port=14550,
        broadcast_address="255.255.255.255",
        serializer=mock_serializer,
        peer_host=None,         # Essential: peer_host must be None for broadcast branch
        peer_port=14551
    )
    
    # Mock the transport
    adapter.transport = MagicMock()
    
    # Send a message
    msg = HeartbeatMessage(sender_id="test_node", status="active", timestamp=12345.0)
    await adapter.broadcast_message(msg)
    
    # Verify sendto was called exactly twice
    assert adapter.transport.sendto.call_count == 2
    
    # Verify the destinations
    calls = adapter.transport.sendto.call_args_list
    
    # First call should be the discovery broadcast (port 14550)
    assert calls[0].args[1] == ("255.255.255.255", 14550)
    
    # Second call should be the relay forward broadcast (port 14551)
    assert calls[1].args[1] == ("255.255.255.255", 14551)
