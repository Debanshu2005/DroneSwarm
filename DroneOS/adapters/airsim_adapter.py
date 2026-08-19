import asyncio
from typing import Optional
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.shared.config.models import FlightConfig
from DroneOS.core.interfaces import IFlightController

logger = setup_logger("AirSimAdapter")

try:
    import airsim
except ImportError:
    logger.warning("AirSim module not found. Adapter will fail if instantiated without a mock.")
    airsim = None

class AirSimFlightController(IFlightController):
    """
    Implements IFlightController using Microsoft AirSim.
    Wraps AirSim's blocking calls in asyncio.to_thread to maintain async performance.
    Uses an asyncio.Lock to strictly synchronize all RPC calls.
    Features a robust Connection Manager to handle intermittent StreamClosedErrors and RPC drops.
    """
    def __init__(self, vehicle_name: str, config: FlightConfig):
        self.vehicle_name = vehicle_name
        self.config = config
        self.client: Optional['airsim.MultirotorClient'] = None
        self._connected = False
        self._reconnecting = False
        self._reconnect_task = None
        self._client_lock = asyncio.Lock()

    def _handle_rpc_error(self, e: Exception):
        """Detects broken RPC sessions (e.g. tornado.iostream.StreamClosedError) and triggers reconnect."""
        if self._connected:
            logger.error(f"AirSim RPC Connection lost: {e.__class__.__name__} - {e}")
            self._connected = False
            self.client = None
            if not self._reconnecting:
                self._reconnecting = True
                try:
                    loop = asyncio.get_running_loop()
                    self._reconnect_task = loop.create_task(self._reconnect_loop())
                except RuntimeError:
                    logger.error("Failed to start reconnect loop: No running event loop.")

    async def _reconnect_loop(self):
        """Safely recreates the AirSim client without holding the lock during network IO."""
        logger.warning("AirSim Connection Manager: Starting reconnect loop...")
        try:
            while not self._connected:
                try:
                    new_client = await asyncio.to_thread(
                        airsim.MultirotorClient, 
                        ip=self.config.airsim_host, 
                        port=self.config.airsim_port,
                        timeout_value=int(self.config.airsim_timeout)
                    )
                    await asyncio.to_thread(new_client.confirmConnection)
                    await asyncio.to_thread(new_client.enableApiControl, True, self.vehicle_name)
                    
                    async with self._client_lock:
                        self.client = new_client
                        self._connected = True
                    logger.info("AirSim Connection Manager: Successfully restored RPC session!")
                    break
                except Exception as e:
                    logger.debug(f"AirSim reconnect attempt failed: {e}")
                    await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            logger.info("AirSim reconnect loop cancelled.")
        finally:
            self._reconnecting = False
            self._reconnect_task = None

    async def connect(self) -> bool:
        for attempt in range(self.config.airsim_retry_count):
            try:
                # Connect to AirSim simulator
                async with self._client_lock:
                    if self._connected: return True
                    self.client = await asyncio.to_thread(
                        airsim.MultirotorClient, 
                        ip=self.config.airsim_host, 
                        port=self.config.airsim_port,
                        timeout_value=int(self.config.airsim_timeout)
                    )
                    await asyncio.to_thread(self.client.confirmConnection)
                    await asyncio.to_thread(self.client.enableApiControl, True, self.vehicle_name)
                
                self._connected = True
                logger.info(f"Connected to AirSim vehicle: {self.vehicle_name} on {self.config.airsim_host}:{self.config.airsim_port}")
                return True
            except (OSError, RuntimeError, ValueError) as e:
                logger.exception(f"Failed to connect to AirSim (Attempt {attempt+1}/{self.config.airsim_retry_count}): {e}")
                await asyncio.sleep(1.0)
        return False

    async def disconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            
        if self.client and self._connected:
            try:
                async with self._client_lock:
                    await asyncio.to_thread(self.client.enableApiControl, False, self.vehicle_name)
                self.client = None
                self._connected = False
                logger.info("Disconnected from AirSim.")
            except Exception as e:
                logger.exception(f"Error disconnecting: {e}")

    async def arm(self) -> bool:
        if not self._connected: return False
        try:
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(self.client.armDisarm, True, self.vehicle_name)
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def disarm(self) -> bool:
        if not self._connected: return False
        try:
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(self.client.armDisarm, False, self.vehicle_name)
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def takeoff(self, altitude: float = 10.0) -> bool:
        if not self._connected: return False
        try:
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(self.client.takeoffAsync, vehicle_name=self.vehicle_name)
                await asyncio.to_thread(self.client.moveToZAsync, -altitude, 5.0, vehicle_name=self.vehicle_name)
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def land(self) -> bool:
        if not self._connected: return False
        try:
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(self.client.landAsync, vehicle_name=self.vehicle_name)
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def rtl(self) -> bool:
        if not self._connected: return False
        try:
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(self.client.goHomeAsync, vehicle_name=self.vehicle_name)
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def hover(self) -> bool:
        if not self._connected: return False
        try:
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(self.client.hoverAsync, vehicle_name=self.vehicle_name)
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def move_velocity(self, vx: float, vy: float, vz: float, duration: float, yaw_rate: float = 0.0) -> bool:
        if not self._connected: return False
        try:
            drivetrain = airsim.DrivetrainType.MaxDegreeOfFreedom
            yaw_mode = airsim.YawMode(is_rate=True, yaw_or_rate=yaw_rate)
            async with self._client_lock:
                if not self.client: return False
                await asyncio.to_thread(
                    self.client.moveByVelocityAsync, vx, vy, vz, duration, 
                    drivetrain=drivetrain, yaw_mode=yaw_mode, vehicle_name=self.vehicle_name
                )
            return True
        except Exception as e:
            self._handle_rpc_error(e)
            return False

    async def get_telemetry(self) -> TelemetryData:
        if not self._connected:
            return self._empty_telemetry()
            
        try:
            async with self._client_lock:
                if not self.client: return self._empty_telemetry()
                state = await asyncio.to_thread(self.client.getMultirotorState, vehicle_name=self.vehicle_name)
                try:
                    gps = await asyncio.to_thread(self.client.getGpsData, vehicle_name=self.vehicle_name)
                    lat = gps.gnss.geo_point.latitude
                    lon = gps.gnss.geo_point.longitude
                except Exception:
                    lat = None
                    lon = None
            
            # Extract kinematics correctly from state
            kinematics = state.kinematics_estimated
            pos = kinematics.position
            lin_vel = kinematics.linear_velocity

            # Extract orientation
            try:
                pitch_rad, roll_rad, yaw_rad = airsim.to_eularian_angles(kinematics.orientation)
                import math
                pitch = math.degrees(pitch_rad)
                roll = math.degrees(roll_rad)
                yaw = math.degrees(yaw_rad)
                heading = (yaw + 360) % 360
            except Exception:
                pitch, roll, yaw, heading = None, None, None, None
            
            # Simple mock battery for simulator
            battery = 98.0
            voltage = 14.8
            
            # Note: AirSim uses NED coordinates (z is down). Altitude is -z.
            mode_map = {0: "landed", 1: "flying"}
            mapped_mode = mode_map.get(state.landed_state, "unknown")
            
            import math
            vx = lin_vel.x_val
            vy = lin_vel.y_val
            gs = math.sqrt(vx*vx + vy*vy)
            armed = "ARMED" if mapped_mode != "landed" else "DISARMED"
            
            return TelemetryData(
                battery_level=battery,
                voltage=voltage,
                altitude=-pos.z_val,
                latitude=lat,
                longitude=lon,
                velocity_x=lin_vel.x_val,
                velocity_y=lin_vel.y_val,
                velocity_z=lin_vel.z_val,
                ground_speed=gs,
                pitch=pitch,
                roll=roll,
                yaw=yaw,
                heading=heading,
                flight_mode=mapped_mode,
                armed_state=armed
            )
        except Exception as e:
            self._handle_rpc_error(e)
            return self._empty_telemetry()

    def _empty_telemetry(self) -> TelemetryData:
        return TelemetryData(
            battery_level=None, voltage=None, altitude=None, latitude=None, longitude=None,
            velocity_x=None, velocity_y=None, velocity_z=None, 
            pitch=None, roll=None, yaw=None, heading=None, flight_mode="disconnected"
        )
