import pytest
from unittest.mock import MagicMock, AsyncMock
from DroneOS2.shared.communication.network_node import UdpNetworkAdapter
from DroneOS2.shared.communication.serializers import JsonSerializer
from DroneOS2.shared.protocol.messages import HeartbeatMessage

def test_default_serialization_omits_empty_hmac_sig():
    serializer = JsonSerializer()
    msg = HeartbeatMessage(sender_id="test_node", status="active", timestamp=12345.0)

    assert b"hmac_sig" not in serializer.serialize(msg)

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
    
    # Verify sendto was called exactly 3 times (Local, Discovery, External Relay)
    assert adapter.transport.sendto.call_count == 3
    
    calls = adapter.transport.sendto.call_args_list
    assert calls[0][0][1] == ("127.0.0.1", 14551)
    assert calls[1][0][1] == ("255.255.255.255", 14550)
    assert calls[2][0][1] == ("255.255.255.255", 14551)

@pytest.mark.asyncio
async def test_udp_hmac_signs_and_accepts_when_secret_set(monkeypatch):
    monkeypatch.setenv("DRONE_NET_SECRET", "shared-secret")
    serializer = JsonSerializer()
    sender = UdpNetworkAdapter("sender", "0.0.0.0", 14550, "255.255.255.255", serializer)
    receiver = UdpNetworkAdapter("receiver", "0.0.0.0", 14550, "255.255.255.255", serializer)
    callback = AsyncMock()
    receiver.register_callback(callback)

    msg = HeartbeatMessage(sender_id="sender", status="active", timestamp=12345.0)
    signed = sender._sign_message(msg)

    assert signed.hmac_sig
    await receiver._handle_incoming(serializer.serialize(signed), ("127.0.0.1", 45000))

    callback.assert_awaited_once()

@pytest.mark.asyncio
async def test_udp_hmac_drops_unsigned_or_wrong_signature_when_secret_set(monkeypatch):
    monkeypatch.setenv("DRONE_NET_SECRET", "shared-secret")
    serializer = JsonSerializer()
    receiver = UdpNetworkAdapter("receiver", "0.0.0.0", 14550, "255.255.255.255", serializer)
    callback = AsyncMock()
    receiver.register_callback(callback)

    unsigned = HeartbeatMessage(sender_id="sender", status="active", timestamp=12345.0)
    bad_sig = unsigned.model_copy(update={"hmac_sig": "bad"})

    await receiver._handle_incoming(serializer.serialize(unsigned), ("127.0.0.1", 45000))
    await receiver._handle_incoming(serializer.serialize(bad_sig), ("127.0.0.1", 45000))

    callback.assert_not_awaited()
