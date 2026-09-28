import os

adapter_code_template = """import asyncio
from typing import Optional, Tuple
import math
import time

from __MOD__.core.interfaces import IFlightController
from __MOD__.shared.config.models import FlightConfig
from __MOD__.shared.utils.logger import setup_logger
from __MOD__.shared.protocol.messages import TelemetryData

logger = setup_logger("AirSimAdapter")

try:
    import airsim
except ImportError:
    logger.warning("AirSim module not found. Adapter will fail if instantiated without a mock.")
    airsim = None

class AirSimFlightController(IFlightController):
    def __init__(self, vehicle_name: str, config: FlightConfig):
        self.vehicle_name = vehicle_name
        self.config = config
        self.client: Optional['airsim.MultirotorClient'] = None
        self._connected = False
        self._telemetry = self._empty_telemetry()
        self._telemetry_task = None

    def _empty_telemetry(self) -> TelemetryData:
        return TelemetryData(
            battery_level=100.0, altitude=0.0, latitude=0.0, longitude=0.0,
            velocity_x=0.0, velocity_y=0.0, velocity_z=0.0, flight_mode="disconnected"
        )

    async def connect(self) -> bool:
        if not airsim:
            logger.error("Cannot connect to AirSim: airsim module is not installed.")
            return False
        try:
            logger.info(f"AirSim CONNECTING to {self.config.airsim_host}:{self.config.airsim_port}")
            self.client = airsim.MultirotorClient(ip=self.config.airsim_host, port=self.config.airsim_port)
            await asyncio.to_thread(self.client.confirmConnection)
            await asyncio.to_thread(self.client.enableApiControl, True, self.vehicle_name)
            self._connected = True
            logger.info(f"AirSim CONNECTED to {self.config.airsim_host}")
            self._telemetry_task = asyncio.create_task(self._telemetry_loop())
            return True
        except Exception as e:
            logger.error(f"AirSim connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        self._connected = False
        if self._telemetry_task:
            self._telemetry_task.cancel()
        if self.client:
            try:
                await asyncio.to_thread(self.client.enableApiControl, False, self.vehicle_name)
            except Exception as e:
                logger.error(f"Error disabling API control: {e}")
            self.client = None
        logger.info("AirSim DISCONNECTED")

    async def arm(self) -> bool:
        if not self._connected: return False
        try:
            return await asyncio.to_thread(self.client.armDisarm, True, self.vehicle_name)
        except Exception as e:
            logger.error(f"AirSim arm failed: {e}")
            return False

    async def disarm(self) -> bool:
        if not self._connected: return False
        try:
            return await asyncio.to_thread(self.client.armDisarm, False, self.vehicle_name)
        except Exception as e:
            logger.error(f"AirSim disarm failed: {e}")
            return False

    async def takeoff(self, altitude: float = 10.0) -> bool:
        if not self._connected: return False
        try:
            takeoff_fut = await asyncio.to_thread(self.client.takeoffAsync, vehicle_name=self.vehicle_name)
            await asyncio.to_thread(takeoff_fut.join)
            z_fut = await asyncio.to_thread(self.client.moveToZAsync, -altitude, 2.0, vehicle_name=self.vehicle_name)
            await asyncio.to_thread(z_fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim takeoff failed: {e}")
            return False

    async def land(self) -> bool:
        if not self._connected: return False
        try:
            land_fut = await asyncio.to_thread(self.client.landAsync, vehicle_name=self.vehicle_name)
            await asyncio.to_thread(land_fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim land failed: {e}")
            return False

    async def rtl(self) -> bool:
        if not self._connected: return False
        try:
            rtl_fut = await asyncio.to_thread(self.client.goHomeAsync, vehicle_name=self.vehicle_name)
            await asyncio.to_thread(rtl_fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim RTL failed: {e}")
            return False

    async def hover(self) -> bool:
        if not self._connected: return False
        try:
            hover_fut = await asyncio.to_thread(self.client.hoverAsync, vehicle_name=self.vehicle_name)
            await asyncio.to_thread(hover_fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim hover failed: {e}")
            return False

    async def move_velocity(self, vx: float, vy: float, vz: float, duration: float, yaw_rate: float = 0.0) -> bool:
        if not self._connected: return False
        try:
            yaw_mode = airsim.YawMode(is_rate=True, yaw_or_rate=yaw_rate)
            fut = await asyncio.to_thread(
                self.client.moveByVelocityAsync,
                vx, vy, vz, duration, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, vehicle_name=self.vehicle_name
            )
            await asyncio.to_thread(fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim move_velocity failed: {e}")
            return False

    async def move_velocity_ned(self, north: float, east: float, down: float, duration: float, yaw_rate: float = 0.0) -> bool:
        return await self.move_velocity(north, east, down, duration, yaw_rate)

    async def goto_location(self, lat: float, lon: float, alt: float, yaw: float = 0.0) -> bool:
        if not self._connected: return False
        logger.warning("goto_location (global) not fully supported, falling back to local if possible.")
        return False

    async def goto_local_ned(self, north: float, east: float, down: float, yaw: float = 0.0) -> bool:
        if not self._connected: return False
        try:
            yaw_mode = airsim.YawMode(is_rate=False, yaw_or_rate=yaw)
            fut = await asyncio.to_thread(
                self.client.moveToPositionAsync,
                north, east, down, 5.0, yaw_mode=yaw_mode, vehicle_name=self.vehicle_name
            )
            await asyncio.to_thread(fut.join)
            return True
        except Exception as e:
            logger.error(f"AirSim goto_local_ned failed: {e}")
            return False

    async def stop_movement(self) -> bool:
        return await self.hover()

    async def get_home_position(self) -> Optional[Tuple[float, float, float]]:
        if not self._connected: return None
        try:
            gps = await asyncio.to_thread(self.client.getGpsData, vehicle_name=self.vehicle_name)
            return (gps.gnss.geo_point.latitude, gps.gnss.geo_point.longitude, gps.gnss.geo_point.altitude)
        except Exception:
            return None

    async def get_telemetry(self) -> TelemetryData:
        return self._telemetry

    async def set_mode(self, mode: str) -> bool:
        if not self._connected: return False
        self._telemetry.flight_mode = mode
        return True

    async def get_all_params(self) -> dict:
        return {}

    async def get_param(self, name: str, param_type: str = "float"):
        return None

    async def set_param(self, name: str, value, param_type: str = "float") -> bool:
        return False
        
    async def _telemetry_loop(self):
        while self._connected:
            try:
                state = await asyncio.to_thread(self.client.getMultirotorState, vehicle_name=self.vehicle_name)
                gps = await asyncio.to_thread(self.client.getGpsData, vehicle_name=self.vehicle_name)
                
                self._telemetry.timestamp = time.time()
                self._telemetry.latitude = gps.gnss.geo_point.latitude
                self._telemetry.longitude = gps.gnss.geo_point.longitude
                self._telemetry.altitude = state.kinematics_estimated.position.z_val * -1  # AirSim NED Z is down
                
                self._telemetry.velocity_x = state.kinematics_estimated.linear_velocity.x_val
                self._telemetry.velocity_y = state.kinematics_estimated.linear_velocity.y_val
                self._telemetry.velocity_z = state.kinematics_estimated.linear_velocity.z_val
                
                self._telemetry.ground_speed = math.sqrt(self._telemetry.velocity_x**2 + self._telemetry.velocity_y**2)
                
                self._telemetry.battery_level = 100.0
                self._telemetry.gps_valid = True
                self._telemetry.local_pos_valid = True
                self._telemetry.global_pos_valid = True
                self._telemetry.home_valid = True
                self._telemetry.is_armable = True
                self._telemetry.health_all_ok = True
                self._telemetry.armed_state = "ARMED" if state.ready_task_name != "" else "DISARMED"
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AirSim telemetry loop error: {e}")
            await asyncio.sleep(0.1)
"""

for mod in ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]:
    os.makedirs(f"{mod}/adapters", exist_ok=True)
    with open(f"{mod}/adapters/airsim_adapter.py", "w") as f:
        f.write(adapter_code_template.replace("__MOD__", mod))
        
    factory_path = f"{mod}/adapters/factory.py"
    if os.path.exists(factory_path):
        with open(factory_path, "r") as f:
            content = f.read()
            
        new_content = content.replace(
            'if backend_type in ["mavsdk", "px4"]:',
            f'''if backend_type == "airsim":
            from {mod}.adapters.airsim_adapter import AirSimFlightController
            logger.info("Initializing AirSim Flight Controller Adapter.")
            return AirSimFlightController(vehicle_name=drone_cfg.vehicle_name, config=flight_cfg)
            
        elif backend_type in ["mavsdk", "px4"]:'''
        )
        with open(factory_path, "w") as f:
            f.write(new_content)
print("Done")
