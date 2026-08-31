from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from DroneOS.main import DroneOSApp
from DroneOS.sensors.gps_monitor import GpsMonitor
from DroneOS.shared.protocol.messages import TelemetryData

@pytest.mark.asyncio
async def test_gps_monitor_degraded_and_restored_callbacks_fire_once_per_transition():
    monitor = GpsMonitor()
    monitor.on_gps_degraded = AsyncMock()
    monitor.on_gps_restored = AsyncMock()

    await monitor.evaluate_telemetry(TelemetryData(flight_mode="GUIDED", gps_valid=False))
    await monitor.evaluate_telemetry(TelemetryData(flight_mode="GUIDED", gps_valid=False))
    await monitor.evaluate_telemetry(TelemetryData(flight_mode="GUIDED", gps_valid=True))

    monitor.on_gps_degraded.assert_awaited_once()
    monitor.on_gps_restored.assert_awaited_once()

@pytest.mark.asyncio
async def test_gps_degradation_in_gps_mode_triggers_hover_failsafe():
    telemetry = TelemetryData(flight_mode="GUIDED", armed_state="ARMED", gps_valid=False)
    app = SimpleNamespace(
        flight_controller=SimpleNamespace(get_telemetry=AsyncMock(return_value=telemetry)),
        flight_manager=SimpleNamespace(is_gps_dependent_navigation_active=MagicMock(return_value=True)),
        safety_module=SimpleNamespace(trigger_gps_degraded_failsafe=AsyncMock()),
    )

    await DroneOSApp._handle_gps_degraded(app)

    app.safety_module.trigger_gps_degraded_failsafe.assert_awaited_once()

@pytest.mark.asyncio
async def test_gps_degradation_during_local_navigation_has_no_effect():
    telemetry = TelemetryData(
        flight_mode="ALTCTL",
        armed_state="ARMED",
        gps_valid=False,
        local_pos_valid=True,
    )
    app = SimpleNamespace(
        flight_controller=SimpleNamespace(get_telemetry=AsyncMock(return_value=telemetry)),
        flight_manager=SimpleNamespace(is_gps_dependent_navigation_active=MagicMock(return_value=False)),
        safety_module=SimpleNamespace(trigger_gps_degraded_failsafe=AsyncMock()),
    )

    await DroneOSApp._handle_gps_degraded(app)

    app.safety_module.trigger_gps_degraded_failsafe.assert_not_awaited()
