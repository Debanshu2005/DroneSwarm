import asyncio
import math
import time
from DroneOS2.shared.utils.logger import setup_logger
from DroneOS2.core.collision_avoidance import ICollisionAvoidance
from DroneOS2.core.mission_manager import MissionManager
from DroneOS2.core.swarm_manager import SwarmMembership
from DroneOS2.core.navigation_manager import NavigationManager
from DroneOS2.core.flight_state import FlightStateStore
from DroneOS2.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS2.shared.protocol.messages import TelemetryData

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

        from DroneOS2.core.formation_engine import FormationEngine
        self.formation_engine = FormationEngine(swarm_manager, state_store, config=config)

    def calculate_bid(self, my_telemetry: TelemetryData, target_lat: float, target_lon: float) -> float:
        if not my_telemetry.latitude or not my_telemetry.longitude:
            return float('inf')
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

        # 1. Collect ALL fresh peer telemetry — formation membership does NOT exempt a peer.
        # CA is a hard safety layer and must evaluate every airborne peer.
        peer_telemetry = {}
        my_id = self.swarm.identity.drone_id
        for peer_id in self.swarm.registry.get_all_peers():
            if peer_id == my_id:
                continue
            state = self.swarm.registry.get_peer(peer_id)
            if state and state.telemetry is not None:
                peer_telemetry[peer_id] = state.telemetry

        # 2. CA diagnostics — log what CA is about to receive
        logger.info("CA_INPUT self=%s peers=%d", my_id, len(peer_telemetry))
        if peer_telemetry and current_telemetry.latitude is not None:
            now = time.time()
            for peer_id, peer_t in peer_telemetry.items():
                age = (now - peer_t.timestamp) if peer_t.timestamp is not None else float('inf')
                if current_telemetry.latitude is not None and peer_t.latitude is not None:
                    from DroneOS2.core.collision_avoidance import _haversine_ne
                    rn, re = _haversine_ne(
                        current_telemetry.latitude, current_telemetry.longitude,
                        peer_t.latitude, peer_t.longitude
                    )
                    dist_m = math.sqrt(rn * rn + re * re)
                    alt_diff = abs((current_telemetry.altitude or 0.0) - (peer_t.altitude or 0.0))
                else:
                    dist_m = float('inf')
                    alt_diff = float('inf')
                logger.debug(
                    "CA_PEER peer=%s age=%.3fs dist=%.2fm alt_diff=%.2fm",
                    peer_id, age, dist_m, alt_diff
                )

        # 3. Evaluate Collision Threats (Highest Priority)
        state, correction, threat_peer, dist = self.ca.evaluate_threats(
            current_telemetry,
            peer_telemetry
        )

        logger.info(
            "CA_DECISION state=%s peer=%s dist=%.2f",
            state, threat_peer, dist if dist != float('inf') else -1.0
        )

        if state != "NORMAL":
            is_formation_peer = False
            if self.nav.flight_manager.formation_params and threat_peer:
                slot_assignments = self.nav.flight_manager.formation_params.get("slot_assignments", {})
                if threat_peer in slot_assignments:
                    is_formation_peer = True

            if is_formation_peer and state == "WARNING":
                logger.info("CA_IGNORED WARNING for formation peer %s (letting formation engine handle it)", threat_peer)
                state = "NORMAL"

        if state != "NORMAL":
            if correction and getattr(self.config, "max_velocity", None):
                max_v = float(self.config.max_velocity)
                speed = math.hypot(correction.get("north", 0.0), correction.get("east", 0.0))
                if speed > max_v:
                    scale = max_v / speed
                    correction["north"] *= scale
                    correction["east"] *= scale

            mode = current_telemetry.flight_mode or "UNKNOWN"
            log_str = (
                f"SAFETY INTERVENTION | state: {state} | "
                f"own_id: {my_id} | neighbor_id: {threat_peer} | "
                f"dist: {dist:.2f}m | mode: {mode} | ts: {time.time()} | reason: Minimum separation breached"
            )
            if state == "WARNING":
                if correction:
                    logger.warning(log_str + " | action: WARNING_MOVE")
                    logger.info(
                        "CA_AVOIDANCE peer=%s north=%.3f east=%.3f down=%.3f",
                        threat_peer,
                        correction.get("north", 0.0),
                        correction.get("east", 0.0),
                        correction.get("down", 0.0),
                    )
                    intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0, params=correction)
                    self.state_store.submit_intent(intent)
                    return
                else:
                    logger.warning(log_str + " | action: NONE (Logging)")
            elif state == "AVOIDANCE":
                logger.warning(log_str + " | action: EVASIVE_MOVE")
                if correction:
                    logger.info(
                        "CA_AVOIDANCE peer=%s north=%.3f east=%.3f down=%.3f",
                        threat_peer,
                        correction.get("north", 0.0),
                        correction.get("east", 0.0),
                        correction.get("down", 0.0),
                    )
                    intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0, params=correction)
                    self.state_store.submit_intent(intent)
                return
            elif state == "EMERGENCY":
                logger.critical(log_str + " | action: EMERGENCY_EVASIVE_MOVE")
                if correction:
                    logger.info(
                        "CA_AVOIDANCE peer=%s north=%.3f east=%.3f down=%.3f",
                        threat_peer,
                        correction.get("north", 0.0),
                        correction.get("east", 0.0),
                        correction.get("down", 0.0),
                    )
                    intent = FlightIntent(IntentSource.COLLISION, IntentAction.MOVE_VELOCITY_NED, ttl_seconds=1.0, params=correction)
                else:
                    intent = FlightIntent(IntentSource.COLLISION, IntentAction.HOVER, ttl_seconds=1.0)
                self.state_store.submit_intent(intent)
                return

        # 4. Proceed with Formation Execution
        if self.nav.flight_manager.formation_params:
            params = self.nav.flight_manager.formation_params
            slot_assignments = params.get("slot_assignments", {})
            my_slot = slot_assignments.get(my_id, "?")
            logger.info("FORMATION_ACTIVE drone=%s slot=%s", my_id, my_slot)

            # Log anchor telemetry
            anchor_id = None
            for did, s in slot_assignments.items():
                if int(s) == 0:
                    anchor_id = did
                    break
            if anchor_id and anchor_id != my_id:
                anchor_peer = self.swarm.registry.get_peer(anchor_id)
                if anchor_peer and anchor_peer.last_position_time is not None:
                    age = time.time() - anchor_peer.last_position_time
                    logger.info(
                        "FORMATION_ANCHOR drone=%s anchor=%s lat=%s lon=%s age=%.3fs",
                        my_id, anchor_id, anchor_peer.lat, anchor_peer.lon, age
                    )
                else:
                    logger.warning(
                        "FORMATION_ANCHOR drone=%s anchor=%s MISSING or no position",
                        my_id, anchor_id
                    )

            # Log peer telemetry ages
            formation_members = params.get("members", list(slot_assignments.keys()))
            now = time.time()
            peer_ages = []
            for pid in formation_members:
                if pid == my_id:
                    continue
                p = self.swarm.registry.get_peer(pid)
                if p and p.last_position_time is not None:
                    peer_ages.append(f"{pid}:{now - p.last_position_time:.2f}s")
                else:
                    peer_ages.append(f"{pid}:MISSING")
            logger.info("FORMATION_PEERS drone=%s peers=%s", my_id, ",".join(peer_ages) if peer_ages else "none")

            intent = self.formation_engine.compute_intent(
                current_telemetry,
                peer_telemetry,
                params
            )
            logger.info(
                "FORMATION_INTENT drone=%s slot=%s action=%s params=%s",
                my_id, my_slot, intent.action.value, intent.params
            )
            if intent:
                self.state_store.submit_intent(intent)
            return

        # 5. Proceed with Mission Execution
        mission_state = self.mission.get_current_state()
        if mission_state == "RUNNING":
            wp = self.mission.get_current_waypoint()
            if wp:
                if getattr(self, '_waypoint_delay_start', None) is not None:
                    elapsed = time.monotonic() - self._waypoint_delay_start
                    if elapsed >= wp.delay:
                        logger.info(f"Waypoint delay of {wp.delay}s completed. Advancing mission.")
                        self._waypoint_delay_start = None
                        self.mission.advance_waypoint()
                    else:
                        intent = FlightIntent(IntentSource.MISSION, IntentAction.HOVER, ttl_seconds=1.0)
                        self.state_store.submit_intent(intent)
                    return

                reached = self.mission.executor.execute_waypoint(
                    current_telemetry,
                    self.mission.tracker.current_index
                )
                if reached:
                    logger.info("Waypoint reached.")
                    if wp.delay > 0:
                        logger.info(f"Starting waypoint delay of {wp.delay}s.")
                        self._waypoint_delay_start = time.monotonic()
                        intent = FlightIntent(IntentSource.MISSION, IntentAction.HOVER, ttl_seconds=1.0)
                        self.state_store.submit_intent(intent)
                    else:
                        logger.info("Advancing mission.")
                        self.mission.advance_waypoint()
