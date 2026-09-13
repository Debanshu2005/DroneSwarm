import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from DroneOS1.adapters.px4_adapter import PX4FlightController
from DroneOS1.shared.config.models import FlightConfig
from DroneOS1.shared.protocol.messages import TelemetryData

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
        airsim_retry_count=3
    )

@pytest.mark.asyncio
async def test_set_mode_timeout_raises_runtime_error(base_config):
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.action.hold = AsyncMock()
    
    # Mock get_telemetry to always return a mode that is NOT the target mode
    async def mock_get_telemetry():
        return TelemetryData(flight_mode="STABILIZE")
    fc.get_telemetry = mock_get_telemetry
    
    # Attempt to set a mode that won't raise an error but will timeout
    with pytest.raises(RuntimeError) as exc_info:
        await fc.set_mode("LOITER")
    
    assert "timed out waiting for telemetry confirmation" in str(exc_info.value)

@pytest.mark.asyncio
async def test_set_mode_raises_on_unsupported_modes(base_config):
    fc = PX4FlightController("drone1", base_config)
    fc._connected = True
    fc.client = MagicMock()
    fc.client.action.hold = AsyncMock()
    
    # AUTO should raise with a specific message
    with pytest.raises(RuntimeError) as exc_info_auto:
        await fc.set_mode("AUTO")
    assert "AUTO mode requires an uploaded mission" in str(exc_info_auto.value)
    
    # STABILIZE should raise with a specific message
    with pytest.raises(RuntimeError) as exc_info_stab:
        await fc.set_mode("STABILIZE")
    assert "cannot currently be set via this interface" in str(exc_info_stab.value)
    
    # Ensure no action or manual_control methods were called
    fc.client.action.hold.assert_not_called()
    fc.client.manual_control.set_manual_control_input.assert_not_called()
    fc.client.manual_control.start_altitude_control.assert_not_called()
