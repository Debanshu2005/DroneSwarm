import asyncio
import math
import time
from typing import Optional, Tuple

from DroneOS1.core.interfaces import IFlightController
from DroneOS1.shared.config.models import FlightConfig
from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.shared.protocol.messages import TelemetryData

logger = setup_logger("AirSimAdapter")

try:
    import airsim
except ImportError:
    logger.warning("AirSim module not found. Adapter will fail if instantiated without a mock.")
    airsim = None

class AirSimFlightController(IFlightController):
    """
    AirSim flight controller adapter.
    Test hooks:
    - self.sim_battery_level (float): override battery level (default 100.0)
    - self.sim_gps_valid (bool): override GPS validity (default True)
    """
    def __init__(self, vehicle_name: str, config: FlightConfig):
        self.vehicle_name = vehicle_name
        self.config = config
        self.client: Optional['airsim.MultirotorClient'] = None
        self._telem_client: Optional['airsim.MultirotorClient'] = None
        self._connected = False
        self._telemetry = self._empty_telemetry()
        self._telemetry_task = None
        
        self._armed = False
        self._mode = "disconnected"
        self._home_geopoint = None
        
        self.sim_battery_level = 100.0
        self.sim_gps_valid = True
        
        self._cmd_lock = asyncio.Lock()
        
        self._last_error = None
        self._last_error_time = 0.0

    def _empty_telemetry(self) -> TelemetryData:
        return TelemetryData(
            battery_level=100.0, altitude=0.0, latitude=0.0, longitude=0.0,
            velocity_x=0.0, velocity_y=0.0, velocity_z=0.0, flight_mode="disconnected"
        )

    def _log_rate_limited(self, msg: str):
        now = time.time()
        if self._last_error != msg or now - self._last_error_time > 5.0:
            logger.error(msg)
            self._last_error = msg
            self._last_error_time = now

    async def connect(self) -> bool:
        if not airsim:
            logger.error("Cannot connect to AirSim: airsim module is not installed.")
            return False
            
        retry_count = getattr(self.config, 'airsim_retry_count', 3)
        for attempt in range(retry_count):
            try:
                logger.info(f"AirSim CONNECTING to {self.config.airsim_host}:{self.config.airsim_port} (Attempt {attempt+1}/{retry_count})")
                
                self.client = airsim.MultirotorClient(ip=self.config.airsim_host, port=self.config.airsim_port)
                await asyncio.to_thread(self.client.confirmConnection)
                await asyncio.to_thread(self.client.enableApiControl, True, self.vehicle_name)
                
                self._telem_client = airsim.MultirotorClient(ip=self.config.airsim_host, port=self.config.airsim_port)
                await asyncio.to_thread(self._telem_client.confirmConnection)
                
                self._connected = True
                self._mode = "HOLD"
                
                self._home_geopoint = await asyncio.to_thread(self.client.getHomeGeoPoint, self.vehicle_name)
                
                logger.info(f"AirSim CONNECTED to {self.config.airsim_host}")
                self._telemetry_task = asyncio.create_task(self._telemetry_loop())
                return True
            except Exception as e:
                logger.error(f"AirSim connection attempt {attempt+1} failed: {e}")
                self.client = None
                self._telem_client = None
                if attempt < retry_count - 1:
                    await asyncio.sleep(1.0)
                    
        return False

    async def disconnect(self) -> None:
        self._connected = False
        self._armed = False
        self._mode = "disconnected"
        
        if self._telemetry_task:
            self._telemetry_task.cancel()
            try:
                await self._telemetry_task
            except asyncio.CancelledError:
                pass
            self._telemetry_task = None
            
        if self.client:
            try:
                await asyncio.to_thread(self.client.enableApiControl, False, self.vehicle_name)
            except Exception as e:
                logger.error(f"Error disabling API control: {e}")
            self.client = None
            
        self._telem_client = None
        logger.info("AirSim DISCONNECTED")

    async def arm(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            async with self._cmd_lock:
                success = await asyncio.to_thread(self.client.armDisarm, True, self.vehicle_name)
            if success:
                self._armed = True
                self._home_geopoint = await asyncio.to_thread(self.client.getHomeGeoPoint, self.vehicle_name)
            return bool(success)
        except Exception as e:
            logger.error(f"AirSim arm failed: {e}")
            return False

    async def disarm(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            async with self._cmd_lock:
                success = await asyncio.to_thread(self.client.armDisarm, False, self.vehicle_name)
            if success:
                self._armed = False
            return bool(success)
        except Exception as e:
            logger.error(f"AirSim disarm failed: {e}")
            return False

    async def takeoff(self, altitude: float = 10.0) -> bool:
        if not self._connected or self.client is None: return False
        try:
            if not self._armed:
                success = await self.arm()
                if not success:
                    return False
            
            self._mode = "TAKEOFF"
            async with self._cmd_lock:
                takeoff_fut = await asyncio.to_thread(self.client.takeoffAsync, 15.0, self.vehicle_name)
                await asyncio.wait_for(asyncio.to_thread(takeoff_fut.join), timeout=20.0)
                
                max_vel = getattr(self.config, 'max_velocity', 5.0)
                z_fut = await asyncio.to_thread(self.client.moveToZAsync, -altitude, max_vel, 15.0, airsim.YawMode(False, 0), -1, 1, self.vehicle_name)
                await asyncio.wait_for(asyncio.to_thread(z_fut.join), timeout=20.0)
            self._mode = "HOLD"
            return True
        except asyncio.TimeoutError:
            logger.error("AirSim takeoff timed out")
            self._mode = "HOLD"
            return False
        except Exception as e:
            logger.error(f"AirSim takeoff failed: {e}")
            self._mode = "HOLD"
            return False

    async def land(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            self._mode = "LAND"
            async with self._cmd_lock:
                land_fut = await asyncio.to_thread(self.client.landAsync, 30.0, self.vehicle_name)
                await asyncio.to_thread(land_fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim land failed: {e}")
            return False

    async def rtl(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            self._mode = "RTL"
            async with self._cmd_lock:
                rtl_fut = await asyncio.to_thread(self.client.goHomeAsync, 30.0, self.vehicle_name)
                await asyncio.wait_for(asyncio.to_thread(rtl_fut.join), timeout=35.0)
                
                land_fut = await asyncio.to_thread(self.client.landAsync, 30.0, self.vehicle_name)
                await asyncio.wait_for(asyncio.to_thread(land_fut.join), timeout=35.0)
            return True
        except asyncio.TimeoutError:
            logger.error("AirSim RTL timed out")
            return False
        except Exception as e:
            logger.error(f"AirSim RTL failed: {e}")
            return False

    async def hover(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            self._mode = "HOLD"
            async with self._cmd_lock:
                hover_fut = await asyncio.to_thread(self.client.hoverAsync, self.vehicle_name)
                await asyncio.to_thread(hover_fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim hover failed: {e}")
            return False

    async def move_velocity(self, vx: float, vy: float, vz: float, duration: float, yaw_rate: float = 0.0) -> bool:
        if not self._connected or self.client is None: return False
        try:
            max_vel = getattr(self.config, 'max_velocity', 5.0)
            vx = max(-max_vel, min(max_vel, vx))
            vy = max(-max_vel, min(max_vel, vy))
            vz = max(-max_vel, min(max_vel, vz))
            
            yaw_mode = airsim.YawMode(is_rate=True, yaw_or_rate=yaw_rate)
            async with self._cmd_lock:
                fut = await asyncio.to_thread(
                    self.client.moveByVelocityBodyFrameAsync,
                    vx, vy, vz, duration, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, self.vehicle_name
                )
                await asyncio.to_thread(fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim move_velocity failed: {e}")
            return False

    async def move_velocity_ned(self, north: float, east: float, down: float, duration: float, yaw_rate: float = 0.0) -> bool:
        if not self._connected or self.client is None: return False
        try:
            max_vel = getattr(self.config, 'max_velocity', 5.0)
            north = max(-max_vel, min(max_vel, north))
            east = max(-max_vel, min(max_vel, east))
            down = max(-max_vel, min(max_vel, down))
            
            yaw_mode = airsim.YawMode(is_rate=True, yaw_or_rate=yaw_rate)
            async with self._cmd_lock:
                fut = await asyncio.to_thread(
                    self.client.moveByVelocityAsync,
                    north, east, down, duration, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, self.vehicle_name
                )
                await asyncio.to_thread(fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim move_velocity_ned failed: {e}")
            return False

    async def goto_location(self, lat: float, lon: float, alt: float, yaw: float = 0.0) -> bool:
        if not self._connected or self.client is None: return False
        if not self._home_geopoint or math.isnan(self._home_geopoint.latitude):
            logger.warning("goto_location rejected: home position is unavailable.")
            return False
            
        try:
            home_lat = self._home_geopoint.latitude
            home_lon = self._home_geopoint.longitude
            north = (lat - home_lat) * 111320.0
            east = (lon - home_lon) * 111320.0 * math.cos(math.radians(home_lat))
            down = -alt
            
            max_vel = getattr(self.config, 'max_velocity', 5.0)
            yaw_mode = airsim.YawMode(is_rate=False, yaw_or_rate=yaw)
            
            async with self._cmd_lock:
                fut = await asyncio.to_thread(
                    self.client.moveToPositionAsync,
                    north, east, down, max_vel, 60.0, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, -1, 1, self.vehicle_name
                )
                await asyncio.to_thread(fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim goto_location failed: {e}")
            return False

    async def goto_local_ned(self, north: float, east: float, down: float, yaw: float = 0.0) -> bool:
        if not self._connected or self.client is None: return False
        try:
            max_vel = getattr(self.config, 'max_velocity', 5.0)
            yaw_mode = airsim.YawMode(is_rate=False, yaw_or_rate=yaw)
            
            async with self._cmd_lock:
                fut = await asyncio.to_thread(
                    self.client.moveToPositionAsync,
                    north, east, down, max_vel, 60.0, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, -1, 1, self.vehicle_name
                )
                await asyncio.to_thread(fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim goto_local_ned failed: {e}")
            return False

    async def stop_movement(self) -> bool:
        return await self.hover()

    async def get_home_position(self) -> Optional[Tuple[float, float, float]]:
        if not self._connected or self.client is None: return None
        if self._home_geopoint and not math.isnan(self._home_geopoint.latitude):
            return (self._home_geopoint.latitude, self._home_geopoint.longitude, self._home_geopoint.altitude)
        try:
            logger.warning("Home position not cached, falling back to current GPS.")
            gps = await asyncio.to_thread(self.client.getGpsData, "", self.vehicle_name)
            return (gps.gnss.geo_point.latitude, gps.gnss.geo_point.longitude, gps.gnss.geo_point.altitude)
        except Exception:
            return None

    async def get_telemetry(self) -> TelemetryData:
        return self._telemetry

    async def set_mode(self, mode: str) -> bool:
        if not self._connected or self.client is None: return False
        self._mode = mode
        return True

    async def get_all_params(self) -> dict:
        if self._connected and self.client is not None:
            logger.debug("get_all_params stub called")
        return {}

    async def get_param(self, name: str, param_type: str = "float"):
        if self._connected and self.client is not None:
            logger.debug(f"get_param stub called for {name}")
        return None

    async def set_param(self, name: str, value, param_type: str = "float") -> bool:
        if self._connected and self.client is not None:
            logger.debug(f"set_param stub called for {name}={value}")
        return False
        
    async def _telemetry_loop(self):
        while self._connected and self._telem_client is not None:
            try:
                state = await asyncio.to_thread(self._telem_client.getMultirotorState, self.vehicle_name)
                
                self._telemetry.timestamp = time.time()
                self._telemetry.latitude = state.gps_location.latitude
                self._telemetry.longitude = state.gps_location.longitude
                
                z_val = state.kinematics_estimated.position.z_val
                if math.isnan(z_val):
                    self._telemetry.altitude = None
                else:
                    self._telemetry.altitude = -z_val
                
                self._telemetry.velocity_x = state.kinematics_estimated.linear_velocity.x_val
                self._telemetry.velocity_y = state.kinematics_estimated.linear_velocity.y_val
                self._telemetry.velocity_z = state.kinematics_estimated.linear_velocity.z_val
                
                if not math.isnan(self._telemetry.velocity_x) and not math.isnan(self._telemetry.velocity_y):
                    self._telemetry.ground_speed = math.sqrt(self._telemetry.velocity_x**2 + self._telemetry.velocity_y**2)
                else:
                    self._telemetry.ground_speed = None
                
                pitch, roll, yaw = airsim.to_eularian_angles(state.kinematics_estimated.orientation)
                self._telemetry.pitch = math.degrees(pitch)
                self._telemetry.roll = math.degrees(roll)
                yaw_deg = math.degrees(yaw)
                self._telemetry.yaw = yaw_deg
                
                heading = yaw_deg
                if heading < 0:
                    heading += 360.0
                self._telemetry.heading = heading
                
                self._telemetry.battery_level = self.sim_battery_level
                self._telemetry.gps_valid = self.sim_gps_valid
                
                self._telemetry.local_pos_valid = True
                self._telemetry.global_pos_valid = True
                self._telemetry.home_valid = True
                
                self._telemetry.is_armable = getattr(state, 'can_arm', True)
                self._telemetry.health_all_ok = True
                
                if state.landed_state == 0 and self._mode == "LAND":
                    self._armed = False
                
                self._telemetry.armed_state = "ARMED" if self._armed else "DISARMED"
                self._telemetry.flight_mode = self._mode
                self._telemetry.heartbeat_age = 0.0
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_rate_limited(f"AirSim telemetry loop error: {e}")
                self._telemetry.flight_mode = "disconnected"
                self._telemetry.heartbeat_age = 999.0
            await asyncio.sleep(0.1)
