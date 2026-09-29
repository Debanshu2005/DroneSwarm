import time
import math
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global, global_offset_local_m
from DroneOS.core.repulsion_field import compute_repulsion
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction

logger = setup_logger("FormationEngine")

_ANCHOR_STALE_SEC = 3.0
_DEFAULT_SPACING = 10.0
_DEFAULT_MIN_SEP_M = 8.0


class FormationEngine:
    def __init__(self, swarm_manager, state_store, config=None):
        self.swarm_manager = swarm_manager
        self.state_store = state_store
        self.config = config
        self.form_mgr = FormationManager()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _my_slot(self, params: dict) -> int:
        """
        Return this drone's slot index from the explicit slot_assignments map.
        Returns -1 if this drone has no assignment.
        """
        slot_assignments = params.get("slot_assignments", {})
        my_id = self.swarm_manager.identity.drone_id
        if my_id not in slot_assignments:
            return -1
        return int(slot_assignments[my_id])

    def _total_drones(self, params: dict) -> int:
        members = params.get("members")
        if members:
            return len(members)
        slot_assignments = params.get("slot_assignments", {})
        if slot_assignments:
            return len(slot_assignments)
        return 1

    def _anchor_id(self, params: dict) -> str:
        """Slot 0 is always the anchor."""
        slot_assignments = params.get("slot_assignments", {})
        for drone_id, slot in slot_assignments.items():
            if int(slot) == 0:
                return drone_id
        # Fallback: first member alphabetically
        members = params.get("members")
        if members:
            return sorted(members)[0]
        return self.swarm_manager.identity.drone_id

    def _min_separation_m(self) -> float:
        """Read min_formation_separation_m from config, or use default."""
        if self.config and getattr(self.config, "formation", None):
            return float(getattr(self.config.formation, "min_formation_separation_m", _DEFAULT_MIN_SEP_M))
        return _DEFAULT_MIN_SEP_M

    def _peer_horizontal_dist(self, my_lat, my_lon, peer) -> float:
        """Flat-earth horizontal distance in metres from self to a peer object."""
        if peer.lat is None or peer.lon is None:
            return float("inf")
        dn, de = global_offset_local_m(my_lat, my_lon, peer.lat, peer.lon)
        return math.hypot(dn, de)

    # ------------------------------------------------------------------
    # Public API used by DecisionEngine (CA diagnostics)
    # ------------------------------------------------------------------

    def get_expected_positions(self, current_telemetry, params: dict) -> dict:
        """
        Returns {drone_id: (lat, lon)} for every member in the assignment,
        computed from the anchor position and each drone's slot offset.
        """
        f_type_str = params.get("type", "V").upper()
        try:
            f_type = FormationType(f_type_str)
        except ValueError:
            return {}

        spacing = float(params.get("spacing", _DEFAULT_SPACING))
        self.form_mgr.set_formation(f_type, spacing)

        slot_assignments = params.get("slot_assignments", {})
        if not slot_assignments:
            return {}

        total = self._total_drones(params)
        anchor_id = self._anchor_id(params)
        my_id = self.swarm_manager.identity.drone_id
        now = time.time()

        # Resolve anchor position
        if anchor_id == my_id:
            if not current_telemetry.gps_valid or current_telemetry.latitude is None:
                return {}
            anchor_lat = current_telemetry.latitude
            anchor_lon = current_telemetry.longitude
            anchor_alt = current_telemetry.altitude or 0.0
        else:
            anchor_peer = self.swarm_manager.registry.get_peer(anchor_id)
            if not (anchor_peer and anchor_peer.last_position_time is not None
                    and (now - anchor_peer.last_position_time) < _ANCHOR_STALE_SEC
                    and anchor_peer.lat is not None):
                return {}
            anchor_lat = anchor_peer.lat
            anchor_lon = anchor_peer.lon
            anchor_alt = anchor_peer.alt

        expected = {}
        for drone_id, slot in slot_assignments.items():
            dx_n, dy_e, _ = self.form_mgr.get_offset(int(slot), total)
            t_lat, t_lon, _ = convert_local_offset_to_global(anchor_lat, anchor_lon, anchor_alt, dx_n, dy_e)
            expected[drone_id] = (t_lat, t_lon)
        return expected

    # ------------------------------------------------------------------
    # Main intent computation
    # ------------------------------------------------------------------

    def compute_intent(self, current_telemetry, peer_telemetry, params: dict) -> FlightIntent:
        f_type_str = params.get("type", "V").upper()
        try:
            f_type = FormationType(f_type_str)
        except ValueError:
            logger.error("Invalid formation type: %s", f_type_str)
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        spacing = float(params.get("spacing", _DEFAULT_SPACING))
        speed = float(params.get("speed", 2.0))
        repulsion_radius_m = float(params.get("repulsion_radius_m", spacing * 0.4))
        self.form_mgr.set_formation(f_type, spacing)

        my_id = self.swarm_manager.identity.drone_id
        my_slot = self._my_slot(params)

        if my_slot < 0:
            now = time.time()
            if not hasattr(self, "_last_no_slot_warn") or (now - self._last_no_slot_warn) > 5.0:
                logger.warning("FORMATION_NO_SLOT drone=%s", my_id)
                self._last_no_slot_warn = now
            return FlightIntent(IntentSource.IDLE, IntentAction.IDLE)

        total = self._total_drones(params)
        anchor_id = self._anchor_id(params)

        logger.info("FORMATION_ASSIGNMENT drone=%s slot=%d", my_id, my_slot)

        if not current_telemetry.gps_valid:
            logger.warning("Formation engine waiting: GPS invalid.")
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        # Anchor should follow manual or mission commands, not be forced to hover.
        # By returning IDLE here, the anchor's arbiter will fall back to MANUAL/MISSION intents.
        if my_id == anchor_id:
            return FlightIntent(IntentSource.IDLE, IntentAction.IDLE)

        now = time.time()
        anchor_peer = self.swarm_manager.registry.get_peer(anchor_id)
        anchor_pos_valid = (
            anchor_peer is not None
            and anchor_peer.last_position_time is not None
            and (now - anchor_peer.last_position_time) < _ANCHOR_STALE_SEC
            and anchor_peer.lat is not None
            and anchor_peer.lon is not None
            and anchor_peer.alt is not None
        )

        if not anchor_pos_valid:
            logger.warning("Anchor %s position stale or missing. Hovering.", anchor_id)
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        # Compute slot offset (primary target)
        dx_north, dy_east, _ = self.form_mgr.get_offset(my_slot, total)

        # Repulsion correction (secondary, capped at 15% of spacing)
        if current_telemetry.latitude is not None and current_telemetry.longitude is not None:
            neighbor_offsets = []
            slot_assignments = params.get("slot_assignments", {})
            for p_id in slot_assignments:
                if p_id == my_id:
                    continue
                peer = self.swarm_manager.registry.get_peer(p_id)
                if (peer and peer.last_position_time is not None
                        and (now - peer.last_position_time) < _ANCHOR_STALE_SEC
                        and peer.lat is not None and peer.lon is not None):
                    p_n, p_e = global_offset_local_m(
                        current_telemetry.latitude, current_telemetry.longitude,
                        peer.lat, peer.lon
                    )
                    neighbor_offsets.append((p_n, p_e))

            rep_n, rep_e = compute_repulsion(
                neighbor_offsets,
                radius=repulsion_radius_m,
                gain=0.5,
                max_displacement=spacing * 0.15,   # cap at 15% of spacing
            )
            dx_north += rep_n
            dy_east += rep_e

        target_lat, target_lon, target_alt = convert_local_offset_to_global(
            anchor_peer.lat, anchor_peer.lon, anchor_peer.alt, dx_north, dy_east
        )

        if current_telemetry.latitude is None or current_telemetry.longitude is None:
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        error_north, error_east = global_offset_local_m(
            current_telemetry.latitude, current_telemetry.longitude,
            target_lat, target_lon
        )
        
        dist_to_target = math.hypot(error_north, error_east)
        if not hasattr(self, "_last_formation_log") or (now - self._last_formation_log) >= 1.0:
            anchor_age = now - anchor_peer.last_position_time
            logger.info("FORMATION_STATUS slot=%s anchor_id=%s anchor_age=%.2f dist_to_target=%.2f",
                        my_slot, anchor_id, anchor_age, dist_to_target)
            self._last_formation_log = now

        kp = 1.0
        if self.config and getattr(self.config, "formation", None):
            kp = float(self.config.formation.velocity_gain)

        vx = error_north * kp
        vy = error_east * kp

        magnitude = math.hypot(vx, vy)
        if magnitude > speed and magnitude > 0:
            scale = speed / magnitude
            vx *= scale
            vy *= scale

        # ------------------------------------------------------------------
        # Formation separation safety layer (secondary, does NOT change slot)
        # ------------------------------------------------------------------
        # Check actual physical distance to each formation peer.
        # If any peer is closer than min_formation_separation_m, scale down
        # the velocity toward that peer to prevent unsafe convergence.
        # This never reassigns slots — it only limits approach speed.
        # ------------------------------------------------------------------
        min_sep = self._min_separation_m()
        slot_assignments = params.get("slot_assignments", {})
        too_close = False

        for p_id in slot_assignments:
            if p_id == my_id:
                continue
            peer = self.swarm_manager.registry.get_peer(p_id)
            if not (peer and peer.last_position_time is not None
                    and (now - peer.last_position_time) < _ANCHOR_STALE_SEC
                    and peer.lat is not None and peer.lon is not None):
                continue

            dist = self._peer_horizontal_dist(
                current_telemetry.latitude, current_telemetry.longitude, peer
            )

            logger.debug(
                "FORMATION_SEPARATION drone=%s peer=%s distance=%.2fm min=%.2fm",
                my_id, p_id, dist, min_sep
            )

            if dist < min_sep:
                too_close = True
                # Compute unit vector from self toward peer
                dn, de = global_offset_local_m(
                    current_telemetry.latitude, current_telemetry.longitude,
                    peer.lat, peer.lon
                )
                peer_dist = math.hypot(dn, de)
                if peer_dist < 1e-6:
                    continue

                # Dot product of velocity with direction toward peer
                dot = (vx * dn + vy * de) / peer_dist

                if dot > 0:
                    # Velocity has a component toward the too-close peer.
                    # Scale it down proportionally: 0 at dist=0, 1 at dist=min_sep.
                    scale_factor = max(0.0, dist / min_sep)
                    vx *= scale_factor
                    vy *= scale_factor
                    logger.warning(
                        "FORMATION_STABILIZE drone=%s reason=too_close peer=%s "
                        "dist=%.2fm min=%.2fm action=LIMIT_VELOCITY scale=%.3f",
                        my_id, p_id, dist, min_sep, scale_factor
                    )

        if too_close and math.hypot(vx, vy) < 1e-4:
            # Fully blocked — hover rather than drift
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        logger.info(
            "FORMATION_TARGET drone=%s slot=%d north=%.3f east=%.3f vx=%.3f vy=%.3f",
            my_id, my_slot, target_lat, target_lon, vx, vy
        )

        return FlightIntent(
            IntentSource.FORMATION,
            IntentAction.MOVE_VELOCITY_NED,
            ttl_seconds=1.0,
            params={"north": vx, "east": vy, "down": 0.0, "yaw_rate": 0.0}
        )
