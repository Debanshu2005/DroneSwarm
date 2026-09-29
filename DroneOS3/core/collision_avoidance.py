import math
import time
from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional
from DroneOS3.shared.utils.logger import setup_logger
from DroneOS3.shared.protocol.messages import TelemetryData
from DroneOS3.shared.config.models import CollisionAvoidanceConfig

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
        self.timeout = self.config.neighbor_timeout_sec

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

        worst_state = "NORMAL"
        best_correction = None
        threat_peer = None
        min_dist_found = float("inf")

        for peer_id, peer_t in swarm_telemetry.items():
            if not peer_t.gps_valid or peer_t.latitude is None or peer_t.longitude is None:
                continue

            if peer_t.timestamp is not None:
                age = time.time() - peer_t.timestamp
                if age > self.timeout:
                    continue

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

            if state != "NORMAL":
                if dist < min_dist_found:
                    min_dist_found = dist
                    worst_state = state
                    threat_peer = peer_id

                    bearing = math.atan2(
                        math.sin(delta_lambda) * math.cos(phi2),
                        math.cos(phi1) * math.sin(phi2)
                        - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda),
                    )
                    escape_bearing = bearing + math.pi
                    speed = 5.0 if state == "EMERGENCY" else 2.0
                    north = speed * math.cos(escape_bearing)
                    east = speed * math.sin(escape_bearing)
                    down = 0.0
                    best_correction = {"north": north, "east": east, "down": down, "duration": 1.0}

        if worst_state == "WARNING":
            return "WARNING", None, threat_peer, min_dist_found

        return worst_state, best_correction, threat_peer, min_dist_found
