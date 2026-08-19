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
        
    async def move(self, params: Dict[str, Any]) -> bool:
        telemetry = await self.fc.get_telemetry()
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot move: Drone is not ARMED.")
            return False
            
        vx = params.get('vx', 0.0)
        vy = params.get('vy', 0.0)
        vz = params.get('vz', 0.0)
        duration = params.get('duration', 1.0)
        yaw_rate = params.get('yaw_rate', 0.0)
        
        return await self.fc.move_velocity(vx, vy, vz, duration, yaw_rate)

    async def set_mode(self, params: Dict[str, Any]) -> bool:
        mode = params.get('mode')
        if not mode:
            logger.error("Cannot set mode: No mode specified.")
            return False
            
        success = await self.fc.set_mode(mode)
        if success:
            logger.info(f"Mode set to {mode} accepted.")
        return success
