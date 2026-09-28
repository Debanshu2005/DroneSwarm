import sys
import math
import asyncio
import pytest
from unittest.mock import MagicMock, patch, call
import time

class FakeFuture:
    def __init__(self, result=None, delay=0.0):
        self.result = result
        self.delay = delay
    def join(self):
        if self.delay > 0:
            time.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

class StrictVector3r:
    __slots__ = ['x_val', 'y_val', 'z_val']
    def __init__(self):
        self.x_val = 0.0
        self.y_val = 0.0
        self.z_val = 0.0

class StrictKinematics:
    __slots__ = ['position', 'linear_velocity', 'orientation']
    def __init__(self):
        self.position = StrictVector3r()
        self.linear_velocity = StrictVector3r()
        self.orientation = None

class StrictGeoPoint:
    __slots__ = ['latitude', 'longitude', 'altitude']
    def __init__(self):
        self.latitude = 0.0
        self.longitude = 0.0
        self.altitude = 0.0

class StrictState:
    __slots__ = ['collision', 'kinematics_estimated', 'gps_location', 'timestamp', 'landed_state', 'rc_data', 'ready', 'ready_message', 'can_arm']
    def __init__(self):
        self.collision = None
        self.kinematics_estimated = StrictKinematics()
        self.gps_location = StrictGeoPoint()
        self.timestamp = 0
        self.landed_state = 1
        self.rc_data = None
        self.ready = True
        self.ready_message = ""
        self.can_arm = True

class StrictGpsData:
    __slots__ = ['gnss']
    def __init__(self):
        class Gnss:
            __slots__ = ['geo_point']
            def __init__(self):
                self.geo_point = StrictGeoPoint()
        self.gnss = Gnss()

class FakeAirSim:
    class YawMode:
        def __init__(self, is_rate, yaw_or_rate):
            self.is_rate = is_rate
            self.yaw_or_rate = yaw_or_rate

    class DrivetrainType:
        MaxDegreeOfFreedom = 0

    @staticmethod
    def to_eularian_angles(q):
        return (0.1, 0.2, -math.pi/2)

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
            
            self._home = StrictGeoPoint()
            self._home.latitude = 47.641468
            self._home.longitude = -122.140165
            self._home.altitude = 122.0
            self.getHomeGeoPoint = MagicMock(return_value=self._home)
            
            self._gps = StrictGpsData()
            self._gps.gnss.geo_point.latitude = 47.641468
            self._gps.gnss.geo_point.longitude = -122.140165
            self._gps.gnss.geo_point.altitude = 122.0
            self.getGpsData = MagicMock(return_value=self._gps)
            
            self._state = StrictState()
            self._state.gps_location.latitude = 47.641468
            self._state.gps_location.longitude = -122.140165
            self._state.kinematics_estimated.position.z_val = -10.0
            self._state.kinematics_estimated.linear_velocity.x_val = 1.0
            self._state.kinematics_estimated.linear_velocity.y_val = 2.0
            self._state.kinematics_estimated.linear_velocity.z_val = 0.5
            self.getMultirotorState = MagicMock(return_value=self._state)

sys.modules['airsim'] = FakeAirSim

from DroneOS.adapters.airsim_adapter import AirSimFlightController
from DroneOS.adapters.factory import AdapterFactory
from DroneOS.shared.config.models import FlightConfig, DroneConfig

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
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        assert await adapter.connect()
        assert adapter._connected
        assert adapter._mode == "HOLD"
        assert adapter.client.enableApiControl.call_args == call(True, "Drone1")
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_connect_failure_leaves_clean_state(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient') as mock_client:
        mock_instance = MagicMock()
        mock_instance.getHomeGeoPoint.side_effect = Exception("Connection Refused")
        mock_client.return_value = mock_instance
        
        assert not await adapter.connect()
        assert not adapter._connected
        assert adapter.client is None
        assert adapter._telem_client is None
        assert adapter._telemetry_task is None

def test_ready_task_name_raises():
    s = StrictState()
    with pytest.raises(AttributeError):
        _ = s.ready_task_name

@pytest.mark.asyncio
async def test_telemetry_loop_strict(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await asyncio.sleep(0.2)
        telemetry = await adapter.get_telemetry()
        assert telemetry.flight_mode == "HOLD"
        assert telemetry.altitude == 10.0
        assert math.isclose(telemetry.heading, 270.0) # -90 deg -> 270
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_heartbeat_age_grows_on_error(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter._telem_client.getMultirotorState.side_effect = Exception("Sim crashed")
        await asyncio.sleep(0.2)
        telemetry = await adapter.get_telemetry()
        assert telemetry.heartbeat_age > 0.0
        assert telemetry.flight_mode == "disconnected"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_supersede_goto_with_land(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=2.0)
        
        start = time.time()
        goto_task = asyncio.create_task(adapter.goto_local_ned(10.0, 0.0, -10.0))
        await asyncio.sleep(0.1)
        
        land_task = asyncio.create_task(adapter.land())
        
        land_res = await land_task
        goto_res = await goto_task
        
        duration = time.time() - start
        
        assert goto_res == False
        assert land_res == True
        assert duration < 2.5 # land preempted without waiting 2.0s lock
        assert adapter._mode == "HOLD"
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_supersede_takeoff_middle(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        
        adapter.client.takeoffAsync.return_value = FakeFuture(delay=0.5)
        
        takeoff_task = asyncio.create_task(adapter.takeoff(10.0))
        await asyncio.sleep(0.1)
        
        await adapter.hover()
        
        takeoff_res = await takeoff_task
        
        assert takeoff_res == False
        adapter.client.moveToZAsync.assert_not_called()
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_disarm_on_land(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        
        assert adapter._armed == True
        await adapter.land()
        
        adapter._telem_client._state.landed_state = 0
        await asyncio.sleep(0.2)
        
        assert adapter._armed == False
        telemetry = await adapter.get_telemetry()
        assert telemetry.armed_state == "DISARMED"
        assert adapter._mode == "HOLD"
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_goto_location_no_home(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter._home_geopoint = None
        assert not await adapter.goto_location(47.0, -122.0, 10.0)
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_speed_clamping(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.move_velocity(10.0, -10.0, 0.0, 1.0)
        adapter.client.moveByVelocityBodyFrameAsync.assert_called_once()
        args = adapter.client.moveByVelocityBodyFrameAsync.call_args[0]
        assert args[0] == 5.0
        assert args[1] == -5.0
        assert args[2] == 0.0
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_move_velocity_duration(adapter):
    with patch('DroneOS.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter.client.moveByVelocityBodyFrameAsync.return_value = FakeFuture(delay=0.1)
        start = time.time()
        assert await adapter.move_velocity(1.0, 0.0, 0.0, 0.1)
        dur = time.time() - start
        assert 0.1 <= dur < 0.2
        await adapter.disconnect()

def test_all_abstract_methods_implemented():
    assert "get_home_position" in dir(AirSimFlightController)
    assert "goto_location" in dir(AirSimFlightController)
    assert "move_velocity_ned" in dir(AirSimFlightController)
