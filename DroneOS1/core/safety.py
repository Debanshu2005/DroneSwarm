from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.interfaces import IFlightController

logger = setup_logger("SafetyModule")

class SafetyModule:
    """
    Coordinates safety actions. Decouples safety triggers from flight logic.
    Submits high-priority intents to the FlightStateStore.
    """
    def __init__(self, flight_controller: IFlightController, state_store: FlightStateStore, config=None):
        self.fc = flight_controller
        self.state_store = state_store
        self.config = config
        self.is_failsafe_active: bool = False
        self.mission_manager = None

    def set_mission_manager(self, mm):
        self.mission_manager = mm

    async def trigger_emergency_stop(self) -> None:
        self.is_failsafe_active = True
        logger.critical("EMERGENCY STOP INITIATED!")
        # Emit an emergency kill intent that bypasses everything
        intent = FlightIntent(IntentSource.SAFETY, IntentAction.EMERGENCY_KILL, ttl_seconds=10.0)
        self.state_store.submit_intent(intent)
        
        if self.mission_manager:
            self.mission_manager.abort_mission()

    def reset_failsafe(self) -> None:
        self.is_failsafe_active = False
        self.state_store.clear_intent(IntentSource.SAFETY)
        logger.info("Failsafe/Emergency stop reset manually.")

    async def trigger_connection_lost_failsafe(self) -> None:
        self.is_failsafe_active = True
        telemetry = self.state_store.local_telemetry
        
        if telemetry.armed_state != "ARMED":
            logger.info("Connection lost, but drone is not armed. Waiting for reconnection.")
            return

        is_indoor = self.config and getattr(self.config, "profile", "") == "indoor"
        if is_indoor or not telemetry.home_valid:
            logger.warning("Connection lost failsafe! Landing.")
            intent = FlightIntent(IntentSource.SAFETY, IntentAction.LAND, ttl_seconds=600.0)
            self.state_store.submit_intent(intent)
        else:
            logger.warning("Connection lost failsafe! Returning to launch...")
            intent = FlightIntent(IntentSource.SAFETY, IntentAction.RTL, ttl_seconds=600.0)
            self.state_store.submit_intent(intent)

    async def trigger_low_battery_failsafe(self) -> None:
        self.is_failsafe_active = True
        is_indoor = self.config and getattr(self.config, "profile", "") == "indoor"
        if is_indoor:
            logger.warning("Low battery failsafe triggered! Landing.")
            intent = FlightIntent(IntentSource.SAFETY, IntentAction.LAND, ttl_seconds=600.0)
            self.state_store.submit_intent(intent)
        else:
            logger.warning("Low battery failsafe triggered! Returning to launch...")
            intent = FlightIntent(IntentSource.SAFETY, IntentAction.RTL, ttl_seconds=600.0)
            self.state_store.submit_intent(intent)

    async def trigger_critical_battery_failsafe(self) -> None:
        self.is_failsafe_active = True
        logger.critical("Critical battery failsafe triggered! Landing immediately...")
        intent = FlightIntent(IntentSource.SAFETY, IntentAction.LAND, ttl_seconds=600.0)
        self.state_store.submit_intent(intent)

    async def trigger_gps_degraded_failsafe(self) -> None:
        self.is_failsafe_active = True
        logger.warning("GPS degraded failsafe triggered! Holding position.")
        intent = FlightIntent(IntentSource.SAFETY, IntentAction.HOVER, ttl_seconds=600.0)
        self.state_store.submit_intent(intent)
