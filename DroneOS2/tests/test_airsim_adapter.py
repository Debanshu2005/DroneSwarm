import sys
import math
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
import time

class FakeFuture:
    def __init__(self, result=None):
        self.result = result
    def join(self):
        return self.result

class FakeAirSim:
    class YawMode:
        def __init__(self, is_rate, yaw_or_rate):
            self.is_rate = is_rate
            self.yaw_or_rate = yaw_or_rate

    class DrivetrainType:
        MaxDegreeOfFreedom = 0

    @staticmethod
    def to_eularian_angles(q):
        return (0.1, 0.2, 0.3)

    class MultirotorClient:
        def __init__(self, ip, port):
            self.ip = ip
            self.port = port
            self.confirmConnection = MagicMock()
            self.enableApiControl = MagicMock()
            self.armDisarm = MagicMock(return_value=True)
            self.takeoffAsync = MagicMock(return_value=FakeFuture())
            self.moveToZAsync = MagicMock(return_value=FakeFuture())
            self.landAsync = MagicMock(return_value=FakeFuture())
            self.goHomeAsync = MagicMock(return_value=FakeFuture())
            self.hoverAsync = MagicMock(return_value=FakeFuture())
            self.moveByVelocityBodyFrameAsync = MagicMock(return_value=FakeFuture())
            self.moveByVelocityAsync = MagicMock(return_value=FakeFuture())
            self.moveToPositionAsync = MagicMock(return_value=FakeFuture())
            
            geo_point = MagicMock()
            geo_point.latitude = 47.641468
            geo_point.longitude = -122.140165
            geo_point.altitude = 122.0
            
            self.getHomeGeoPoint = MagicMock(return_value=geo_point)
            
            gps_data = MagicMock()
            gps_data.gnss.geo_point = geo_point
            self.getGpsData = MagicMock(return_value=gps_data)
            
            state = MagicMock()
            state.gps_location = geo_point
            state.kinematics_estimated.position.z_val = -10.0
            state.kinematics_estimated.linear_velocity.x_val = 1.0
            state.kinematics_estimated.linear_velocity.y_val = 2.0
            state.kinematics_estimated.linear_velocity.z_val = 0.5
            state.kinematics_estimated.orientation = MagicMock()
            state.can_arm = True
            state.landed_state = 1
            self.getMultirotorState = MagicMock(return_value=state)

sys.modules['airsim'] = FakeAirSim

from DroneOS2.adapters.airsim_adapter import AirSimFlightController
from DroneOS2.adapters.factory import AdapterFactory
from DroneOS2.shared.config.models import FlightConfig, DroneConfig

@pytest.fixture
def flight_config():
    return FlightConfig(
        adapter_type="airsim",
        takeoff_altitude=10.0,
        max_velocity=5.0,
        pipeline_hz=20.0,
        px4_connection_string="",
        airsim_host="127.0.0.1",
        airsim_port=41451,
        airsim_timeout=5.0,
        airsim_retry_count=2
    )

@pytest.fixture
def adapter(flight_config):
    return AirSimFlightController("Drone1", flight_config)

@pytest.mark.asyncio
async def test_connect_success(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        assert await adapter.connect()
        assert adapter._connected
        assert adapter._mode == "HOLD"
        assert adapter.client.enableApiControl.call_args == call(True, "Drone1")
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_connect_failure(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient') as mock_client:
        mock_client.side_effect = Exception("Connection Refused")
        assert not await adapter.connect()
        assert not adapter._connected

@pytest.mark.asyncio
async def test_armed_state_transitions(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        assert adapter._armed == False
        
        # Test Arm
        assert await adapter.arm()
        assert adapter._armed == True
        assert adapter.client.armDisarm.call_args == call(True, "Drone1")
        
        # Test Disarm
        assert await adapter.disarm()
        assert adapter._armed == False
        assert adapter.client.armDisarm.call_args == call(False, "Drone1")
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_telemetry_loop(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await asyncio.sleep(0.2)
        telemetry = await adapter.get_telemetry()
        assert telemetry.flight_mode == "HOLD"
        assert telemetry.altitude == 10.0 # -(-10.0)
        assert telemetry.pitch > 0.0
        assert telemetry.gps_valid == True
        assert telemetry.is_armable == True
        assert telemetry.armed_state == "DISARMED"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_body_vs_ned_api(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        
        # Body frame
        await adapter.move_velocity(10.0, 2.0, 3.0, 0.1, 0.0)
        # Clamped to 5.0
        adapter.client.moveByVelocityBodyFrameAsync.assert_called_once()
        args = adapter.client.moveByVelocityBodyFrameAsync.call_args[0]
        assert args[0] == 5.0  # vx
        assert args[1] == 2.0  # vy
        assert args[2] == 3.0  # vz
        assert args[3] == 0.1  # duration
        
        # NED frame
        await adapter.move_velocity_ned(-10.0, 2.0, -3.0, 0.1, 0.0)
        adapter.client.moveByVelocityAsync.assert_called_once()
        args = adapter.client.moveByVelocityAsync.call_args[0]
        assert args[0] == -5.0  # north
        assert args[1] == 2.0  # east
        assert args[2] == -3.0  # down
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_goto_location(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        # Home lat=47.641468, lon=-122.140165
        lat = 47.641568 # diff = 0.0001
        lon = -122.140165
        alt = 20.0
        assert await adapter.goto_location(lat, lon, alt)
        adapter.client.moveToPositionAsync.assert_called_once()
        args = adapter.client.moveToPositionAsync.call_args[0]
        assert math.isclose(args[0], 0.0001 * 111320.0)
        assert args[1] == 0.0
        assert args[2] == -20.0
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_rtl_flow(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        assert await adapter.rtl()
        adapter.client.goHomeAsync.assert_called_once()
        adapter.client.landAsync.assert_called_once()
        assert adapter._mode == "RTL"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_disconnect_awaits_task(adapter):
    with patch('DroneOS2.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        task = adapter._telemetry_task
        assert not task.done()
        await adapter.disconnect()
        assert task.done()

def test_factory_returns_airsim(flight_config):
    drone_cfg = DroneConfig(drone_id="d1", vehicle_name="Drone1")
    fc = AdapterFactory.create_flight_controller(drone_cfg, flight_config)
    assert isinstance(fc, AirSimFlightController)
    assert fc.vehicle_name == "Drone1"
