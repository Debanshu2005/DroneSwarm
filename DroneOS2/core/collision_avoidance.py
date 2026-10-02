import math
import time
from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional
from DroneOS.shared.utils.logger import setup_logger
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.shared.config.models import CollisionAvoidanceConfig

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

    def evaluate_threats(
        self,
        self_telemetry: TelemetryData,
        swarm_telemetry: Dict[str, TelemetryData],
    ) -> Tuple[str, Optional[Dict[str, float]], Optional[str], float]:
        if not self.enabled:
            return "NORMAL", None, None, 0.0

        if not self_telemetry.gps_valid or self_telemetry.latitude is None or self_telemetry.longitude is None:
            return "NORMAL", None, None, 0.0

        my_lat = self_telemetry.latitude
        my_lon = self_telemetry.longitude
        my_alt = self_telemetry.altitude or 0.0

        if my_alt < getattr(self.config, 'ground_altitude_m', 0.5):
            return "NORMAL", None, None, 0.0

        worst_state = "NORMAL"
        best_correction = None
        threat_peer = None
        min_dist_found = float("inf")

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
