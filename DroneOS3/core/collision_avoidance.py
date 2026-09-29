import hashlib
import math
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

from DroneOS3.shared.config.models import CollisionAvoidanceConfig
from DroneOS3.shared.protocol.messages import TelemetryData
from DroneOS3.shared.utils.logger import setup_logger

logger = setup_logger("CollisionAvoidance")

_WARN_INTERVAL = 2.0


class ICollisionAvoidance(ABC):
    @abstractmethod
    def evaluate_threats(
        self,
        self_telemetry: TelemetryData,
        swarm_telemetry: Dict[str, TelemetryData],
    ) -> Tuple[str, Optional[Dict[str, float]], Optional[str], float]:
        pass


def _haversine_ne(lat1, lon1, lat2, lon2) -> Tuple[float, float]:
    """Return (north_m, east_m) displacement from point1 to point2."""
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    north = d_lat * 6371000.0
    east = d_lon * 6371000.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return north, east


class StandardCollisionAvoidance(ICollisionAvoidance):
    def __init__(self, config: Optional[CollisionAvoidanceConfig] = None, drone_id: str = ""):
        self.config = config or CollisionAvoidanceConfig()
        self.drone_id = drone_id
        self.enabled = self.config.enabled
        self.min_h_dist = self.config.min_horizontal_distance
        self.min_v_dist = self.config.min_vertical_distance
        self.warn_dist = self.config.warning_distance
        self.emg_dist = self.config.emergency_distance
        self.timeout = self.config.neighbor_timeout_sec
        self.lookahead = getattr(self.config, "lookahead_sec", 3.0)
        self.avoidance_speed = getattr(self.config, "avoidance_speed", 4.0)
        self.emergency_speed = getattr(self.config, "emergency_speed", 5.0)
        self.max_peer_age = getattr(self.config, "max_peer_age_sec", 1.0)
        self.ground_alt = getattr(self.config, "ground_altitude_m", 0.5)
        self._last_warn: Dict[str, float] = {}

    def evaluate_threats(
        self,
        self_telemetry: TelemetryData,
        swarm_telemetry: Dict[str, TelemetryData],
    ) -> Tuple[str, Optional[Dict[str, float]], Optional[str], float]:
        if not self.enabled:
            return "NORMAL", None, None, 0.0

        if not self_telemetry.gps_valid or self_telemetry.latitude is None or self_telemetry.longitude is None:
            return "NORMAL", None, None, 0.0

        if self_telemetry.armed_state != "ARMED":
            return "NORMAL", None, None, 0.0

        my_alt = self_telemetry.altitude or 0.0
        if my_alt < self.ground_alt:
            return "NORMAL", None, None, 0.0

        now = time.time()
        my_vx = self_telemetry.velocity_x or 0.0
        my_vy = self_telemetry.velocity_y or 0.0

        worst_state = "NORMAL"
        threat_peer = None
        min_dist_found = float("inf")

        # Accumulate weighted repulsion vectors across all peers
        sum_north = 0.0
        sum_east = 0.0
        sum_down = 0.0
        any_threat = False

        for peer_id, peer_t in swarm_telemetry.items():
            if peer_t.timestamp is None:
                continue
            age = now - peer_t.timestamp
            if age > self.max_peer_age:
                continue
            if not peer_t.gps_valid or peer_t.latitude is None or peer_t.longitude is None:
                continue

            peer_alt = peer_t.altitude or 0.0
            if peer_alt < self.ground_alt:
                continue

            north_m, east_m = _haversine_ne(
                self_telemetry.latitude, self_telemetry.longitude,
                peer_t.latitude, peer_t.longitude,
            )
            dist = math.sqrt(north_m ** 2 + east_m ** 2)
            alt_diff = abs(my_alt - peer_alt)

            if alt_diff > self.min_v_dist:
                continue

            # Age-compensated position: extrapolate peer forward by telemetry age (capped 1s)
            age_capped = min(age, 1.0)
            peer_vx = (peer_t.velocity_x or 0.0)
            peer_vy = (peer_t.velocity_y or 0.0)
            north_m += peer_vx * age_capped
            east_m += peer_vy * age_capped
            dist = math.sqrt(north_m ** 2 + east_m ** 2)

            # CPA: relative velocity of peer w.r.t. self
            rel_vx = peer_vx - my_vx
            rel_vy = peer_vy - my_vy
            rel_speed_sq = rel_vx ** 2 + rel_vy ** 2

            if rel_speed_sq > 1e-6:
                t_cpa = -(north_m * rel_vx + east_m * rel_vy) / rel_speed_sq
                t_cpa = max(0.0, min(t_cpa, self.lookahead))
                cpa_n = north_m + rel_vx * t_cpa
                cpa_e = east_m + rel_vy * t_cpa
                eff_dist = math.sqrt(cpa_n ** 2 + cpa_e ** 2)
                eff_dist = min(dist, eff_dist) if t_cpa > 0 else dist
            else:
                eff_dist = dist

            if eff_dist >= self.warn_dist and dist >= self.warn_dist:
                continue

            # Determine state from effective distance
            if eff_dist < self.emg_dist or dist < self.emg_dist:
                state = "EMERGENCY"
            elif eff_dist < self.min_h_dist or dist < self.min_h_dist:
                state = "AVOIDANCE"
            else:
                state = "WARNING"

            # Rate-limited warning log
            if state == "WARNING":
                last = self._last_warn.get(peer_id, 0.0)
                if now - last >= _WARN_INTERVAL:
                    logger.warning("CA WARNING peer=%s dist=%.1fm cpa=%.1fm", peer_id, dist, eff_dist)
                    self._last_warn[peer_id] = now

            any_threat = True
            if eff_dist < min_dist_found:
                min_dist_found = eff_dist
                worst_state = state
                threat_peer = peer_id

            if state in ("AVOIDANCE", "EMERGENCY"):

                # Repulsion weight: stronger when closer
                weight = 1.0 / max(eff_dist, 0.1)
                if dist > 1e-3:
                    rep_n = -(north_m / dist) * weight
                    rep_e = -(east_m / dist) * weight
                else:
                    # Symmetric fallback: deterministic hash direction
                    h = int(hashlib.md5(self.drone_id.encode()).hexdigest(), 16)
                    angle = (h % 360) * math.pi / 180.0
                    rep_n = math.cos(angle) * weight
                    rep_e = math.sin(angle) * weight

                rep_d = 0.0
                if state == "EMERGENCY":
                    rep_d = -1.5 if my_alt > peer_alt else 1.5

                sum_north += rep_n
                sum_east += rep_e
                sum_down += rep_d

        if not any_threat:
            return "NORMAL", None, None, 0.0

        # Normalise and scale to avoidance/emergency speed
        mag = math.sqrt(sum_north ** 2 + sum_east ** 2)
        speed = self.emergency_speed if worst_state == "EMERGENCY" else self.avoidance_speed

        if worst_state == "WARNING":
            return "WARNING", None, threat_peer, min_dist_found
        if mag > 1e-6:
            scale = speed / mag
            out_n = sum_north * scale
            out_e = sum_east * scale
        else:
            h = int(hashlib.md5(self.drone_id.encode()).hexdigest(), 16)
            angle = (h % 360) * math.pi / 180.0
            out_n = speed * math.cos(angle)
            out_e = speed * math.sin(angle)

        correction = {"north": out_n, "east": out_e, "down": sum_down, "duration": 1.0}
        return worst_state, correction, threat_peer, min_dist_found
