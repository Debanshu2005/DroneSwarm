import json
from unittest.mock import AsyncMock, MagicMock
import pytest

from relay.relay import UdpWebsocketRelay

class FakeWebSocket:
    def __init__(self, message=None):
        self.message = message
        self.remote_address = ("127.0.0.1", 12345)
        self.close = AsyncMock()

    async def recv(self):
        return self.message

@pytest.mark.asyncio
async def test_websocket_auth_accepts_matching_token(monkeypatch):
    monkeypatch.setenv("RELAY_AUTH_TOKEN", "secret-token")
    relay = UdpWebsocketRelay()
    ws = FakeWebSocket(json.dumps({"type": "AUTH", "token": "secret-token"}))

    assert await relay._authenticate_websocket(ws) is True
    ws.close.assert_not_awaited()

@pytest.mark.asyncio
async def test_websocket_auth_rejects_missing_or_wrong_token(monkeypatch):
    monkeypatch.setenv("RELAY_AUTH_TOKEN", "secret-token")
    relay = UdpWebsocketRelay()
    ws = FakeWebSocket(json.dumps({"type": "AUTH", "token": "wrong"}))

    assert await relay._authenticate_websocket(ws) is False
    ws.close.assert_awaited_once()

@pytest.mark.asyncio
async def test_websocket_auth_unset_accepts_without_handshake(monkeypatch):
    monkeypatch.delenv("RELAY_AUTH_TOKEN", raising=False)
    relay = UdpWebsocketRelay()
    ws = FakeWebSocket()

    assert await relay._authenticate_websocket(ws) is True
    ws.close.assert_not_awaited()

@pytest.mark.asyncio
async def test_relay_signs_udp_command_when_secret_set(monkeypatch):
    monkeypatch.setenv("DRONE_NET_SECRET", "shared-secret")
    relay = UdpWebsocketRelay(udp_broadcast_addr="127.0.0.1")
    relay.transport = MagicMock()
    message = {
        "msg_type": "control",
        "sender_id": "gs_mobile_01",
        "timestamp": 12345.0,
        "target_id": "drone1",
        "action": "arm",
        "params": {},
        "cmd_id": "cmd-1",
    }

    await relay.forward_ws_to_udp(json.dumps(message))

    sent = json.loads(relay.transport.sendto.call_args.args[0].decode("utf-8"))
    assert sent["hmac_sig"]
    assert relay._verify_message_dict(sent, ("127.0.0.1", 14550)) is True

@pytest.mark.asyncio
async def test_relay_drops_unsigned_udp_when_secret_set(monkeypatch):
    monkeypatch.setenv("DRONE_NET_SECRET", "shared-secret")
    relay = UdpWebsocketRelay()
    relay.active_websockets.add(AsyncMock())
    data = json.dumps({
        "msg_type": "heartbeat",
        "sender_id": "drone1",
        "timestamp": 12345.0,
        "target_id": None,
        "status": "active",
    }).encode("utf-8")

    await relay.forward_udp_to_ws(data, ("127.0.0.1", 14550))

    assert relay.known_endpoints == {}
