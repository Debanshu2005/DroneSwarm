import asyncio
import concurrent.futures
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
    Implements a dispatch-and-return non-blocking contract (like PX4): 
    motions return True when dispatched. Completion is observed via get_telemetry().
    Takeoff is the only exception, which blocks until altitude is reached.
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
        self._telem_lock = asyncio.Lock()
        self._cmd_seq = 0
        self._awaiting_disarm = False
        self._sim_battery_override = None
        self._sim_gps_override = None
        
        self._last_error = None
        self._last_error_time = 0.0
        self._last_ok = time.time()
        self._bg_tasks = set()
        self._rtl_task = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

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

    async def _watch_land(self, seq, timeout):
        start = time.time()
        while time.time() - start < timeout:
            if self._cmd_seq != seq:
                return
            if not self._awaiting_disarm:
                return
            await asyncio.sleep(0.5)
        if self._cmd_seq == seq:
            self._log_rate_limited("AirSim land watcher timed out")
            self._awaiting_disarm = False
            if self._mode == "LAND":
                self._mode = "HOLD"

    async def _watch_goto(self, seq, target_n, target_e, target_d, timeout):
        start = time.time()
        while time.time() - start < timeout:
            if self._cmd_seq != seq:
                return
            try:
                async with self._telem_lock:
                    state = await asyncio.to_thread(self._telem_client.getMultirotorState, self.vehicle_name)
                curr_n = state.kinematics_estimated.position.x_val
                curr_e = state.kinematics_estimated.position.y_val
                curr_d = state.kinematics_estimated.position.z_val
                vx = state.kinematics_estimated.linear_velocity.x_val
                vy = state.kinematics_estimated.linear_velocity.y_val
                vz = state.kinematics_estimated.linear_velocity.z_val
                dist = math.sqrt((curr_n - target_n)**2 + (curr_e - target_e)**2 + (curr_d - target_d)**2)
                vel = math.sqrt(vx**2 + vy**2 + vz**2)
                if dist < 1.0 and vel < 0.2:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        if self._cmd_seq == seq:
            self._log_rate_limited("AirSim goto watcher timed out")
            # Do not get stuck in goto mode if we time out
            pass
    async def _rtl_sequence(self, seq):
        start = time.time()
        arrived = False
        while time.time() - start < 75.0:
            if self._cmd_seq != seq:
                return
            try:
                async with self._telem_lock:
                    state = await asyncio.to_thread(self._telem_client.getMultirotorState, self.vehicle_name)
                curr_n = state.kinematics_estimated.position.x_val
                curr_e = state.kinematics_estimated.position.y_val
                dist_home = math.sqrt(curr_n**2 + curr_e**2)
                vx = state.kinematics_estimated.linear_velocity.x_val
                vy = state.kinematics_estimated.linear_velocity.y_val
                vz = state.kinematics_estimated.linear_velocity.z_val
                vel = math.sqrt(vx**2 + vy**2 + vz**2)
                if dist_home < 2.0 and vel < 0.2:
                    arrived = True
                    break
            except Exception as e:
                pass
            await asyncio.sleep(0.5)
        if not arrived:
            if self._cmd_seq == seq:
                self._log_rate_limited("AirSim RTL goHome timed out")
                self._mode = "HOLD"
            return
        try:
            async with self._cmd_lock:
                if self._cmd_seq != seq:
                    return
                self._awaiting_disarm = True
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self.client.landAsync, 30.0, self.vehicle_name)
        except Exception as e:
            if self._cmd_seq == seq:
                self._log_rate_limited(f"AirSim RTL land failed: {e}")
                self._awaiting_disarm = False
                self._mode = "HOLD"
            return
        start = time.time()
        while time.time() - start < 35.0:
            if self._cmd_seq != seq:
                return
            if not self._awaiting_disarm:
                return
            await asyncio.sleep(0.5)
        if self._cmd_seq == seq:
            self._log_rate_limited("AirSim RTL land watcher timed out")
            self._awaiting_disarm = False
            self._mode = "HOLD"
    async def connect(self) -> bool:
        if not airsim:
            logger.error("Cannot connect to AirSim: airsim module is not installed.")
            return False
            
        retry_count = getattr(self.config, 'airsim_retry_count', 3)
        for attempt in range(retry_count):
            temp_client = None
            temp_telem = None
            api_enabled = False
            try:
                logger.info(f"AirSim CONNECTING to {self.config.airsim_host}:{self.config.airsim_port} (Attempt {attempt+1}/{retry_count})")
                
                temp_client = airsim.MultirotorClient(ip=self.config.airsim_host, port=self.config.airsim_port)
                await asyncio.to_thread(temp_client.confirmConnection)
                await asyncio.to_thread(temp_client.enableApiControl, True, self.vehicle_name)
                api_enabled = True
                
                temp_telem = airsim.MultirotorClient(ip=self.config.airsim_host, port=self.config.airsim_port)
                await asyncio.to_thread(temp_telem.confirmConnection)
                
                home_geo = await asyncio.to_thread(temp_client.getHomeGeoPoint, self.vehicle_name)
                
                self.client = temp_client
                self._telem_client = temp_telem
                self._home_geopoint = home_geo
                self._connected = True
                self._mode = "HOLD"
                self._last_ok = time.time()
                
                logger.info(f"AirSim CONNECTED to {self.config.airsim_host}")
                self._telemetry_task = asyncio.create_task(self._telemetry_loop())
                return True
            except Exception as e:
                logger.error(f"AirSim connection attempt {attempt+1} failed: {e}")
                if api_enabled and temp_client:
                    try:
                        await asyncio.to_thread(temp_client.enableApiControl, False, self.vehicle_name)
                    except:
                        pass
                if attempt < retry_count - 1:
                    await asyncio.sleep(1.0)
                    
        self._connected = False
        self.client = None
        self._telem_client = None
        return False

    async def disconnect(self) -> None:
        self._connected = False
        self._armed = False
        self._mode = "disconnected"
        
        for t in list(self._bg_tasks):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._bg_tasks.clear()
        self._rtl_task = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        
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
                self._cmd_seq += 1
                seq = self._cmd_seq
                self._awaiting_disarm = False
                fut = await asyncio.get_running_loop().run_in_executor(self._executor, self.client.armDisarm, True, self.vehicle_name)
            
            if seq != self._cmd_seq:
                logger.debug("arm superseded")
                return False
                
            if fut:
                self._armed = True
                self._home_geopoint = await asyncio.get_running_loop().run_in_executor(self._executor, self.client.getHomeGeoPoint, self.vehicle_name)
            return bool(fut)
        except Exception as e:
            logger.error(f"AirSim arm failed: {e}")
            return False

    async def disarm(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            async with self._cmd_lock:
                self._cmd_seq += 1
                seq = self._cmd_seq
                fut = await asyncio.get_running_loop().run_in_executor(self._executor, self.client.armDisarm, False, self.vehicle_name)
                
            if seq != self._cmd_seq:
                logger.debug("disarm superseded")
                return False
                
            if fut:
                self._armed = False
            return bool(fut)
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
            
            async with self._cmd_lock:
                self._cmd_seq += 1
                seq = self._cmd_seq
                self._awaiting_disarm = False
                self._mode = "TAKEOFF"
                loop = asyncio.get_running_loop()
                def do_takeoff():
                    return self.client.takeoffAsync(15.0, self.vehicle_name).join()
                takeoff_task = loop.run_in_executor(self._executor, do_takeoff)
                
            await asyncio.wait_for(takeoff_task, timeout=20.0)
            if seq != self._cmd_seq:
                logger.debug("takeoff superseded before Z move")
                return False
                
            max_vel = getattr(self.config, 'max_velocity', 5.0)
            async with self._cmd_lock:
                self._cmd_seq += 1
                seq = self._cmd_seq
                def do_z():
                    return self.client.moveToZAsync(-altitude, max_vel, 15.0, airsim.YawMode(False, 0), -1, 1, self.vehicle_name).join()
                z_task = loop.run_in_executor(self._executor, do_z)
                
            await asyncio.wait_for(z_task, timeout=20.0)
                
            
            
            if seq != self._cmd_seq:
                logger.debug("takeoff superseded after Z move")
                return False
                
            self._mode = "HOLD"
            return True
        except asyncio.TimeoutError:
            logger.error("AirSim takeoff timed out")
            if getattr(self, '_cmd_seq', None) == locals().get('seq', -1): self._mode = "HOLD"
            return False
        except Exception as e:
            logger.error(f"AirSim takeoff failed: {e}")
            if getattr(self, '_cmd_seq', None) == locals().get('seq', -1): self._mode = "HOLD"
            return False

    async def land(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            async with self._cmd_lock:
                self._cmd_seq += 1
                seq = self._cmd_seq
                self._mode = "LAND"
                self._awaiting_disarm = True
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self.client.landAsync, 30.0, self.vehicle_name)
                
            task = asyncio.create_task(self._watch_land(seq, 35.0))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return True
        except Exception as e:
            self._awaiting_disarm = False
            if self._mode == "LAND":
                self._mode = "HOLD"
            logger.error(f"AirSim land failed: {e}")
            return False

    async def rtl(self) -> bool:
        if not self._connected or self.client is None: return False
        try:
            async with self._cmd_lock:
                self._cmd_seq += 1
                seq = self._cmd_seq
                self._mode = "RTL"
                self._awaiting_disarm = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self.client.goHomeAsync, 30.0, self.vehicle_name)
                
            task = asyncio.create_task(self._rtl_sequence(seq))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            self._rtl_task = task
            return True
        except Exception as e:
            self._awaiting_disarm = False
            if self._mode == "RTL":
                self._mode = "HOLD"
            logger.error(f"AirSim RTL failed: {e}")
            return False

    async def hover(self, force: bool = False) -> bool:
        if not self._connected or self.client is None: return False
        # Do not interrupt an active LAND or RTL unless the caller explicitly forces it
        if self._mode in ("LAND", "RTL") and not force:
            return True
        try:
            async with self._cmd_lock:
                self._cmd_seq += 1
                self._mode = "HOLD"
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self.client.hoverAsync, self.vehicle_name)
            return True
        except Exception as e:
            logger.error(f"AirSim hover failed: {e}")
            return False

    async def move_velocity(self, vx: float, vy: float, vz: float, duration: float, yaw_rate: float = 0.0) -> bool:
        """Move using world-frame NED velocity (vx=North, vy=East, vz=Down).
        FormationEngine produces global NED error vectors so this must use
        moveByVelocityAsync (world frame), NOT moveByVelocityBodyFrameAsync.
        """
        if not self._connected or self.client is None: return False
        try:
            max_vel = getattr(self.config, 'max_velocity', 5.0)
            vx = max(-max_vel, min(max_vel, vx))
            vy = max(-max_vel, min(max_vel, vy))
            vz = max(-max_vel, min(max_vel, vz))
            yaw_mode = airsim.YawMode(is_rate=True, yaw_or_rate=yaw_rate)

            hz = getattr(self.config, 'pipeline_hz', 10.0)
            eff_dur = max(duration, 2.0 / hz)

            logger.debug(
                "FC move_velocity NED vx=%.3f vy=%.3f vz=%.3f dur=%.3f vehicle=%s",
                vx, vy, vz, eff_dur, self.vehicle_name
            )
            async with self._cmd_lock:
                self._cmd_seq += 1
                self._awaiting_disarm = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._executor,
                    self.client.moveByVelocityAsync,
                    vx, vy, vz, eff_dur, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, self.vehicle_name
                )
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
            
            hz = getattr(self.config, 'pipeline_hz', 10.0)
            eff_dur = max(duration, 2.0 / hz)
            
            async with self._cmd_lock:
                self._cmd_seq += 1
                self._awaiting_disarm = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._executor,
                    self.client.moveByVelocityAsync,
                    north, east, down, eff_dur, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, self.vehicle_name
                )
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
                self._cmd_seq += 1
                seq = self._cmd_seq
                self._awaiting_disarm = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._executor,
                    self.client.moveToPositionAsync,
                    north, east, down, max_vel, 60.0, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, -1, 1, self.vehicle_name
                )
                
            task = asyncio.create_task(self._watch_goto(seq, north, east, down, 65.0))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
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
                self._cmd_seq += 1
                seq = self._cmd_seq
                self._awaiting_disarm = False
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._executor,
                    self.client.moveToPositionAsync,
                    north, east, down, max_vel, 60.0, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, -1, 1, self.vehicle_name
                )
                
            task = asyncio.create_task(self._watch_goto(seq, north, east, down, 65.0))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
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
        if mode == "HOLD":
            await self.hover()
        else:
            logger.debug(f"Mode {mode} is label only in AirSim backend")
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
        
    async def set_sim_battery(self, level: float):
        self._sim_battery_override = max(0.0, min(100.0, level))
        
    async def set_sim_gps_valid(self, valid: bool):
        self._sim_gps_override = valid
        
    def clear_sim_overrides(self):
        self._sim_battery_override = None
        self._sim_gps_override = None

    async def _telemetry_loop(self):
        while self._connected and self._telem_client is not None:
            try:
                async with self._telem_lock:
                    state = await asyncio.to_thread(self._telem_client.getMultirotorState, self.vehicle_name)
                
                self._last_ok = time.time()
                
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
                
                if self.config.sim:
                    if self._sim_gps_override is None:
                        self.sim_gps_valid = not self.config.sim.drop_gps
                    if self._sim_battery_override is None and self.config.sim.battery_drain_multiplier > 0:
                        self.sim_battery_level -= self.config.sim.battery_drain_multiplier * 0.1
                        self.sim_battery_level = max(0.0, self.sim_battery_level)
                
                if self._sim_gps_override is not None:
                    self.sim_gps_valid = self._sim_gps_override
                if self._sim_battery_override is not None:
                    self.sim_battery_level = self._sim_battery_override
                
                self._telemetry.battery_level = self.sim_battery_level
                self._telemetry.gps_valid = self.sim_gps_valid
                
                self._telemetry.local_pos_valid = True
                self._telemetry.global_pos_valid = True
                self._telemetry.home_valid = True
                
                self._telemetry.is_armable = getattr(state, 'can_arm', True)
                self._telemetry.health_all_ok = True
                
                if self._awaiting_disarm and state.landed_state == 0:
                    self._armed = False
                    self._awaiting_disarm = False
                    if self._mode in ("LAND", "RTL"):
                        self._mode = "HOLD"
                
                self._telemetry.armed_state = "ARMED" if self._armed else "DISARMED"
                self._telemetry.flight_mode = self._mode
                self._telemetry.heartbeat_age = time.time() - self._last_ok
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_rate_limited(f"AirSim telemetry loop error: {e}")
                self._telemetry.flight_mode = "disconnected"
                self._telemetry.heartbeat_age = time.time() - self._last_ok
            await asyncio.sleep(0.1)
