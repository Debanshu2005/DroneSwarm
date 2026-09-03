import time
import math
from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.formation_manager import global_offset_local_m

logger = setup_logger("SmartRtlEngine")

class SmartRtlEngine:
    def __init__(self, config):
        self.config = config
        self.internal_state = "IDLE"

    def compute_intent(self, state_store: FlightStateStore) -> FlightIntent:
        if not state_store.smart_rtl_active:
            self.internal_state = "IDLE"
            return None
            
        telemetry = state_store.local_telemetry
        if not telemetry or not telemetry.gps_valid or telemetry.latitude is None or telemetry.longitude is None:
            logger.warning("Smart RTL active but GPS invalid. Hovering.")
            return FlightIntent(IntentSource.MANUAL, IntentAction.HOVER, ttl_seconds=1.0)
            
        if not state_store.smart_rtl_target:
            logger.error("Smart RTL active but target not set. Cancelling.")
            state_store.smart_rtl_active = False
            self.internal_state = "IDLE"
            return None

        target_lat, target_lon, target_alt = state_store.smart_rtl_target
        
        # Check timeout
        timeout = 60.0
        if self.config and getattr(self.config, 'smart_rtl', None):
            timeout = float(self.config.smart_rtl.timeout_s)
            
        if time.time() - state_store.smart_rtl_start_time > timeout:
            logger.error("Smart RTL timeout exceeded! Cancelling.")
            state_store.smart_rtl_active = False
            self.internal_state = "IDLE"
            return None

        # Check arrival radius
        arrival_radius = 2.0
        if self.config and getattr(self.config, 'smart_rtl', None):
            arrival_radius = float(self.config.smart_rtl.arrival_radius_m)
            
        dx_north, dy_east = global_offset_local_m(
            telemetry.latitude, telemetry.longitude, target_lat, target_lon
        )
        distance = math.sqrt(dx_north**2 + dy_east**2)
        
        # If we arrived horizontally, we should land.
        if distance < arrival_radius or self.internal_state == "LANDING":
            self.internal_state = "LANDING"
            # If drone is already landed/disarmed, complete the sequence
            if telemetry.armed_state == "DISARMED" or telemetry.flight_mode == "disconnected":
                logger.info("Smart RTL sequence COMPLETE.")
                state_store.smart_rtl_active = False
                self.internal_state = "COMPLETE"
                return None
                
            return FlightIntent(IntentSource.MANUAL, IntentAction.LAND, ttl_seconds=2.0)
            
        else:
            self.internal_state = "NAVIGATING"
            return FlightIntent(
                IntentSource.MANUAL, 
                IntentAction.GOTO, 
                ttl_seconds=1.0, 
                params={"lat": target_lat, "lon": target_lon, "alt": target_alt, "yaw": 0.0}
            )

