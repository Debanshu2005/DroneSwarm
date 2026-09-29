from DroneOS3.core.interfaces import IFlightController
from DroneOS3.shared.utils.logger import setup_logger
from DroneOS3.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS3.core.flight_state import FlightStateStore
from typing import Dict, Any
import time

logger = setup_logger("FlightManager")

class FlightManager:
    def __init__(self, flight_controller: IFlightController, state_store: FlightStateStore, min_srtl_altitude_m: float = 2.0):
        self.fc = flight_controller
        self.state_store = state_store
        self.swarm_manager = None
        self._active_navigation_frame = None
        self._min_srtl_altitude_m = min_srtl_altitude_m
        self.formation_params = None
        self.mission_manager = None  # set by main after construction

    def set_swarm_manager(self, swarm_manager):
        self.swarm_manager = swarm_manager

    async def arm(self, params: Dict[str, Any] = None) -> bool:
        self.state_store.clear_landing_latch()
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
        self.state_store.clear_landing_latch()
        altitude = getattr(self.fc.config, "takeoff_altitude", 5.0) if hasattr(self.fc, "config") else 5.0
        if params and "altitude_m" in params:
            try:
                altitude = float(params["altitude_m"])
            except (ValueError, TypeError):
                return False
        intent_params = {"altitude": altitude}
        if params:
            intent_params.update({k: v for k, v in params.items() if k.startswith("_")})
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.TAKEOFF, ttl_seconds=30.0, params=intent_params)
        self.state_store.submit_intent(intent)
        logger.info("Takeoff intent submitted for %sm.", altitude)
        return True

    def _pre_land_rtl_cleanup(self):
        self.formation_params = None
        if self.mission_manager is not None:
            try:
                self.mission_manager.abort_mission()
            except Exception:
                pass
        self.state_store.clear_intent(IntentSource.FORMATION)
        self.state_store.clear_intent(IntentSource.MISSION)
        self.state_store.clear_intent(IntentSource.COLLISION)
        self.state_store.set_landing_latch()

    async def land(self, params: Dict[str, Any] = None) -> bool:
        self._pre_land_rtl_cleanup()
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.LAND, ttl_seconds=15.0, params=params or {})
        self.state_store.submit_intent(intent)
        logger.info("Land intent submitted.")
        return True

    async def rtl(self, params: Dict[str, Any] = None) -> bool:
        self._pre_land_rtl_cleanup()
        intent = FlightIntent(IntentSource.MANUAL, IntentAction.RTL, ttl_seconds=15.0, params=params or {})
        self.state_store.submit_intent(intent)
        logger.info("RTL intent submitted.")
        return True

    async def cancel_critical_command(self, command_id: str) -> bool:
        manual_intent = self.state_store.get_intents().get(IntentSource.MANUAL)
        if manual_intent and manual_intent.params.get("_command_id") == command_id:
            cleared = self.state_store.complete_intent(manual_intent)
            logger.warning("Cancelled undispatched critical command id=%s cleared=%s", command_id, cleared)
            return cleared
        return False

    async def smart_rtl(self, params: Dict[str, Any] = None) -> bool:
        telemetry = self.state_store.local_telemetry
        if telemetry.altitude is None or telemetry.altitude < self._min_srtl_altitude_m:
            logger.error("SRTL rejected: altitude too low")
            return False
        home = await self.fc.get_home_position()
        if home is None:
            logger.error("SRTL rejected: home position not available.")
            return False
        home_lat, home_lon, _ = home
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
        self.state_store.clear_intent(IntentSource.MANUAL)
        self.state_store.clear_intent(IntentSource.FORMATION)
        self.state_store.clear_intent(IntentSource.MISSION)
        self.state_store.smart_rtl_active = False
        return True

    async def move(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "LOCAL_NED"
        telemetry = self.state_store.local_telemetry
        if getattr(telemetry, "armed_state", None) != "ARMED":
            return False
        vx = float(params.get("vx", 0.0))
        vy = float(params.get("vy", 0.0))
        vz = float(params.get("vz", 0.0))
        yaw_rate = float(params.get("yaw_rate", 0.0))
        intent = FlightIntent(
            IntentSource.MANUAL, IntentAction.MOVE_VELOCITY, ttl_seconds=0.5,
            params={"vx": vx, "vy": vy, "vz": vz, "yaw_rate": yaw_rate}
        )
        self.state_store.submit_intent(intent)
        return True

    async def goto(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "GLOBAL_RELATIVE_ALT"
        lat = params.get("lat")
        lon = params.get("lon")
        alt = params.get("alt")
        if lat is None or lon is None or alt is None:
            return False
        intent = FlightIntent(
            IntentSource.MANUAL, IntentAction.GOTO, ttl_seconds=5.0,
            params={"lat": lat, "lon": lon, "alt": alt, "yaw": 0.0}
        )
        self.state_store.submit_intent(intent)
        return True

    async def goto_local(self, params: Dict[str, Any]) -> bool:
        self._active_navigation_frame = "LOCAL_NED"
        north = params.get("north")
        east = params.get("east")
        down = params.get("down")
        if north is None or east is None or down is None:
            return False
        intent = FlightIntent(
            IntentSource.MANUAL, IntentAction.GOTO_NED, ttl_seconds=5.0,
            params={"north": north, "east": east, "down": down, "yaw": params.get("yaw", 0.0)}
        )
        self.state_store.submit_intent(intent)
        return True

    async def set_mode(self, params: Dict[str, Any]) -> bool:
        mode = params.get("mode")
        if not mode:
            return False
        return await self.fc.set_mode(mode)

    async def formation_update(self, params: Dict[str, Any]) -> bool:
        if not self.swarm_manager:
            return False
            
        drone_id = getattr(self.swarm_manager, "identity", None)
        drone_id = getattr(drone_id, "drone_id", "unknown") if drone_id else "unknown"
        
        slot_assignments = params.get("slot_assignments")
        if not isinstance(slot_assignments, dict) or not slot_assignments or drone_id not in slot_assignments:
            logger.warning("formation rejected: no slot for %s", drone_id)
            return False
            
        try:
            for k in list(slot_assignments.keys()):
                slot_assignments[k] = int(slot_assignments[k])
                
            f_type_str = params.get("type", "V").upper()
            from DroneOS3.core.formation_manager import FormationType
            _ = FormationType(f_type_str)
        except ValueError as e:
            logger.warning("formation rejected: invalid parameters: %s", e)
            return False

        self._active_navigation_frame = "GLOBAL_RELATIVE_ALT"
        self.formation_params = params
        
        slot = slot_assignments[drone_id]
        logger.info(
            "FORMATION_COMMAND_RECEIVED drone=%s type=%s spacing=%s slot=%s members=%s",
            drone_id, params.get("type"), params.get("spacing"), slot,
            params.get("members")
        )
        
        logger.info(
            "FORMATION_COMMAND_RECEIVED slot_map=%s",
            slot_assignments
        )
        
        logger.info(
            "FORMATION_ACTIVE drone=%s slot=%s — params stored, FormationEngine will drive movement",
            drone_id, slot
        )
        return True

    def is_gps_dependent_navigation_active(self, telemetry=None) -> bool:
        if self._active_navigation_frame == "GLOBAL_RELATIVE_ALT":
            return True
        if self._active_navigation_frame == "LOCAL_NED":
            return False
        mode = getattr(telemetry, "flight_mode", "") or ""
        return mode.upper() in {"AUTO", "MISSION", "GUIDED", "LOITER", "RTL", "HOLD", "POSCTL", "POSITION", "OFFBOARD"}
