import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from DroneOS1.core.command_handler import CommandHandler
from DroneOS1.core.swarm_manager import SwarmMembership
from DroneOS1.shared.protocol.messages import CommandAction, ControlMessage, TelemetryData

def make_handler(monkeypatch, require=False, expected_ids="", expected_count=""):
    if require:
        monkeypatch.setenv("REQUIRE_PEERS_BEFORE_ARM", "true")
    else:
        monkeypatch.delenv("REQUIRE_PEERS_BEFORE_ARM", raising=False)
    monkeypatch.setenv("EXPECTED_PEER_IDS", expected_ids)
    monkeypatch.setenv("EXPECTED_PEER_COUNT", expected_count)

    telemetry = TelemetryData(
        timestamp=time.time(),
        flight_mode="GUIDED",
        armed_state="DISARMED",
        gps_valid=True,
        home_valid=True,
    )
    fc = SimpleNamespace(get_telemetry=AsyncMock(return_value=telemetry))
    safety = SimpleNamespace(is_failsafe_active=False)
    health = SimpleNamespace(last_heartbeat_time=time.time(), timeout_seconds=10.0)
    swarm = SwarmMembership("drone_self")
    handler = CommandHandler(
        node_id="drone_self",
        safety_module=safety,
        flight_controller=fc,
        health_monitor=health,
        swarm_manager=swarm,
    )
    handler.register_handler(CommandAction.ARM, AsyncMock(return_value=True))
    return handler, swarm

@pytest.mark.asyncio
async def test_arm_peer_gate_disabled_allows_zero_peers(monkeypatch):
    handler, _ = make_handler(monkeypatch, require=False)
    msg = ControlMessage(sender_id="gs", timestamp=time.time(), action=CommandAction.ARM)

    assert await handler.handle_command(msg) is True

@pytest.mark.asyncio
async def test_arm_peer_gate_enabled_allows_recent_expected_peers(monkeypatch):
    handler, swarm = make_handler(monkeypatch, require=True, expected_ids="drone2,drone3")
    swarm.registry.add_peer("drone2")
    swarm.registry.add_peer("drone3")
    msg = ControlMessage(sender_id="gs", timestamp=time.time(), action=CommandAction.ARM)

    assert await handler.handle_command(msg) is True

@pytest.mark.asyncio
async def test_arm_peer_gate_enabled_rejects_missing_or_stale_peer(monkeypatch):
    handler, swarm = make_handler(monkeypatch, require=True, expected_ids="drone2,drone3")
    swarm.registry.add_peer("drone2")
    swarm.registry.get_peer("drone2").last_seen = time.time() - 30.0
    msg = ControlMessage(sender_id="gs", timestamp=time.time(), action=CommandAction.ARM)

    assert await handler.handle_command(msg) is False
