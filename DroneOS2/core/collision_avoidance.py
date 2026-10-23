import math
import time
from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional, List
from DroneOS2.shared.utils.logger import setup_logger
from DroneOS2.shared.protocol.messages import TelemetryData
from DroneOS2.shared.config.models import CollisionAvoidanceConfig

logger = setup_logger("CollisionAvoidance")


def _haversine_ne(my_lat, my_lon, peer_lat, peer_lon) -> Tuple[float, float]:
    """Return (north_m, east_m) from self to peer — used by DecisionEngine diagnostics."""
    R = 6371000.0
    dlat = math.radians(peer_lat - my_lat)
    dlon = math.radians(peer_lon - my_lon)
    north = R * dlat
    east = R * dlon * math.cos(math.radians(my_lat))
    return north, east


class ICollisionAvoidance(ABC):
    @abstractmethod
    def evaluate_threats(
        self,
        self_telemetry: TelemetryData,
        swarm_telemetry: Dict[str, TelemetryData],
    ) -> Tuple[str, Optional[Dict[str, float]], Optional[str], float]:
        """
        Returns (safety_state, corrective_velocity_vector, peer_id, distance)
        safety_state: NORMAL, WARNING, AVOIDANCE, EMERGENCY
        """
        pass


class StandardCollisionAvoidance(ICollisionAvoidance):
    """
    Collision avoidance using direct haversine distance comparison.
    Behavioral baseline from commit 8f5b44c.
    """
    def __init__(self, config: Optional[CollisionAvoidanceConfig] = None, drone_id: str = "drone"):
        self.config = config or CollisionAvoidanceConfig()
        self.drone_id = drone_id
        self.enabled = self.config.enabled
        self.min_h_dist = self.config.min_horizontal_distance
        self.min_v_dist = self.config.min_vertical_distance
        self.warn_dist = self.config.warning_distance
        self.emg_dist = self.config.emergency_distance
        self.timeout = self.config.neighbor_timeout_sec # Cutoff for treating a peer as gone
        self.max_peer_age_sec = self.config.max_peer_age_sec # Staleness gate for CA evaluation
        self.lookahead_sec = self.config.lookahead_sec
        self.avoidance_speed = self.config.avoidance_speed
        self.emergency_speed = self.config.emergency_speed
        self.multi_threat_avoidance = getattr(self.config, 'multi_threat_avoidance', False)
        self.vertical_escape_enabled = getattr(self.config, 'vertical_escape_enabled', True)
        self.vertical_escape_speed = getattr(self.config, 'vertical_escape_speed', 1.5)
        self.last_threats: List[Tuple[str, str, float]] = []

    def evaluate_threats(
        self,
        self_telemetry: TelemetryData,
        swarm_telemetry: Dict[str, TelemetryData],
    ) -> Tuple[str, Optional[Dict[str, float]], Optional[str], float]:
        self.last_threats = []

        if not self.enabled:
            return "NORMAL", None, None, 0.0

        if not self_telemetry.gps_valid or self_telemetry.latitude is None or self_telemetry.longitude is None:
            return "NORMAL", None, None, 0.0

        my_lat = self_telemetry.latitude
        my_lon = self_telemetry.longitude
        my_alt = self_telemetry.altitude or 0.0

        if my_alt < getattr(self.config, 'ground_altitude_m', 0.5):
            return "NORMAL", None, None, 0.0

        threat_items = []

        for peer_id, peer_t in swarm_telemetry.items():
            if not peer_t.gps_valid or peer_t.latitude is None or peer_t.longitude is None:
                continue

            # Staleness gate for CA evaluation using max_peer_age_sec
            if peer_t.timestamp is not None:
                age = time.time() - peer_t.timestamp
                if age > self.max_peer_age_sec:
                    continue

            # Haversine distance (meters)
            R = 6371000
            phi1 = math.radians(my_lat)
            phi2 = math.radians(peer_t.latitude)
            delta_phi = math.radians(peer_t.latitude - my_lat)
            delta_lambda = math.radians(peer_t.longitude - my_lon)

            a = (math.sin(delta_phi / 2.0) ** 2
                 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            dist = R * c

            alt_diff = abs(my_alt - (peer_t.altitude or 0.0))
            if alt_diff > self.min_v_dist:
                continue

            if dist < self.emg_dist:
                state = "EMERGENCY"
            elif dist < self.min_h_dist:
                state = "AVOIDANCE"
            elif dist < self.warn_dist:
                state = "WARNING"
            else:
                state = "NORMAL"

            # Predictive check
            if state != "EMERGENCY" and peer_t.velocity_x is not None and peer_t.velocity_y is not None:
                my_vn = self_telemetry.velocity_x or 0.0
                my_ve = self_telemetry.velocity_y or 0.0
                rel_vn = peer_t.velocity_x - my_vn
                rel_ve = peer_t.velocity_y - my_ve
                
                # We project positions over lookahead_sec
                # (north_m, east_m) distance currently:
                dn, de = _haversine_ne(my_lat, my_lon, peer_t.latitude, peer_t.longitude)
                
                proj_n = dn + rel_vn * self.lookahead_sec
                proj_e = de + rel_ve * self.lookahead_sec
                proj_dist = math.sqrt(proj_n**2 + proj_e**2)
                
                # Only escalate if they are getting closer and will breach min_h_dist
                if proj_dist < self.min_h_dist and proj_dist < dist:
                    # Escalate one level
                    if state == "NORMAL":
                        state = "WARNING"
                    elif state == "WARNING":
                        state = "AVOIDANCE"
                    elif state == "AVOIDANCE":
                        state = "EMERGENCY"

            if state != "NORMAL":
                threat_items.append((peer_id, state, dist, delta_lambda, phi1, phi2))

        self.last_threats = [(item[0], item[1], item[2]) for item in threat_items]

        if not threat_items:
            return "NORMAL", None, None, 0.0

        if not self.multi_threat_avoidance:
            worst_state = "NORMAL"
            best_correction = None
            threat_peer = None
            min_dist_found = float("inf")

            for peer_id, state, dist, delta_lambda, phi1, phi2 in threat_items:
                if dist < min_dist_found:
                    min_dist_found = dist
                    worst_state = state
                    threat_peer = peer_id

                    # Bearing from self to peer, then escape in opposite direction
                    bearing = math.atan2(
                        math.sin(delta_lambda) * math.cos(phi2),
                        math.cos(phi1) * math.sin(phi2)
                        - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda),
                    )
                    escape_bearing = bearing + math.pi
                    speed = self.emergency_speed if state == "EMERGENCY" else self.avoidance_speed
                    north = speed * math.cos(escape_bearing)
                    east = speed * math.sin(escape_bearing)
                    down = 0.0
                    best_correction = {"north": north, "east": east, "down": down, "duration": 1.0}

            if worst_state == "WARNING":
                warn_correction = None
                if best_correction:
                    warn_correction = {
                        "north": 0.5 * best_correction["north"],
                        "east": 0.5 * best_correction["east"],
                        "down": 0.0,
                        "duration": 1.0
                    }
                return "WARNING", warn_correction, threat_peer, min_dist_found

            return worst_state, best_correction, threat_peer, min_dist_found

        # Multi-threat avoidance mode
        # LIMITATION NOTE:
        # This pairwise vote cannot guarantee separation for three or more drones
        # stacked in a single vertical column, as that would require a global altitude
        # stagger planner. This reactive pairwise layer handles 2-drone vertical escape
        # and multi-threat horizontal push synthesis.

        SEVERITY_WEIGHT = {"WARNING": 0.5, "AVOIDANCE": 1.0, "EMERGENCY": 2.0}

        total_w = 0.0
        sum_w_u_n = 0.0
        sum_w_u_e = 0.0
        threat_details = []
        force_vertical = False

        for peer_id, state, dist, delta_lambda, phi1, phi2 in threat_items:
            severity = SEVERITY_WEIGHT[state]
            w_i = severity / max(dist, 0.5)

            # Edge case (section 3, item 7): horizontal distance below 0.05m
            if dist < 0.05:
                u_n = 1.0  # Deterministic fallback direction: north
                u_e = 0.0
                force_vertical = True
            else:
                bearing = math.atan2(
                    math.sin(delta_lambda) * math.cos(phi2),
                    math.cos(phi1) * math.sin(phi2)
                    - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda),
                )
                escape_bearing = bearing + math.pi
                u_n = math.cos(escape_bearing)
                u_e = math.sin(escape_bearing)

            total_w += w_i
            sum_w_u_n += w_i * u_n
            sum_w_u_e += w_i * u_e
            threat_details.append((peer_id, state, dist, w_i, u_n, u_e))

        # 5. Worst state, peer_id (highest severity, ties: nearest), and min distance
        sorted_threats = sorted(threat_items, key=lambda t: (-SEVERITY_WEIGHT[t[1]], t[2]))
        threat_peer = sorted_threats[0][0]
        worst_state = sorted_threats[0][1]
        min_dist_found = min(t[2] for t in threat_items)

        # 3. Horizontal command
        norm_sum = math.hypot(sum_w_u_n, sum_w_u_e)
        ratio = norm_sum / total_w if total_w > 0 else 0.0

        if worst_state == "EMERGENCY":
            speed = self.emergency_speed
        elif worst_state == "AVOIDANCE":
            speed = self.avoidance_speed
        else:  # WARNING
            speed = 0.5 * self.avoidance_speed

        v_n = speed * (sum_w_u_n / total_w)
        v_e = speed * (sum_w_u_e / total_w)

        # 6. Vertical escape
        down = 0.0
        duration = 1.0

        if (ratio < 0.5 or force_vertical) and self.vertical_escape_enabled:
            vote_total = 0.0
            for peer_id, state, dist, w_i, u_n, u_e in threat_details:
                vote = 1.0 if self.drone_id < peer_id else -1.0
                vote_total += w_i * vote

            if vote_total > 0:
                down = -self.vertical_escape_speed  # climb (NED negative is up)
            elif vote_total < 0:
                down = self.vertical_escape_speed   # descend (NED positive is down)
            else:
                down = -self.vertical_escape_speed  # zero climbs

            # Safety overrides:
            # - Never descend if altitude - vertical_escape_speed * duration < ground_altitude_m + 2.0
            ground_limit = getattr(self.config, 'ground_altitude_m', 0.5) + 2.0
            if down > 0.0 and (my_alt - self.vertical_escape_speed * duration) < ground_limit:
                down = 0.0

            # - Never climb above any maximum altitude defined in configs
            max_alt = getattr(self.config, 'max_altitude', None)
            if down < 0.0 and max_alt is not None and (my_alt + self.vertical_escape_speed * duration) > max_alt:
                down = 0.0

        # Section 8 Logging: When the flag is on and there is more than one threat, log one line per tick at INFO
        if len(threat_items) > 1:
            threats_str = ",".join(f"{t[0]}:{t[1]}:{t[2]:.2f}" for t in threat_items)
            vert_str = "climb" if down < 0.0 else ("descend" if down > 0.0 else "none")
            logger.info(
                "CA_MULTI self=%s threats=%s ratio=%.2f vertical=%s",
                self.drone_id, threats_str, ratio, vert_str
            )

        correction = {"north": v_n, "east": v_e, "down": down, "duration": duration}
        return worst_state, correction, threat_peer, min_dist_found

