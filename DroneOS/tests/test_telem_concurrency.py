import threading
import asyncio
import sys
import math
import pytest
from unittest.mock import MagicMock, patch

# Import the shared mock objects from the main adapter test module.
# They are defined at module level in test_airsim_adapter.py, so we
# re-import from there to avoid duplicating the FakeAirSim definition.
sys.path.insert(0, "DroneOS/tests")

# Replicate the minimal FakeAirSim / fixture needed so this file is
# self-contained and can run in isolation.

class StrictVector3r:
    __slots__ = ['x_val', 'y_val', 'z_val']
    def __init__(self):
        self.x_val = 0.0; self.y_val = 0.0; self.z_val = 0.0

class StrictKinematics:
    __slots__ = ['position', 'linear_velocity', 'orientation']
    def __init__(self):
        self.position = StrictVector3r()
        self.linear_velocity = StrictVector3r()
        self.orientation = None

class StrictGeoPoint:
    __slots__ = ['latitude', 'longitude', 'altitude']
    def __init__(self):
        self.latitude = 0.0; self.longitude = 0.0; self.altitude = 0.0

class StrictState:
    __slots__ = ['collision', 'kinematics_estimated', 'gps_location',
                 'timestamp', 'landed_state', 'rc_data', 'ready',
                 'ready_message', 'can_arm']
    def __init__(self):
        self.collision = None
        self.kinematics_estimated = StrictKinematics()
        self.gps_location = StrictGeoPoint()
        self.timestamp = 0
        self.landed_state = 1
        self.rc_data = None
        self.ready = True; self.ready_message = ""; self.can_arm = True

class FakeAirSimConcurrency:
    class YawMode:
        def __init__(self, is_rate, yaw_or_rate):
            pass
    class DrivetrainType:
        MaxDegreeOfFreedom = 0
    @staticmethod
    def to_eularian_angles(q):
        return (0.1, 0.2, -math.pi / 2)
    class MultirotorClient:
        def __init__(self, ip, port):
            self.confirmConnection = MagicMock()
            self.enableApiControl = MagicMock()
            self.armDisarm = MagicMock(return_value=True)
            self.takeoffAsync = MagicMock(return_value=MagicMock(join=MagicMock()))
            self.landAsync = MagicMock(return_value=MagicMock(join=MagicMock()))
            self.hoverAsync = MagicMock(return_value=MagicMock(join=MagicMock()))
            self._home = StrictGeoPoint()
            self._home.latitude = 47.0; self._home.longitude = -122.0; self._home.altitude = 100.0
            self.getHomeGeoPoint = MagicMock(return_value=self._home)
            self._state = StrictState()
            self.getMultirotorState = MagicMock(return_value=self._state)

sys.modules['airsim'] = FakeAirSimConcurrency

from DroneOS.adapters.airsim_adapter import AirSimFlightController
from DroneOS.shared.config.models import FlightConfig

@pytest.fixture
def adapter():
    cfg = FlightConfig(
        adapter_type="airsim", takeoff_altitude=10.0, max_velocity=5.0,
        pipeline_hz=20.0, px4_connection_string="",
        airsim_host="127.0.0.1", airsim_port=41451,
    )
    return AirSimFlightController("Drone1", cfg)


@pytest.mark.asyncio
async def test_telem_lock_serializes_concurrent_state_reads(adapter):
    # Regression for: 'Existing exports of data: object cannot be re-sized'.
    # _telemetry_loop, _watch_goto and _rtl_sequence all call
    # getMultirotorState via asyncio.to_thread. Without _telem_lock those
    # can run concurrently in the default thread-pool and corrupt the
    # msgpack-rpc/numpy buffers. This test confirms the lock prevents overlap.
    with patch("DroneOS.adapters.airsim_adapter.airsim.MultirotorClient",
               FakeAirSimConcurrency.MultirotorClient):
        await adapter.connect()

        call_log = []
        real_get = adapter._telem_client.getMultirotorState
        overlap_detected = threading.Event()
        in_call = threading.Event()

        def tracked_get(vehicle_name):
            if in_call.is_set():
                overlap_detected.set()
            in_call.set()
            try:
                return real_get(vehicle_name)
            finally:
                in_call.clear()
                call_log.append(1)

        adapter._telem_client.getMultirotorState = tracked_get

        async def locked_read():
            for _ in range(3):
                async with adapter._telem_lock:
                    await asyncio.to_thread(
                        adapter._telem_client.getMultirotorState,
                        adapter.vehicle_name,
                    )
                await asyncio.sleep(0.01)

        # Three concurrent coroutines all competing for _telem_lock.
        await asyncio.gather(locked_read(), locked_read(), locked_read())

        assert not overlap_detected.is_set(), (
            "Concurrent getMultirotorState calls detected: _telem_lock is not working"
        )
        assert len(call_log) >= 9

        await adapter.disconnect()
