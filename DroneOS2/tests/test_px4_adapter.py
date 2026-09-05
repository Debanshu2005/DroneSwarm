import pytest
import asyncio
from unittest.mock import MagicMock, patch

from DroneOS2.adapters.px4_adapter import PX4FlightController
from DroneOS.shared.config.models import FlightConfig

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
async def test_explicit_serial_configuration(base_config, monkeypatch):
    fc = PX4FlightController("drone3", base_config)
    
    # We want to ensure that glob is NOT called if we use an explicit string.
    glob_mock = MagicMock()
    monkeypatch.setattr("glob.glob", glob_mock)
    
    # We mock MAVSDK System to just return immediately
    mock_system = MagicMock()
    
    # Also mock wait_for to prevent actual sleeping
    async def mock_wait_for(coro, timeout):
        return True
    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)
    
    with patch("DroneOS2.adapters.px4_adapter.System", return_value=mock_system):
        # We expect this to try to connect to the exact string in base_config
        await fc.connect()
        
        # Ensure we didn't search USB paths
        glob_mock.assert_not_called()
        
        # Ensure we called connect with the explicit string
        mock_system.connect.assert_called_once_with(system_address="serial:///dev/serial0:57600")
        
        await fc.disconnect()

@pytest.mark.asyncio
async def test_auto_serial_fallback_preserved(base_config, monkeypatch):
    base_config.px4_connection_string = "serial://auto:115200"
    fc = PX4FlightController("drone3", base_config)
    
    glob_mock = MagicMock(return_value=["/dev/ttyUSB0"])
    monkeypatch.setattr("glob.glob", glob_mock)
    
    mock_system = MagicMock()
    
    async def mock_wait_for(coro, timeout):
        return True
    monkeypatch.setattr(asyncio, "wait_for", mock_wait_for)
    
    with patch("DroneOS2.adapters.px4_adapter.System", return_value=mock_system):
        await fc.connect()
        
        # Ensure we DID search USB paths
        assert glob_mock.called
        
        # Because we mocked glob to return /dev/ttyUSB0 and it's drone3, it falls back to the last one
        mock_system.connect.assert_called_once_with(system_address="serial:///dev/ttyUSB0:115200")
        
        await fc.disconnect()
