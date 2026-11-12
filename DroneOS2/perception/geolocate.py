"""Pinhole camera geolocation projecting pixel coordinates to geographic coordinates."""

import math
from typing import Optional, Tuple


def _rot_x(rad: float):
    c, s = math.cos(rad), math.sin(rad)
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def _rot_y(rad: float):
    c, s = math.cos(rad), math.sin(rad)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def _rot_z(rad: float):
    c, s = math.cos(rad), math.sin(rad)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def _mat_vec(M, v):
    return [sum(M[i][j] * v[j] for j in range(3)) for i in range(3)]


def _mat_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def wgs84_meters_per_degree(lat_deg: float) -> Tuple[float, float]:
    """
    Compute WGS84 meters per degree of latitude and longitude at given latitude.
    m_per_deg_lat = 111132.92 - 559.82*cos(2φ) + 1.175*cos(4φ)
    m_per_deg_lon = 111412.84*cos(φ) - 93.5*cos(3φ) + 0.118*cos(5φ)
    """
    phi = math.radians(lat_deg)
    m_lat = 111132.92 - 559.82 * math.cos(2.0 * phi) + 1.175 * math.cos(4.0 * phi)
    m_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3.0 * phi) + 0.118 * math.cos(5.0 * phi)
    return m_lat, m_lon


def meters_to_lat_lon(dn: float, de: float, base_lat: float, base_lon: float) -> Tuple[float, float]:
    """Convert relative NED (North, East) offsets in meters to (latitude, longitude)."""
    m_lat, m_lon = wgs84_meters_per_degree(base_lat)
    target_lat = base_lat + (dn / m_lat)
    target_lon = base_lon + (de / m_lon)
    return target_lat, target_lon


def lat_lon_to_meters(lat: float, lon: float, base_lat: float, base_lon: float) -> Tuple[float, float]:
    """Convert (latitude, longitude) to relative NED (North, East) offsets in meters from base."""
    m_lat, m_lon = wgs84_meters_per_degree(base_lat)
    dn = (lat - base_lat) * m_lat
    de = (lon - base_lon) * m_lon
    return dn, de


def project_pixel_to_ned(
    u: float,
    v: float,
    img_w: int,
    img_h: int,
    hfov_deg: float,
    mount_pitch_deg: float,
    roll_deg: float,
    pitch_deg: float,
    yaw_deg: float,
    height_agl: float,
    max_range_m: float = 120.0,
) -> Optional[Tuple[float, float]]:
    """
    Project image pixel coordinates (u, v) onto flat ground under the drone.
    Returns (north_m, east_m) relative to drone position, or None if rejected.
    """
    if height_agl <= 0.0 or img_w <= 0 or img_h <= 0:
        return None

    # Camera model: pinhole
    cx = img_w / 2.0
    cy = img_h / 2.0
    fx = (img_w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    fy = fx

    # Ray in camera frame (x right, y down, z forward)
    x_cam = (u - cx) / fx
    y_cam = (v - cy) / fy
    z_cam = 1.0

    # Camera->body (FRD) for forward-looking camera: body = [cam_z, cam_x, cam_y]
    v_body0 = [z_cam, x_cam, y_cam]

    # Pitch down by mount_pitch via Ry(-mount_pitch) (90 = nadir)
    Ry_mount = _rot_y(math.radians(-mount_pitch_deg))
    v_body = _mat_vec(Ry_mount, v_body0)

    # Body->NED: Rz(yaw) * Ry(pitch) * Rx(roll)
    Rz = _rot_z(math.radians(yaw_deg))
    Ry = _rot_y(math.radians(pitch_deg))
    Rx = _rot_x(math.radians(roll_deg))
    R_body_to_ned = _mat_mul(Rz, _mat_mul(Ry, Rx))

    d_ned = _mat_vec(R_body_to_ned, v_body)
    d_n, d_e, d_down = d_ned

    # Reject if ray is at/above horizon or looking upward
    if d_down < 0.05:
        return None

    # Intersect with ground: t = h / d_down
    t = height_agl / d_down
    north_m = t * d_n
    east_m = t * d_e

    # Reject if horizontal range exceeds maximum
    horiz_range = math.hypot(north_m, east_m)
    if horiz_range > max_range_m:
        return None

    return north_m, east_m


def pixel_to_lat_lon(
    u: float,
    v: float,
    img_w: int,
    img_h: int,
    hfov_deg: float,
    mount_pitch_deg: float,
    roll_deg: float,
    pitch_deg: float,
    yaw_deg: float,
    drone_alt: float,
    ground_elevation_m: float,
    drone_lat: float,
    drone_lon: float,
    max_range_m: float = 120.0,
) -> Optional[Tuple[float, float, float]]:
    """
    Project pixel (u, v) to ground (latitude, longitude, altitude_agl).
    Returns (lat, lon, height_agl) or None if ray does not intersect ground within max_range.
    """
    height_agl = drone_alt - ground_elevation_m
    ned_offset = project_pixel_to_ned(
        u=u,
        v=v,
        img_w=img_w,
        img_h=img_h,
        hfov_deg=hfov_deg,
        mount_pitch_deg=mount_pitch_deg,
        roll_deg=roll_deg,
        pitch_deg=pitch_deg,
        yaw_deg=yaw_deg,
        height_agl=height_agl,
        max_range_m=max_range_m,
    )
    if ned_offset is None:
        return None

    dn, de = ned_offset
    lat, lon = meters_to_lat_lon(dn, de, drone_lat, drone_lon)
    return lat, lon, height_agl

