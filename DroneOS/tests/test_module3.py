import pytest
import sys
from unittest.mock import MagicMock

# Mock airsim module before importing AirSimFlightController
mock_airsim = MagicMock()
sys.modules['airsim'] = mock_airsim

from DroneOS.adapters.airsim_adapter import AirSimFlightController

@pytest.fixture
def mock_client():
    client = MagicMock()
    
    # Mock futures returned by AirSim Async methods
    future = MagicMock()
    future.join.return_value = None
    
    client.takeoffAsync.return_value = future
    client.moveToZAsync.return_value = future
    client.landAsync.return_value = future
    client.goHomeAsync.return_value = future
    client.hoverAsync.return_value = future
    client.moveByVelocityAsync.return_value = future
    
    # Mock state
    state = MagicMock()
    state.kinematics_estimated.position.z_val = -10.0
    state.kinematics_estimated.linear_velocity.x_val = 1.0
    state.kinematics_estimated.linear_velocity.y_val = 0.0
    state.kinematics_estimated.linear_velocity.z_val = 0.0
    state.landed_state = "flying"
    
    client.getMultirotorState.return_value = state
    
    mock_airsim.MultirotorClient.return_value = client
    return client

@pytest.mark.asyncio
async def test_airsim_adapter_connect(mock_client):
    adapter = AirSimFlightController(vehicle_name="Drone1")
    
    success = await adapter.connect()
    assert success
    assert adapter._connected
    
    mock_airsim.MultirotorClient.assert_called_once_with(ip="127.0.0.1")
    mock_client.confirmConnection.assert_called_once()
    mock_client.enableApiControl.assert_called_once_with(True, "Drone1")
    
    await adapter.disconnect()
    mock_client.enableApiControl.assert_called_with(False, "Drone1")
    assert not adapter._connected

@pytest.mark.asyncio
async def test_airsim_adapter_flight_commands(mock_client):
    adapter = AirSimFlightController()
    await adapter.connect()
    
    assert await adapter.arm()
    mock_client.armDisarm.assert_called_with(True, "")
    
    assert await adapter.takeoff(15.0)
    mock_client.takeoffAsync.assert_called_with(vehicle_name="")
    mock_client.moveToZAsync.assert_called_with(-15.0, 5.0, vehicle_name="")
    
    assert await adapter.land()
    mock_client.landAsync.assert_called_with(vehicle_name="")
    
    assert await adapter.rtl()
    mock_client.goHomeAsync.assert_called_with(vehicle_name="")

@pytest.mark.asyncio
async def test_airsim_adapter_telemetry(mock_client):
    adapter = AirSimFlightController()
    await adapter.connect()
    
    telemetry = await adapter.get_telemetry()
    assert telemetry.altitude == 10.0 # -z_val
    assert telemetry.velocity_x == 1.0
    assert telemetry.flight_mode == "flying"
