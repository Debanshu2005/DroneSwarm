import asyncio
from DroneOS1.shared.utils.logger import setup_logger
from DroneOS1.core.collision_avoidance import ICollisionAvoidance
from DroneOS1.core.mission_manager import MissionManager
from DroneOS1.core.swarm_manager import SwarmMembership
from DroneOS1.core.navigation_manager import NavigationManager
from DroneOS1.core.flight_state import FlightStateStore
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS1.shared.protocol.messages import TelemetryData

logger = setup_logger("DecisionEngine")

class LocalDecisionEngine:
    """
    Acts as the decentralized 'brain' of the drone.
    Evaluates input from the SwarmManager, SafetyModule, and MissionManager 
    to dynamically alter flight parameters without GroundStation.
    """
    def __init__(
        self, 
        mission_manager: MissionManager, 
        swarm_manager: SwarmMembership,
        collision_avoidance: ICollisionAvoidance,
        navigation_manager: NavigationManager,
        safety_module,
        state_store: FlightStateStore,
        config=None
    ):
        self.mission = mission_manager
        self.swarm = swarm_manager
        self.ca = collision_avoidance
        self.nav = navigation_manager
        self.safety = safety_module
        self.state_store = state_store
        self.config = config
        self.is_active = True
        self.active_bids = {}
        
        # Pull formation engine into tick evaluation
        from DroneOS1.core.formation_engine import FormationEngine
        self.formation_engine = FormationEngine(swarm_manager, state_store, config=config)

    def calculate_bid(self, my_telemetry: TelemetryData, target_lat: float, target_lon: float) -> float:
        if not my_telemetry.latitude or not my_telemetry.longitude:
            return float('inf')
            
        import math
        dist = math.hypot(my_telemetry.latitude - target_lat, my_telemetry.longitude - target_lon)
        batt = my_telemetry.battery_level or 100.0
        penalty = (100.0 - batt) * 0.1
        return dist + penalty

    async def evaluate_tick(self, current_telemetry: TelemetryData) -> None:
        if not self.is_active:
            return
            
        if self.safety.is_failsafe_active:
            logger.debug("DecisionEngine suspended: Safety failsafe is active.")
            return
            
        # 1. Collect peer telemetry
        peer_telemetry = {}
        for peer_id in self.swarm.registry.get_all_peers():
            state = self.swarm.registry.get_peer(peer_id)
            if state and state.is_active and state.telemetry:
                peer_telemetry[peer_id] = state.telemetry
                
        # 2. Evaluate Collision Threats (Highest Priority)
        state, correction, threat_peer, dist = self.ca.evaluate_threats(
            current_telemetry, 
            peer_telemetry
        )
        
        if state != "NORMAL":
            import time
            mode = current_telemetry.flight_mode or "UNKNOWN"
            log_str = (
                f"SAFETY INTERVENTION | state: {state} | "
                f"own_id: {self.swarm.identity.drone_id} | neighbor_id: {threat_peer} | "
                f"dist: {dist:.2f}m | mode: {mode} | ts: {time.time()} | reason: Minimum separation breached"
            )
            if state == "WARNING":
                logger.warning(log_str + " | action: NONE (Logging)")
            elif state == "AVOIDANCE":
                logger.warning(log_str + " | action: EVASIVE_MOVE")
                if correction:
                    intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY, ttl_seconds=1.0, params=correction)
                    self.state_store.submit_intent(intent)
                return
            elif state == "EMERGENCY":
                logger.critical(log_str + " | action: EMERGENCY_HOVER")
                intent = FlightIntent(IntentSource.COLLISION, IntentAction.HOVER, ttl_seconds=1.0)
                self.state_store.submit_intent(intent)
                return

        # 3. Proceed with Formation Execution
        # If formation is active (signaled by params existing)
        if self.nav.flight_manager.formation_params:
            intent = self.formation_engine.compute_intent(
                current_telemetry, 
                peer_telemetry, 
                self.nav.flight_manager.formation_params
            )
            if intent:
                self.state_store.submit_intent(intent)
            return

        # 4. Proceed with Mission Execution
        mission_state = self.mission.get_current_state()
        if mission_state == "RUNNING":
            wp = self.mission.get_current_waypoint()
            if wp:
                if getattr(self, '_waypoint_delay_start', None) is not None:
                    import time
                    elapsed = time.monotonic() - self._waypoint_delay_start
                    if elapsed >= wp.delay:
                        logger.info(f"Waypoint delay of {wp.delay}s completed. Advancing mission.")
                        self._waypoint_delay_start = None
                        self.mission.advance_waypoint()
                    else:
                        # Emitting HOVER intent during the dwell allows manual/collision to override
                        intent = FlightIntent(IntentSource.MISSION, IntentAction.HOVER, ttl_seconds=1.0)
                        self.state_store.submit_intent(intent)
                    return

                reached = await self.mission.executor.execute_waypoint(
                    current_telemetry, 
                    self.mission.tracker.current_index
                )
                if reached:
                    logger.info("Waypoint reached.")
                    if wp.delay > 0:
                        logger.info(f"Starting waypoint delay of {wp.delay}s.")
                        import time
                        self._waypoint_delay_start = time.monotonic()
                        intent = FlightIntent(IntentSource.MISSION, IntentAction.HOVER, ttl_seconds=1.0)
                        self.state_store.submit_intent(intent)
                    else:
                        logger.info("Advancing mission.")
                        self.mission.advance_waypoint()

