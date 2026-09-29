import math
import time
import hashlib
from abc import ABC, abstractmethod
from typing import Dict, Tuple, Optional
from DroneOS2.shared.utils.logger import setup_logger
from DroneOS2.shared.protocol.messages import TelemetryData
from DroneOS2.shared.config.models import CollisionAvoidanceConfig

logger = setup_logger("CollisionAvoidance")

# Per-peer stale-timestamp log gate: peer_id -> last log time
_stale_ts_logged: Dict[str, float] = {}


def _haversine_ne(my_lat, my_lon, peer_lat, peer_lon) -> Tuple[float, float]:
    """Return (north_m, east_m) from self to peer using equirectangular approx."""
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
    def __init__(self, config: Optional[CollisionAvoidanceConfig] = None, drone_id: str = "drone"):
        self.config = config or CollisionAvoidanceConfig()
        self.enabled = self.config.enabled
        self.min_h_dist = self.config.min_horizontal_distance
        self.min_v_dist = self.config.min_vertical_distance
        self.warn_dist = self.config.warning_distance
        self.emg_dist = self.config.emergency_distance
        self.timeout = self.config.neighbor_timeout_sec
        self.lookahead = self.config.lookahead_sec
        self.avoidance_speed = self.config.avoidance_speed
        self.emergency_speed = self.config.emergency_speed
        self.max_peer_age = self.config.max_peer_age_sec
        self.ground_alt = self.config.ground_altitude_m
        self.drone_id = drone_id
        self._warn_log_times: Dict[str, float] = {}

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

        if my_alt < self.ground_alt:
            return "NORMAL", None, None, 0.0

        if getattr(self_telemetry, "armed_state", None) == "DISARMED":
            return "NORMAL", None, None, 0.0

        my_vn = self_telemetry.velocity_x or 0.0
        my_ve = self_telemetry.velocity_y or 0.0

        now = time.time()

        sum_n = 0.0
        sum_e = 0.0
        worst_state = "NORMAL"
        threat_peer = None
        min_eff_dist = float("inf")
        any_threat = False

        for peer_id, peer_t in swarm_telemetry.items():
            if not peer_t.gps_valid or peer_t.latitude is None or peer_t.longitude is None:
                continue

            if peer_t.timestamp is None:
                global _stale_ts_logged
                last = _stale_ts_logged.get(peer_id, 0.0)
                if now - last > 10.0:
                    logger.debug("COLLISION_AVOIDANCE peer %s has no timestamp, skipping", peer_id)
                    _stale_ts_logged[peer_id] = now
                continue

            age = now - peer_t.timestamp
            if age > self.max_peer_age:
                continue

            peer_alt = peer_t.altitude or 0.0

            if peer_alt < self.ground_alt:
                continue

            if abs(my_alt - peer_alt) > self.min_v_dist:
                continue

            rn, re = _haversine_ne(my_lat, my_lon, peer_t.latitude, peer_t.longitude)

            peer_vn = peer_t.velocity_x or 0.0
            peer_ve = peer_t.velocity_y or 0.0
            age_cap = min(age, 1.0)
            rn += peer_vn * age_cap
            re += peer_ve * age_cap

            current_dist = math.sqrt(rn * rn + re * re)

            dvn = peer_vn - my_vn
            dve = peer_ve - my_ve
            v_sq = dvn * dvn + dve * dve
            if v_sq > 1e-6:
                t_cpa = -(rn * dvn + re * dve) / v_sq
                t_cpa = max(0.0, min(t_cpa, self.lookahead))
                miss_n = rn + dvn * t_cpa
                miss_e = re + dve * t_cpa
                miss_dist = math.sqrt(miss_n * miss_n + miss_e * miss_e)
                eff_dist = min(current_dist, miss_dist) if t_cpa > 0 else current_dist
            else:
                eff_dist = current_dist

            if eff_dist < self.emg_dist:
                state = "EMERGENCY"
            elif eff_dist < self.min_h_dist:
                state = "AVOIDANCE"
            elif eff_dist < self.warn_dist:
                state = "WARNING"
            else:
                continue

            any_threat = True

            state_rank = {"WARNING": 1, "AVOIDANCE": 2, "EMERGENCY": 3}
            if state_rank[state] > state_rank.get(worst_state, 0):
                worst_state = state
            if eff_dist < min_eff_dist:
                min_eff_dist = eff_dist
                threat_peer = peer_id

            if state in ("AVOIDANCE", "EMERGENCY"):
                threshold = self.emg_dist if state == "EMERGENCY" else self.min_h_dist
                weight = threshold / max(eff_dist, 0.01)
                mag = math.sqrt(rn * rn + re * re)
                if mag > 1e-6:
                    sum_n += weight * (-rn / mag)
                    sum_e += weight * (-re / mag)

        if not any_threat:
            return "NORMAL", None, None, 0.0

        if worst_state == "WARNING":
            now2 = time.time()
            last_warn = self._warn_log_times.get(threat_peer, 0.0)
            if now2 - last_warn >= 2.0:
                logger.warning(
                    "COLLISION_AVOIDANCE WARNING peer=%s eff_dist=%.2f",
                    threat_peer, min_eff_dist
                )
                self._warn_log_times[threat_peer] = now2
            return "WARNING", None, threat_peer, min_eff_dist

        speed = self.emergency_speed if worst_state == "EMERGENCY" else self.avoidance_speed
        vec_mag = math.sqrt(sum_n * sum_n + sum_e * sum_e)

        if vec_mag < 1e-6:
            h = int(hashlib.md5(self.drone_id.encode()).hexdigest(), 16)
            angle = (h % 360) * math.pi / 180.0
            sum_n = math.cos(angle)
            sum_e = math.sin(angle)
            vec_mag = 1.0

        north = speed * sum_n / vec_mag
        east = speed * sum_e / vec_mag
        down = 0.0

        if worst_state == "EMERGENCY":
            peer_alt_ref = (swarm_telemetry[threat_peer].altitude or 0.0) if threat_peer in swarm_telemetry else my_alt
            if my_alt >= peer_alt_ref:
                down = -1.5
            elif my_alt > 3.0:
                down = 1.5

        correction = {"north": north, "east": east, "down": down, "duration": 1.0}
        return worst_state, correction, threat_peer, min_eff_dist
