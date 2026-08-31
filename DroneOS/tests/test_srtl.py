import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from DroneOS.shared.nlp.trajectory_engine import parse_task_sequence, TaskAction
from DroneOS.core.flight_manager import FlightManager
from DroneOS.core.interfaces import IFlightController
from DroneOS.shared.protocol.messages import TelemetryData

@pytest.fixture
def mock_fc():
    fc = MagicMock(spec=IFlightController)
    fc.get_telemetry = AsyncMock()
    fc.get_home_position = AsyncMock()
    fc.goto_location = AsyncMock()
    fc.land = AsyncMock()
    fc.hover = AsyncMock()
    return fc

@pytest.mark.asyncio
async def test_srtl_parsing():
    seq1 = parse_task_sequence("srtl")
    assert seq1.tasks[0].action == TaskAction.SRTL

    seq2 = parse_task_sequence("smart rtl")
    assert seq2.tasks[0].action == TaskAction.SRTL

    seq3 = parse_task_sequence("rtl")
    assert seq3.tasks[0].action == TaskAction.RTL

@pytest.mark.asyncio
async def test_smart_rtl_rejection_low_altitude(mock_fc):
    manager = FlightManager(mock_fc, min_srtl_altitude_m=2.0)
    mock_fc.get_telemetry.return_value = TelemetryData(
        altitude=1.5, flight_mode="GUIDED", gps_valid=True
    )
    result = await manager.smart_rtl()
    assert result is False
    mock_fc.goto_location.assert_not_called()

@pytest.mark.asyncio
async def test_smart_rtl_rejection_no_home(mock_fc):
    manager = FlightManager(mock_fc, min_srtl_altitude_m=2.0)
    mock_fc.get_telemetry.return_value = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=True
    )
    mock_fc.get_home_position.return_value = None
    result = await manager.smart_rtl()
    assert result is False
    mock_fc.goto_location.assert_not_called()

@pytest.mark.asyncio
async def test_smart_rtl_loop_altitude_tracking_and_arrival(mock_fc):
    manager = FlightManager(mock_fc, min_srtl_altitude_m=2.0)
    
    mock_fc.get_telemetry.return_value = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=True,
        latitude=10.0, longitude=20.0
    )
    mock_fc.get_home_position.return_value = (10.0001, 20.0001, 0.0)
    
    result = await manager.smart_rtl()
    assert result is True
    
    await asyncio.sleep(0.1)
    mock_fc.goto_location.assert_called_with(10.0001, 20.0001, 5.0, yaw=0.0)
    
    mock_fc.get_telemetry.return_value = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=True,
        latitude=10.0001, longitude=20.0001
    )
    
    await asyncio.sleep(0.6)
    mock_fc.land.assert_called_once()
    assert manager._active_flight_task.done()

@pytest.mark.asyncio
async def test_smart_rtl_gps_degraded(mock_fc):
    manager = FlightManager(mock_fc, min_srtl_altitude_m=2.0)
    
    mock_fc.get_telemetry.return_value = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=True,
        latitude=10.0, longitude=20.0
    )
    mock_fc.get_home_position.return_value = (10.0001, 20.0001, 0.0)
    
    result = await manager.smart_rtl()
    
    await asyncio.sleep(0.1)
    mock_fc.goto_location.reset_mock()
    
    mock_fc.get_telemetry.return_value = TelemetryData(
        altitude=5.0, flight_mode="GUIDED", gps_valid=False,
        latitude=10.0, longitude=20.0
    )
    
    await asyncio.sleep(0.6)
    mock_fc.goto_location.assert_not_called()
