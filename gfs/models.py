"""
Data models for the Global Fusion Service (GFS).
"""

from __future__ import annotations
import math
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


class LifecycleState(str, Enum):
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


# WGS84 Constants
WGS84_A = 6378137.0  # semi-major axis in metres
WGS84_F = 1.0 / 298.257223563  # flattening
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2  # first eccentricity squared


def wgs84_to_enu(lat: float, lon: float, alt: float, ref_lat: float, ref_lon: float, ref_alt: float = 0.0) -> Tuple[float, float, float]:
    """
    Converts WGS84 geodetic coordinates to local East, North, Up (ENU) in metres
    relative to a reference origin.
    """
    phi = math.radians(lat)
    lam = math.radians(lon)
    phi0 = math.radians(ref_lat)
    lam0 = math.radians(ref_lon)

    # Meridian and Prime Vertical radii of curvature
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)

    N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi ** 2)
    X = (N + alt) * cos_phi * cos_lam
    Y = (N + alt) * cos_phi * sin_lam
    Z = (N * (1.0 - WGS84_E2) + alt) * sin_phi

    sin_phi0 = math.sin(phi0)
    cos_phi0 = math.cos(phi0)
    sin_lam0 = math.sin(lam0)
    cos_lam0 = math.cos(lam0)

    N0 = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi0 ** 2)
    X0 = (N0 + ref_alt) * cos_phi0 * cos_lam0
    Y0 = (N0 + ref_alt) * cos_phi0 * sin_lam0
    Z0 = (N0 * (1.0 - WGS84_E2) + ref_alt) * sin_phi0

    dX = X - X0
    dY = Y - Y0
    dZ = Z - Z0

    east = -sin_lam0 * dX + cos_lam0 * dY
    north = -sin_phi0 * cos_lam0 * dX - sin_phi0 * sin_lam0 * dY + cos_phi0 * dZ
    up = cos_phi0 * cos_lam0 * dX + cos_phi0 * sin_lam0 * dY + sin_phi0 * dZ

    return east, north, up


def enu_to_wgs84(east: float, north: float, up: float, ref_lat: float, ref_lon: float, ref_alt: float = 0.0) -> Tuple[float, float, float]:
    """
    Converts local ENU coordinates in metres back to WGS84 geodetic coordinates (lat, lon, alt).
    """
    phi0 = math.radians(ref_lat)
    lam0 = math.radians(ref_lon)
    sin_phi0 = math.sin(phi0)
    cos_phi0 = math.cos(phi0)
    sin_lam0 = math.sin(lam0)
    cos_lam0 = math.cos(lam0)

    N0 = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi0 ** 2)
    X0 = (N0 + ref_alt) * cos_phi0 * cos_lam0
    Y0 = (N0 + ref_alt) * cos_phi0 * sin_lam0
    Z0 = (N0 * (1.0 - WGS84_E2) + ref_alt) * sin_phi0

    # Invert rotation
    dX = -sin_lam0 * east - sin_phi0 * cos_lam0 * north + cos_phi0 * cos_lam0 * up
    dY = cos_lam0 * east - sin_phi0 * sin_lam0 * north + cos_phi0 * sin_lam0 * up
    dZ = cos_phi0 * north + sin_phi0 * up

    X = X0 + dX
    Y = Y0 + dY
    Z = Z0 + dZ

    # Bowring's method for geodetic coordinates from ECEF
    p = math.hypot(X, Y)
    theta = math.atan2(Z * WGS84_A, p * (WGS84_A * (1.0 - WGS84_F)))
    b = WGS84_A * (1.0 - WGS84_F)
    e_prime2 = (WGS84_A ** 2 - b ** 2) / (b ** 2)

    lat = math.atan2(
        Z + e_prime2 * b * (math.sin(theta) ** 3),
        p - WGS84_E2 * WGS84_A * (math.cos(theta) ** 3)
    )
    lon = math.atan2(Y, X)
    N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * (math.sin(lat) ** 2))
    alt = p / math.cos(lat) - N

    return math.degrees(lat), math.degrees(lon), alt


@dataclass
class Observation:
    obs_id: str
    drone_id: str
    drone_session_id: str
    session_generation: int
    frame_id: int
    local_track_id: str
    capture_timestamp_ms: int
    lat: float
    lon: float
    alt: float
    cov_xx: float
    cov_yy: float
    cov_xy: float
    label: str
    confidence: float
    payload_hash: str
    east_m: float = 0.0
    north_m: float = 0.0
    up_m: float = 0.0
    raw_payload: Optional[str] = None


@dataclass
class LocalTracklet:
    """
    Aggregates observations from a single drone having the same local_track_id
    across one or more frames within the current batch.
    """
    drone_id: str
    drone_session_id: str
    session_generation: int
    local_track_id: str
    label: str
    observations: List[Observation] = field(default_factory=list)
    east_m: float = 0.0
    north_m: float = 0.0
    up_m: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    cov_xx: float = 0.25
    cov_yy: float = 0.25
    cov_xy: float = 0.0
    confidence: float = 0.0
    first_frame_id: int = 0
    last_frame_id: int = 0
    first_seen_ms: int = 0
    last_seen_ms: int = 0

    @classmethod
    def from_observations(cls, obs_list: List[Observation]) -> LocalTracklet:
        if not obs_list:
            raise ValueError("Cannot create LocalTracklet from empty observation list")
        # Ensure all observations share drone, session, gen, and local_track_id
        seed = obs_list[0]
        for o in obs_list:
            if o.drone_id != seed.drone_id or o.local_track_id != seed.local_track_id:
                raise ValueError("All observations in LocalTracklet must share drone_id and local_track_id")
        
        # Sort chronologically by frame_id and timestamp
        sorted_obs = sorted(obs_list, key=lambda o: (o.frame_id, o.capture_timestamp_ms))
        
        # Inverse-variance weighting for horizontal coordinates (East, North)
        sum_w_east = 0.0
        sum_w_north = 0.0
        weighted_east = 0.0
        weighted_north = 0.0
        
        for o in sorted_obs:
            w_x = 1.0 / max(o.cov_xx, 1e-4)
            w_y = 1.0 / max(o.cov_yy, 1e-4)
            weighted_east += o.east_m * w_x
            weighted_north += o.north_m * w_y
            sum_w_east += w_x
            sum_w_north += w_y

        fused_east = weighted_east / sum_w_east
        fused_north = weighted_north / sum_w_north
        # Variance of the weighted mean
        fused_cov_xx = 1.0 / sum_w_east
        fused_cov_yy = 1.0 / sum_w_north
        fused_cov_xy = sorted_obs[-1].cov_xy  # take latest correlation

        latest = sorted_obs[-1]
        max_conf = max(o.confidence for o in sorted_obs)

        return cls(
            drone_id=seed.drone_id,
            drone_session_id=seed.drone_session_id,
            session_generation=seed.session_generation,
            local_track_id=seed.local_track_id,
            label=seed.label,
            observations=sorted_obs,
            east_m=fused_east,
            north_m=fused_north,
            up_m=latest.up_m,
            lat=latest.lat,
            lon=latest.lon,
            alt=latest.alt,
            cov_xx=fused_cov_xx,
            cov_yy=fused_cov_yy,
            cov_xy=fused_cov_xy,
            confidence=max_conf,
            first_frame_id=sorted_obs[0].frame_id,
            last_frame_id=latest.frame_id,
            first_seen_ms=sorted_obs[0].capture_timestamp_ms,
            last_seen_ms=latest.capture_timestamp_ms,
        )


@dataclass
class SourceEstimate:
    """
    Retained source-level estimate for a single drone on a global object.
    Used for Covariance Intersection.
    """
    drone_id: str
    drone_session_id: str
    session_generation: int
    local_track_id: str
    east_m: float
    north_m: float
    cov_xx: float
    cov_yy: float
    cov_xy: float
    confidence: float
    last_seen_ms: int
    observation_count: int


@dataclass
class GlobalObject:
    global_id: str
    label: str
    status: str  # 'ACTIVE', 'RETIRED', 'ARCHIVED'
    lat: float
    lon: float
    alt: float
    east_m: float
    north_m: float
    cov_xx: float
    cov_yy: float
    cov_xy: float
    confidence: float
    first_seen_ms: int
    last_seen_ms: int
    observation_count: int = 1
    source_count: int = 1
    source_estimates: Dict[str, SourceEstimate] = field(default_factory=dict)
    version: int = 1
    fusion_method: str = "single_estimate"
    lifecycle_state: str = LifecycleState.CONFIRMED.value  # 'PROVISIONAL', 'CONFIRMED', 'REJECTED', 'RETIRED'
    confirmation_method: Optional[str] = None
    provisional_reason: Optional[str] = None
    expires_at_ms: Optional[int] = None


@dataclass
class GlobalTrackIdentityRecord:
    drone_id: str
    drone_session_id: str
    session_generation: int
    local_track_id: str
    global_id: str
    first_frame_id: int
    last_frame_id: int
    first_seen_ms: int
    last_seen_ms: int


@dataclass
class InboxPacket:
    packet_id: str
    drone_id: str
    drone_session_id: str
    session_generation: int
    received_at_ms: int
    item_count: int
    packet_status: str  # 'RECEIVED', 'PROCESSING', 'COMPLETED', 'FAILED'
    claim_token: Optional[str] = None
    claim_expires_at_ms: Optional[int] = None


@dataclass
class InboxItem:
    packet_id: str
    item_index: int
    obs_id: str
    payload_hash: str
    raw_payload: str
    disposition: str  # 'PENDING', 'FUSED', 'DEFERRED', 'REJECTED', 'EXPIRED'
    rejection_reason: Optional[str] = None
    target_global_id: Optional[str] = None
    disposition_timestamp_ms: Optional[int] = None


@dataclass
class OutboxEvent:
    id: str
    payload: str
    status: str  # 'PENDING', 'LEASED', 'SENT', 'FAILED', 'ACKED'
    lease_token: Optional[str] = None
    lease_expires_at_ms: Optional[int] = None
    delivery_attempt_id: Optional[str] = None
    delivery_attempt_count: int = 0
    sent_at_ms: Optional[int] = None
    ack_timeout_ms: Optional[int] = None
    ack_client_id: Optional[str] = None
    ack_id: Optional[str] = None
    acked_at_ms: Optional[int] = None


@dataclass
class AuthenticatedAck:
    event_id: str
    delivery_attempt_id: str
    ack_id: str
    gcs_client_id: str
    timestamp_ms: int
    kid: str
    signature: str

