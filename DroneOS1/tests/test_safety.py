from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from DroneOS1.core.safety import SafetyModule
from DroneOS1.shared.protocol.messages import TelemetryData

@pytest.mark.asyncio
async def test_emergency_stop_kills_and_aborts_mission():
    fc = AsyncMock()
    safety = SafetyModule(fc)
    safety.set_mission_manager(SimpleNamespace(abort_mission=MagicMock()))

    await safety.trigger_emergency_stop()

    fc.move_velocity.assert_awaited_once_with(0.0, 0.0, 0.0, 1.0, 0.0)
    fc.kill.assert_awaited_once()
    safety.mission_manager.abort_mission.assert_called_once()

@pytest.mark.asyncio
async def test_connection_lost_disarmed_waits():
    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(flight_mode="GUIDED", armed_state="DISARMED")
    safety = SafetyModule(fc)

    await safety.trigger_connection_lost_failsafe()

    fc.land.assert_not_awaited()
    fc.rtl.assert_not_awaited()

@pytest.mark.asyncio
async def test_connection_lost_indoor_lands():
    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", home_valid=True)
    safety = SafetyModule(fc, config=SimpleNamespace(profile="indoor"))

    await safety.trigger_connection_lost_failsafe()

    fc.land.assert_awaited_once()

@pytest.mark.asyncio
async def test_connection_lost_outdoor_uses_rtl_when_home_valid():
    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", home_valid=True)
    safety = SafetyModule(fc, config=SimpleNamespace(profile="outdoor"))

    await safety.trigger_connection_lost_failsafe()

    fc.rtl.assert_awaited_once()

@pytest.mark.asyncio
async def test_connection_lost_outdoor_lands_when_home_invalid():
    fc = AsyncMock()
    fc.get_telemetry.return_value = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", home_valid=False)
    safety = SafetyModule(fc, config=SimpleNamespace(profile="outdoor"))

    await safety.trigger_connection_lost_failsafe()

    fc.land.assert_awaited_once()

@pytest.mark.asyncio
async def test_low_battery_indoor_lands_and_outdoor_rtls():
    indoor_fc = AsyncMock()
    outdoor_fc = AsyncMock()

    await SafetyModule(indoor_fc, config=SimpleNamespace(profile="indoor")).trigger_low_battery_failsafe()
    await SafetyModule(outdoor_fc, config=SimpleNamespace(profile="outdoor")).trigger_low_battery_failsafe()

    indoor_fc.land.assert_awaited_once()
    outdoor_fc.rtl.assert_awaited_once()

@pytest.mark.asyncio
async def test_critical_battery_lands_and_gps_degraded_hovers():
    fc = AsyncMock()
    safety = SafetyModule(fc)

    await safety.trigger_critical_battery_failsafe()
    await safety.trigger_gps_degraded_failsafe()

    fc.land.assert_awaited_once()
    fc.hover.assert_awaited_once()
