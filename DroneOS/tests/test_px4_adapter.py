"""
Unit tests for DroneOS/adapters/px4_adapter.py.
Verifies set_mode behavior, MAV_CMD_DO_SET_MODE payload generation,
rejection on unconfirmed timeout, safety guards while armed, and keepalive management.
"""

import json
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from DroneOS.adapters.px4_adapter import PX4FlightController
from DroneOS.shared.config.models import FlightConfig
from DroneOS.shared.protocol.messages import TelemetryData
from mavsdk.mavlink_direct import MavlinkMessage


@pytest.fixture
def base_config():
    return FlightConfig(
        adapter_type="px4",
        takeoff_altitude=10.0,
        max_velocity=5.0,
        px4_connection_string="serial:///dev/serial0:57600",
        airsim_host="127.0.0.1",
        airsim_port=41451,
        airsim_timeout=5.0,
        airsim_retry_count=3,
        autopilot="auto",
        allow_manual_when_armed=False
    )


@pytest.mark.asyncio
async def test_set_mode_confirmation_timeout_returns_false(base_config):
    """
    When telemetry does not confirm the mode within 3 seconds, set_mode must return False
    and record the last rejection reason.
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    # Set initial telemetry to POSCTL and never confirm HOLD
    fc._telemetry = TelemetryData(flight_mode="POSCTL", native_flight_mode="POSCTL", armed_state="DISARMED")

    with patch("asyncio.sleep", AsyncMock()):
        success = await fc.set_mode("HOLD")

    assert success is False
    assert "requested HOLD, FC is in POSCTL" in fc.last_rejection_reason


@pytest.mark.asyncio
async def test_set_mode_stabilize_no_longer_raises(base_config):
    """
    STABILIZE/STABILIZED must send DO_SET_MODE and no longer raise RuntimeError.
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    # Telemetry confirms STABILIZED immediately
    fc._telemetry = TelemetryData(flight_mode="STABILIZED", native_flight_mode="STABILIZED", armed_state="DISARMED")

    with patch("asyncio.sleep", AsyncMock()):
        success = await fc.set_mode("STABILIZE")

    assert success is True
    assert fc.last_rejection_reason == ""
    fc.client.mavlink_direct.send_message.assert_awaited_once()
    msg = fc.client.mavlink_direct.send_message.call_args[0][0]
    payload = json.loads(msg.fields_json)
    assert payload["command"] == 176
    assert payload["param1"] == 1.0  # MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
    assert payload["param2"] == 7.0  # PX4 main mode 7 = STABILIZED
    assert payload["param3"] == 0.0


@pytest.mark.asyncio
async def test_do_set_mode_payload_px4(base_config):
    """
    Verify DO_SET_MODE payload for PX4 (e.g. POSCTL -> main=3, sub=0; HOLD -> main=4, sub=3).
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc._autopilot = "px4"
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    # Confirm mode immediately
    fc._telemetry = TelemetryData(flight_mode="POSCTL", native_flight_mode="POSCTL", armed_state="DISARMED")

    with patch("asyncio.sleep", AsyncMock()):
        await fc.set_mode("POSCTL")

    msg = fc.client.mavlink_direct.send_message.call_args[0][0]
    assert msg.message_name == "COMMAND_LONG"
    payload = json.loads(msg.fields_json)
    assert payload["command"] == 176
    assert payload["param1"] == 1.0
    assert payload["param2"] == 3.0  # POSCTL
    assert payload["param3"] == 0.0

    # Test HOLD (AUTO.LOITER: main=4, sub=3)
    fc._telemetry.native_flight_mode = "HOLD"
    with patch("asyncio.sleep", AsyncMock()):
        await fc.set_mode("HOLD")
    msg_hold = fc.client.mavlink_direct.send_message.call_args[0][0]
    payload_hold = json.loads(msg_hold.fields_json)
    assert payload_hold["param1"] == 1.0
    assert payload_hold["param2"] == 4.0
    assert payload_hold["param3"] == 3.0


@pytest.mark.asyncio
async def test_do_set_mode_payload_ardupilot(base_config):
    """
    Verify DO_SET_MODE payload for ArduPilot (LOITER -> mode 5, ALT_HOLD -> mode 2).
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc._autopilot = "ardupilot"
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    fc._telemetry = TelemetryData(flight_mode="LOITER", native_flight_mode="LOITER", armed_state="DISARMED")

    with patch("asyncio.sleep", AsyncMock()):
        await fc.set_mode("LOITER")

    msg = fc.client.mavlink_direct.send_message.call_args[0][0]
    payload = json.loads(msg.fields_json)
    assert payload["command"] == 176
    assert payload["param1"] == 1.0
    assert payload["param2"] == 5.0  # ArduCopter LOITER
    assert payload["param3"] == 0.0


@pytest.mark.asyncio
async def test_keepalive_stopped_before_switch(base_config):
    """
    Active mode keepalive must be stopped before issuing a new mode command.
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    # Create dummy keepalive task
    mock_task = asyncio.create_task(asyncio.sleep(10))
    fc._mode_keepalive_task = mock_task

    fc._telemetry = TelemetryData(flight_mode="ALTCTL", native_flight_mode="ALTCTL", armed_state="DISARMED")

    with patch("asyncio.sleep", AsyncMock()):
        await fc.set_mode("ALTCTL")

    assert (hasattr(mock_task, "cancelling") and mock_task.cancelling() > 0) or mock_task.cancelled() or mock_task.done()
    assert fc._mode_keepalive_task is None


@pytest.mark.asyncio
async def test_manual_and_acro_blocked_while_armed(base_config):
    """
    MANUAL and ACRO must be rejected when armed if allow_manual_when_armed is False.
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    fc._telemetry = TelemetryData(flight_mode="POSCTL", native_flight_mode="POSCTL", armed_state="ARMED")

    # MANUAL while armed
    res_manual = await fc.set_mode("MANUAL")
    assert res_manual is False
    assert "Cannot switch to MANUAL while armed" in fc.last_rejection_reason
    fc.client.mavlink_direct.send_message.assert_not_called()

    # ACRO while armed
    res_acro = await fc.set_mode("ACRO")
    assert res_acro is False
    assert "Cannot switch to ACRO while armed" in fc.last_rejection_reason
    fc.client.mavlink_direct.send_message.assert_not_called()

    # Allowed when disarmed
    fc._telemetry.armed_state = "DISARMED"
    fc._telemetry.native_flight_mode = "MANUAL"
    with patch("asyncio.sleep", AsyncMock()):
        res_disarmed = await fc.set_mode("MANUAL")
    assert res_disarmed is True


@pytest.mark.asyncio
async def test_unknown_mode_rejected(base_config):
    """
    Unknown or unsettable mode must be rejected with clear reason and not sent.
    """
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    res = await fc.set_mode("NONEXISTENT_MODE")
    assert res is False
    assert "not settable" in fc.last_rejection_reason
    fc.client.mavlink_direct.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_flight_manager_and_command_handler_rejection_propagation(base_config):
    """
    Verify FlightManager.set_mode captures FC rejection reason and CommandHandler sends it in REJECTED lifecycle.
    """
    from DroneOS.core.flight_manager import FlightManager
    from DroneOS.core.command_handler import CommandHandler
    from DroneOS.shared.protocol.messages import ControlMessage, CommandAction
    from types import SimpleNamespace

    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.mavlink_direct = MagicMock()
    fc.client.mavlink_direct.send_message = AsyncMock()

    # Telemetry will not match mode
    fc._telemetry = TelemetryData(flight_mode="POSCTL", native_flight_mode="POSCTL", armed_state="DISARMED")

    fm = FlightManager(flight_controller=fc, state_store=MagicMock())

    safety = SimpleNamespace(is_failsafe_active=False)
    health = SimpleNamespace(last_heartbeat_time=0.0, timeout_seconds=10.0)
    handler = CommandHandler(
        node_id="drone1",
        safety_module=safety,
        flight_controller=fc,
        health_monitor=health,
    )
    handler.flight_manager = fm
    handler.register_handler(CommandAction.SET_MODE, fm.set_mode)
    handler._send_lifecycle = MagicMock()

    msg = ControlMessage(
        sender_id="mobile_app",
        timestamp=0.0,
        action=CommandAction.SET_MODE,
        params={"mode": "HOLD"},
        cmd_id="cmd-123"
    )

    with patch("asyncio.sleep", AsyncMock()):
        result = await handler.handle_command(msg)

    assert result is False
    assert "requested HOLD, FC is in POSCTL" in fm.last_rejection_reason
    handler._send_lifecycle.assert_called_with(
        "mobile_app",
        CommandAction.SET_MODE,
        "REJECTED",
        reason="requested HOLD, FC is in POSCTL",
        cmd_id="cmd-123"
    )

