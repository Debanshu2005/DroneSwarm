from DroneOS.core.interfaces import IFlightController
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.core.flight_state import FlightStateStore
from typing import Dict, Any
import time

logger = setup_logger("FlightManager")

class FlightManager:
    """
    High-level API for triggering intents or state changes.
    Does not run background loops anymore.
    """
    def __init__(self, flight_controller: IFlightController, state_store: FlightStateStore, min_srtl_altitude_m: float = 2.0):
        self.fc = flight_controller
        self.state_store = state_store
        self.swarm_manager = None
        self._active_navigation_frame = None
        self._min_srtl_altitude_m = min_srtl_altitude_m
        
        # We need a reference to the formation engine to feed it params when active
        self.formation_params = None

    def set_swarm_manager(self, swarm_manager):
        self.swarm_manager = swarm_manager

    async def arm(self, params: Dict[str, Any] = None) -> bool:
        # State change, not continuous movement
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
        telemetry = self.state_store.local_telemetry
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            logger.error("Cannot takeoff: Drone telemetry indicates it is not ARMED.")
            return False
            
        altitude = getattr(self.fc.config, "takeoff_altitude", 5.0) if hasattr(self.fc, "config") else 5.0
        if params and 'altitude_m' in params:
            try:
                altitude = float(params['altitude_m'])
            except (ValueError, TypeError):
                return False
                
        # Emit a takeoff intent
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.TAKEOFF, ttl_seconds=5.0, params={"altitude": altitude})
        self.state_store.submit_intent(intent)
        logger.info(f"Takeoff intent submitted for {altitude}m.")
        return True

    async def land(self, params: Dict[str, Any] = None) -> bool:
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.LAND, ttl_seconds=5.0)
        self.state_store.submit_intent(intent)
        logger.info("Land intent submitted.")
        return True

    async def rtl(self, params: Dict[str, Any] = None) -> bool:
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.RTL, ttl_seconds=5.0)
        self.state_store.submit_intent(intent)
        logger.info("RTL intent submitted.")
        return True

    async def smart_rtl(self, params: Dict[str, Any] = None) -> bool:
        telemetry = self.state_store.local_telemetry
        if telemetry.altitude is None or telemetry.altitude < self._min_srtl_altitude_m:
            logger.error(f"SRTL rejected: altitude too low")
            return False

        home = await self.fc.get_home_position()
        if home is None:
            logger.error("SRTL rejected: home position not available.")
            return False
            
        home_lat, home_lon, _ = home
        
        # Initiate the Smart RTL Engine state
        self.state_store.smart_rtl_active = True
        self.state_store.smart_rtl_target = (home_lat, home_lon, telemetry.altitude)
        self.state_store.smart_rtl_start_time = time.monotonic()
        
        logger.info("Smart RTL initiated.")
        return True

    async def hover(self, params: Dict[str, Any] = None) -> bool:
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.HOVER, ttl_seconds=2.0)
        self.state_store.submit_intent(intent)
        return True
        
    async def stop(self, params: Dict[str, Any] = None) -> bool:
        # Clear all manual intents to fall back to idle/hover
        self.state_store.clear_intent(IntentSource.MANUAL)
        self.state_store.clear_intent(IntentSource.FORMATION)
        self.state_store.clear_intent(IntentSource.MISSION)
        self.state_store.smart_rtl_active = False
        return True

    async def move(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "LOCAL_NED"
        telemetry = self.state_store.local_telemetry
        if getattr(telemetry, 'armed_state', None) != "ARMED":
            return False
            
        vx = float(params.get('vx', 0.0))
        vy = float(params.get('vy', 0.0))
        vz = float(params.get('vz', 0.0))
        yaw_rate = float(params.get('yaw_rate', 0.0))
        
        # Emit intent with short TTL (acts as deadman switch)
        intent = FlightIntent(
            IntentSource.MANUAL, 
            IntentAction.MOVE_VELOCITY, 
            ttl_seconds=0.5, 
            params={"vx": vx, "vy": vy, "vz": vz, "yaw_rate": yaw_rate}
        )
        self.state_store.submit_intent(intent)
        return True

    async def goto(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "GLOBAL_RELATIVE_ALT"
        lat = params.get('lat')
        lon = params.get('lon')
        alt = params.get('alt')
        if lat is None or lon is None or alt is None:
            return False
            
        intent = FlightIntent(
            IntentSource.MANUAL, 
            IntentAction.GOTO, 
            ttl_seconds=5.0, 
            params={"lat": lat, "lon": lon, "alt": alt, "yaw": 0.0}
        )
        self.state_store.submit_intent(intent)
        return True

    async def goto_local(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "LOCAL_NED"
        north = params.get('north')
        east = params.get('east')
        down = params.get('down')
        if north is None or east is None or down is None:
            return False
            
        intent = FlightIntent(
            IntentSource.MANUAL, 
            IntentAction.GOTO_NED, 
            ttl_seconds=5.0, 
            params={"north": north, "east": east, "down": down, "yaw": params.get('yaw', 0.0)}
        )
        self.state_store.submit_intent(intent)
        return True

    async def set_mode(self, params: Dict[str, Any]) -> bool:
        mode = params.get('mode')
        if not mode:
            return False
        return await self.fc.set_mode(mode)

    async def formation_update(self, params: Dict[str, Any]) -> bool:
        if not self.swarm_manager:
            return False
            
        self._active_navigation_frame = "GLOBAL_RELATIVE_ALT"
        self.formation_params = params # Store params for the decision engine/formation engine to pick up
        logger.info(f"Formation parameters updated: {params}")
        return True

    def is_gps_dependent_navigation_active(self, telemetry=None) -> bool:
        if self._active_navigation_frame == "GLOBAL_RELATIVE_ALT":
            return True
        if self._active_navigation_frame == "LOCAL_NED":
            return False
        mode = getattr(telemetry, "flight_mode", "") or ""
        return mode.upper() in {"AUTO", "MISSION", "GUIDED", "LOITER", "RTL", "HOLD", "POSCTL", "POSITION", "OFFBOARD"}

