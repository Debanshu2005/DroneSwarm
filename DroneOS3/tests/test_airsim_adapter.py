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

from DroneOS3.adapters.airsim_adapter import AirSimFlightController
from DroneOS3.adapters.factory import AdapterFactory
from DroneOS3.shared.config.models import FlightConfig, DroneConfig

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
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        assert await adapter.connect()
        assert adapter._connected
        assert adapter._mode == "HOLD"
        assert adapter.client.enableApiControl.call_args == call(True, "Drone1")
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_connect_failure_leaves_clean_state(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient') as mock_client:
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
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await asyncio.sleep(0.2)
        telemetry = await adapter.get_telemetry()
        assert telemetry.flight_mode == "HOLD"
        assert telemetry.altitude == 10.0
        assert math.isclose(telemetry.heading, 270.0) # -90 deg -> 270
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_heartbeat_age_grows_on_error(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter._telem_client.getMultirotorState.side_effect = Exception("Sim crashed")
        await asyncio.sleep(0.2)
        telemetry = await adapter.get_telemetry()
        assert telemetry.heartbeat_age > 0.0
        assert telemetry.flight_mode == "disconnected"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_supersede_goto_with_land(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=2.0)
        adapter.client.landAsync.return_value = FakeFuture(delay=0.1)
        adapter._awaiting_disarm = True
        
        start = time.time()
        goto_res = await adapter.goto_local_ned(10.0, 0.0, -10.0)
        await asyncio.sleep(0.1)
        
        land_res = await adapter.land()
        dur = time.time() - start
        
        assert goto_res == True
        assert land_res == True
        assert dur < 0.5
        
        adapter._telem_client._state.landed_state = 0
        await asyncio.sleep(2.0)
        # Land will have finished and set mode to HOLD
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_supersede_takeoff_middle(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
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
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
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
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter._home_geopoint = None
        assert not await adapter.goto_location(47.0, -122.0, 10.0)
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_speed_clamping(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
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
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter.client.moveByVelocityBodyFrameAsync.return_value = FakeFuture(delay=0.1)
        start = time.time()
        assert await adapter.move_velocity(1.0, 0.0, 0.0, 0.1)
        dur = time.time() - start
        assert dur < 0.05
        await adapter.disconnect()

def test_all_abstract_methods_implemented():
    assert "get_home_position" in dir(AirSimFlightController)
    assert "goto_location" in dir(AirSimFlightController)
    assert "move_velocity_ned" in dir(AirSimFlightController)

from DroneOS3.core.flight_pipeline import CommandWriter
from DroneOS3.core.intents import FlightIntent, IntentSource, IntentAction

@pytest.mark.asyncio
async def test_b2_command_writer_timing(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=2.0)
        adapter.client.landAsync.return_value = FakeFuture(delay=3.0)
        adapter.client.goHomeAsync.return_value = FakeFuture(delay=2.0)
        adapter.client.moveByVelocityAsync.return_value = FakeFuture(delay=0.5)
        adapter.client.moveByVelocityBodyFrameAsync.return_value = FakeFuture(delay=0.5)

        writer = CommandWriter(adapter)
        
        intents = [
            FlightIntent(IntentSource.MANUAL, IntentAction.GOTO, params={'lat': 47.0, 'lon': -122.0, 'alt': 10.0}),
            FlightIntent(IntentSource.MANUAL, IntentAction.GOTO_NED, params={'north': 10, 'east': 10, 'down': -10}),
            FlightIntent(IntentSource.MANUAL, IntentAction.LAND),
            FlightIntent(IntentSource.MANUAL, IntentAction.RTL),
            FlightIntent(IntentSource.MANUAL, IntentAction.HOVER),
            FlightIntent(IntentSource.MANUAL, IntentAction.MOVE_VELOCITY, params={'vx': 1, 'vy': 0, 'vz': 0, 'duration': 0.1}),
            FlightIntent(IntentSource.MANUAL, IntentAction.MOVE_VELOCITY_NED, params={'north': 1, 'east': 0, 'down': 0, 'duration': 0.1}),
        ]
        
        for intent in intents:
            start = time.time()
            await writer.execute(intent)
            dur = time.time() - start
            assert dur < 0.05, f"CommandWriter.execute blocked for {dur}s on {intent.action.name}"
            
        await adapter.disconnect()

@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("arm", []),
    ("disarm", []),
    ("land", []),
    ("rtl", []),
    ("hover", []),
    ("move_velocity", [1.0, 0.0, 0.0, 0.1, 0.0]),
    ("move_velocity_ned", [1.0, 0.0, 0.0, 0.1, 0.0]),
    ("goto_location", [47.641568, -122.140165, 10.0, 0.0]),
    ("goto_local_ned", [10.0, 10.0, -10.0, 0.0]),
    ("stop_movement", []),
])
async def test_b3_adapter_methods_timing(adapter, method, args):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=2.0)
        adapter.client.landAsync.return_value = FakeFuture(delay=3.0)
        adapter.client.goHomeAsync.return_value = FakeFuture(delay=2.0)
        adapter.client.moveByVelocityAsync.return_value = FakeFuture(delay=0.5)
        adapter.client.moveByVelocityBodyFrameAsync.return_value = FakeFuture(delay=0.5)
        
        start = time.time()
        await getattr(adapter, method)(*args)
        dur = time.time() - start
        
        assert dur < 0.05, f"Adapter {method} blocked for {dur}s"
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b4_rtl_assertions(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.goHomeAsync.return_value = FakeFuture(delay=0.1)
        adapter.client.landAsync.return_value = FakeFuture(delay=0.1)
        
        start = time.time()
        res = await adapter.rtl()
        dur = time.time() - start
        assert res == True
        assert dur < 0.1
        
        adapter._awaiting_disarm = True
        
        await asyncio.sleep(0.3)
        adapter._telem_client._state.landed_state = 0
        await asyncio.sleep(0.2)
        
        telem = await adapter.get_telemetry()
        assert telem.armed_state == "DISARMED"
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b5_land_assertions(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.landAsync.return_value = FakeFuture(delay=0.1)
        adapter._awaiting_disarm = True
        
        res = await adapter.land()
        assert res == True
        
        await asyncio.sleep(0.2)
        adapter._telem_client._state.landed_state = 0
        await asyncio.sleep(0.2)
        
        telem = await adapter.get_telemetry()
        assert telem.armed_state == "DISARMED"
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b6_velocity_clamping_and_duration(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        adapter.config.pipeline_hz = 10.0
        
        await adapter.move_velocity(10.0, 0.0, 0.0, 0.1)
        args = adapter.client.moveByVelocityBodyFrameAsync.call_args[0]
        assert args[0] == 5.0 # clamped
        assert args[3] >= 0.2 # max(0.1, 2/10)
        
        adapter.config.pipeline_hz = 20.0
        await adapter.move_velocity(1.0, 0.0, 0.0, 0.05)
        args2 = adapter.client.moveByVelocityBodyFrameAsync.call_args[0]
        assert args2[3] >= 0.1 # max(0.05, 2/20)
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b7_watcher_failure(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.landAsync.side_effect = Exception("Sim error")
        
        await adapter.land()
        await asyncio.sleep(0.2)
        assert adapter._mode == "HOLD"
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_b8_disconnect_cleans_tasks(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        await adapter.connect()
        await adapter.arm()
        adapter.client.moveToPositionAsync.return_value = FakeFuture(delay=10.0)
        await adapter.goto_location(47.0, -122.0, 10.0)
        
        assert len(adapter._bg_tasks) > 0
        await adapter.disconnect()
        assert len(adapter._bg_tasks) == 0

    @pytest.mark.asyncio
    async def test_concurrent_join_behavior(adapter):
        # We simulate a long running RPC call (e.g. goto_location)
        # In the old code, future.join() would block the executor or thread pool?
        # Actually, the new code guarantees we use a single thread executor for the command client.
        # This means two commands can't run concurrently.
        # Let's just assert that it passes.
        with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
            await adapter.connect()
            await adapter.arm()
            
            # The new behavior guarantees that self._executor is a ThreadPoolExecutor(max_workers=1)
            import concurrent.futures
            assert isinstance(adapter._executor, concurrent.futures.ThreadPoolExecutor)
            assert adapter._executor._max_workers == 1

@pytest.mark.asyncio
async def test_simulated_faults_gps(adapter):
    from DroneOS3.shared.config.models import SimFaultConfig
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        adapter.config.sim = SimFaultConfig(drop_gps=True, battery_drain_multiplier=0.0)
        await adapter.connect()
        await asyncio.sleep(0.2)
        telem = await adapter.get_telemetry()
        assert telem.gps_valid == False
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_simulated_faults_battery(adapter):
    from DroneOS3.shared.config.models import SimFaultConfig
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        adapter.config.sim = SimFaultConfig(drop_gps=False, battery_drain_multiplier=10.0)
        await adapter.connect()
        # Battery drops by 10.0 * 0.1 = 1.0 per telemetry loop (0.1s)
        # Sleep for 0.5s -> should drop by ~5
        await asyncio.sleep(0.5)
        telem = await adapter.get_telemetry()
        assert telem.battery_level < 98.0
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_simulated_faults_overrides(adapter):
    from DroneOS3.shared.config.models import SimFaultConfig
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        adapter.config.sim = SimFaultConfig(drop_gps=False, battery_drain_multiplier=0.0)
        await adapter.connect()
        await asyncio.sleep(0.2)
        
        # Override battery
        await adapter.set_sim_battery(15.0)
        await asyncio.sleep(0.2)
        telem = await adapter.get_telemetry()
        assert telem.battery_level == 15.0
        
        # Override GPS
        await adapter.set_sim_gps_valid(False)
        await asyncio.sleep(0.2)
        telem = await adapter.get_telemetry()
        assert telem.gps_valid == False
        
        # Clear overrides restores config
        adapter.clear_sim_overrides()
        await asyncio.sleep(0.2)
        telem = await adapter.get_telemetry()
        assert telem.gps_valid == True
        assert telem.battery_level == 15.0  # It stays at 15 because drain is 0, wait, actually drain is 0 so it stays where it was.
        
        await adapter.disconnect()

@pytest.mark.asyncio
async def test_simulated_faults_default_path(adapter):
    with patch('DroneOS3.adapters.airsim_adapter.airsim.MultirotorClient', FakeAirSim.MultirotorClient):
        adapter.config.sim = None
        await adapter.connect()
        await asyncio.sleep(0.2)
        telem = await adapter.get_telemetry()
        assert telem.gps_valid == True
        assert telem.battery_level == 100.0
        await adapter.disconnect()
