from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.interfaces import IFlightController

logger = setup_logger("SafetyModule")

class SafetyModule:
    """
    Coordinates safety actions. Decouples safety triggers from flight logic.
    """
    def __init__(self, flight_controller: IFlightController):
        self.fc = flight_controller
        self.is_failsafe_active: bool = False
        self.mission_manager = None

    def set_mission_manager(self, mm):
        self.mission_manager = mm

    async def trigger_emergency_stop(self) -> None:
        self.is_failsafe_active = True
        logger.critical("EMERGENCY STOP INITIATED!")
        # Cut power, stop all movement immediately
        try:
            await self.fc.move_velocity(0.0, 0.0, 0.0, 1.0, 0.0)
        except Exception as e:
            logger.error(f"Failed to send zero velocity during emergency stop: {e}")
            
        await self.fc.disarm()
        
        if self.mission_manager:
            self.mission_manager.abort_mission()

    async def trigger_connection_lost_failsafe(self) -> None:
        self.is_failsafe_active = True
        logger.warning("Connection lost failsafe triggered! Returning to launch...")
        await self.fc.rtl()

    async def trigger_low_battery_failsafe(self) -> None:
        self.is_failsafe_active = True
        logger.warning("Low battery failsafe triggered! Returning to launch...")
        await self.fc.rtl()

    async def trigger_critical_battery_failsafe(self) -> None:
        self.is_failsafe_active = True
        logger.critical("Critical battery failsafe triggered! Landing immediately...")
        await self.fc.land()
