from DroneOS.core.interfaces import IFlightController
from DroneOS.shared.utils.logger import setup_logger
from typing import Dict, Any

logger = setup_logger("FlightManager")

class FlightManager:
    """
    High-level state machine and wrapper around the abstract IFlightController.
    Handles sequence of operations (e.g., must be armed before takeoff).
    """
    def __init__(self, flight_controller: IFlightController):
        self.fc = flight_controller
        self._last_move_time = 0.0
        self._deadman_task = None

    async def arm(self, params: Dict[str, Any] = None) -> bool:
        success = await self.fc.arm()
        if success:
            logger.info("Drone arm command accepted.")
        return success

    async def disarm(self, params: Dict[str, Any] = None) -> bool:
        success = await self.fc.disarm()
        if success:
            logger.info("Drone disarm command accepted.")
        return success

    async def takeoff(self, params: Dict[str, Any] = None) -> bool:
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot takeoff: Drone telemetry indicates it is not ARMED.")
            return False
            
        altitude = 5.0
        if params and 'altitude_m' in params:
            try:
                parsed = float(params['altitude_m'])
                if 1.0 <= parsed <= 10.0:
                    altitude = parsed
                else:
                    logger.error(f"Cannot takeoff: Altitude {parsed} is out of bounds (1.0 - 10.0m)")
                    return False
            except (ValueError, TypeError):
                logger.error("Cannot takeoff: Invalid altitude parameter format.")
                return False
                
        success = await self.fc.takeoff(altitude)
        if success:
            logger.info(f"Takeoff command accepted to {altitude}m.")
        return success

    async def land(self, params: Dict[str, Any] = None) -> bool:
        success = await self.fc.land()
        if success:
            logger.info("Land command accepted.")
        return success

    async def rtl(self, params: Dict[str, Any] = None) -> bool:
        success = await self.fc.rtl()
        if success:
            logger.info("RTL command accepted.")
        return success

    async def hover(self, params: Dict[str, Any] = None) -> bool:
        return await self.fc.hover()
        
    async def _move_deadman_monitor(self):
        import asyncio, time
        try:
            while True:
                await asyncio.sleep(0.1)
                if time.time() - self._last_move_time > 0.5:
                    logger.info("Deadman timeout reached. Auto-hovering.")
                    await self.fc.hover()
                    self._deadman_task = None
                    break
        except asyncio.CancelledError:
            pass

    async def move(self, params: Dict[str, Any]) -> bool:
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot move: Drone is not ARMED.")
            return False
            
        vx = float(params.get('vx', 0.0))
        vy = float(params.get('vy', 0.0))
        vz = float(params.get('vz', 0.0))
        yaw_rate = float(params.get('yaw_rate', 0.0))
        
        # Enforce configurable/safe speed limits
        vx = max(-5.0, min(5.0, vx))
        vy = max(-5.0, min(5.0, vy))
        vz = max(-3.0, min(3.0, vz))
        yaw_rate = max(-90.0, min(90.0, yaw_rate))

        import time, asyncio
        self._last_move_time = time.time()
        
        if self._deadman_task is None or self._deadman_task.done():
            self._deadman_task = asyncio.create_task(self._move_deadman_monitor())

        # duration is managed by the deadman now, pass 0.5 for fc internal loop if needed
        return await self.fc.move_velocity(vx, vy, vz, 0.5, yaw_rate)

    async def goto(self, params: Dict[str, Any]) -> bool:
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot goto: Drone is not ARMED.")
            return False
            
        lat = params.get('lat')
        lon = params.get('lon')
        alt = params.get('alt')
        if lat is None or lon is None or alt is None:
            logger.error("Goto requires lat, lon, and alt.")
            return False
            
        # Basic Validation
        if not telemetry.gps_valid:
            logger.error("Goto failed: GPS is invalid.")
            return False
        if telemetry.battery_level is not None and telemetry.battery_level < 10.0:
            logger.error("Goto failed: Battery too low for mission.")
            return False
            
        # Call FC adapter goto (which should compile a temporary mission)
        return await self.fc.goto_location(lat, lon, alt)

    async def set_mode(self, params: Dict[str, Any]) -> bool:
        mode = params.get('mode')
        if not mode:
            logger.error("Cannot set mode: No mode specified.")
            return False
            
        success = await self.fc.set_mode(mode)
        if success:
            logger.info(f"Mode set to {mode} accepted.")
        return success
