import time
import math
from DroneOS2.shared.utils.logger import setup_logger
from DroneOS2.core.formation_manager import FormationManager, FormationType, convert_local_offset_to_global, global_offset_local_m
from DroneOS2.core.repulsion_field import compute_repulsion
from DroneOS2.core.intents import FlightIntent, IntentSource, IntentAction

logger = setup_logger("FormationEngine")

_ANCHOR_STALE_SEC = 3.0
_DEFAULT_SPACING = 10.0
_DEFAULT_MIN_SEP_M = 8.0
_FORMATION_STARTUP_GRACE_SEC = 5.0  # grace period before anchor-missing becomes a WARNING
# Rate-limit period for high-frequency per-tick INFO logs (seconds).
_LOG_RATE_SEC = 1.0


class FormationEngine:
    def __init__(self, swarm_manager, state_store, config=None):
        self.swarm_manager = swarm_manager
        self.state_store = state_store
        self.config = config
        self.form_mgr = FormationManager()
        self._formation_activated_at: float = 0.0  # time.time() when formation was first entered
        # Rate-limit timestamps keyed by log tag.
        self._log_ts: dict = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rl(self, tag: str, now: float) -> bool:
        """Return True when the caller should emit the log (rate-limited to 1/s)."""
        last = self._log_ts.get(tag, 0.0)
        if now - last >= _LOG_RATE_SEC:
            self._log_ts[tag] = now
            return True
        return False

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

    def _anchor_relative_offset(self, slot: int, total: int) -> tuple:
        """
        Compute the NED offset of `slot` relative to the anchor (slot 0).

        For squadron shapes (V, LINE, COLUMN, etc.) slot 0 has offset (0, 0, 0),
        so this returns get_offset(slot) unchanged.

        For assembly shapes (CIRCLE, SQUARE) slot 0 has a non-zero ring position.
        Subtracting the anchor's own ring offset makes followers position themselves
        relative to the anchor's ACTUAL GPS position rather than the ring center,
        so the anchor ends up ON the ring instead of at its center.
        """
        slot_off = self.form_mgr.get_offset(slot, total)
        anchor_off = self.form_mgr.get_offset(0, total)
        return (
            slot_off[0] - anchor_off[0],
            slot_off[1] - anchor_off[1],
            slot_off[2] - anchor_off[2],
        )

    # ------------------------------------------------------------------
    # Public API used by DecisionEngine (CA diagnostics)
    # ------------------------------------------------------------------

    def get_expected_positions(self, current_telemetry, params: dict) -> dict:
        """
        Returns {drone_id: (lat, lon)} for every member in the assignment,
        computed from the anchor position and each drone's anchor-relative slot offset.
        Uses _anchor_relative_offset so CIRCLE and SQUARE place the anchor on the ring.
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
            dx_n, dy_e, _ = self._anchor_relative_offset(int(slot), total)
            t_lat, t_lon, _ = convert_local_offset_to_global(anchor_lat, anchor_lon, anchor_alt, dx_n, dy_e)
            expected[drone_id] = (t_lat, t_lon)
        return expected

    # ------------------------------------------------------------------
    # Main intent computation
    # ------------------------------------------------------------------

    def compute_intent(self, current_telemetry, peer_telemetry, params: dict) -> FlightIntent:
        now = time.time()

        # Track when this drone first entered the current formation session.
        # Reset whenever params change (new formation command received).
        params_key = (params.get("type"), tuple(sorted(params.get("slot_assignments", {}).items())))
        if not hasattr(self, "_last_params_key") or self._last_params_key != params_key:
            self._last_params_key = params_key
            self._formation_activated_at = now

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
            if self._rl("no_slot", now):
                logger.warning("FORMATION_NO_SLOT drone=%s", my_id)
            return FlightIntent(IntentSource.IDLE, IntentAction.IDLE)

        total = self._total_drones(params)
        anchor_id = self._anchor_id(params)

        if self._rl("assignment", now):
            logger.info("FORMATION_ASSIGNMENT drone=%s slot=%d", my_id, my_slot)

        if not current_telemetry.gps_valid:
            logger.warning("Formation engine waiting: GPS invalid.")
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        # Anchor should follow manual or mission commands, not be forced to hover.
        # By returning IDLE here, the anchor's arbiter will fall back to MANUAL/MISSION intents.
        if my_id == anchor_id:
            if self._rl("anchor_status", now):
                logger.info(
                    "FORMATION_STATUS drone=%s type=%s slot=0 anchor=%s target_err_m=0.00 speed_mps=0.00",
                    my_id, f_type_str, anchor_id
                )
            return FlightIntent(IntentSource.IDLE, IntentAction.IDLE)

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
            in_grace = (now - self._formation_activated_at) < _FORMATION_STARTUP_GRACE_SEC
            if in_grace:
                # Log at INFO once per second during startup grace so it is visible.
                if self._rl("grace", now):
                    logger.info(
                        "Anchor %s position not yet available (startup grace %.1fs remaining). Hovering.",
                        anchor_id, _FORMATION_STARTUP_GRACE_SEC - (now - self._formation_activated_at)
                    )
            else:
                logger.warning("Anchor %s position stale or missing. Hovering.", anchor_id)
            return FlightIntent(IntentSource.FORMATION, IntentAction.HOVER, ttl_seconds=1.0)

        # Compute anchor-relative slot offset.
        # This fixes CIRCLE and SQUARE: followers target anchor_pos + relative_offset
        # so the anchor stays on the ring and does not end up at the ring center.
        # Squadron shapes (V, LINE, COLUMN, etc.) have anchor offset (0,0), so
        # _anchor_relative_offset returns the same value as get_offset for them.
        dx_north, dy_east, _ = self._anchor_relative_offset(my_slot, total)

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

        # Once-per-second FORMATION_STATUS line for this follower drone.
        if self._rl("status", now):
            anchor_age = now - anchor_peer.last_position_time
            logger.info(
                "FORMATION_STATUS drone=%s type=%s slot=%d anchor=%s "
                "target_err_m=%.2f speed_mps=%.2f anchor_age=%.2fs",
                my_id, f_type_str, my_slot, anchor_id,
                dist_to_target, math.hypot(vx, vy), anchor_age
            )

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

        if self._rl("intent", now):
            logger.info(
                "FORMATION_INTENT drone=%s slot=%d action=MOVE_VELOCITY_NED north=%.3f east=%.3f",
                my_id, my_slot, vx, vy
            )

        return FlightIntent(
            IntentSource.FORMATION,
            IntentAction.MOVE_VELOCITY_NED,
            ttl_seconds=1.0,
            params={"north": vx, "east": vy, "down": 0.0, "yaw_rate": 0.0}
        )
