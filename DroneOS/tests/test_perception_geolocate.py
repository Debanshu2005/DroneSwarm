"""Unit tests for camera pinhole geolocation and WGS84 projection."""

import math
import pytest

from DroneOS.perception.geolocate import (
    lat_lon_to_meters,
    meters_to_lat_lon,
    pixel_to_lat_lon,
    project_pixel_to_ned,
    wgs84_meters_per_degree,
)


def test_nadir_level_heading0_center_pixel_zero_offset():
    """Nadir/level/heading 0 center pixel -> zero offset."""
    W, H = 1280, 720
    cx, cy = W / 2.0, H / 2.0
    res = project_pixel_to_ned(
        u=cx,
        v=cy,
        img_w=W,
        img_h=H,
        hfov_deg=90.0,
        mount_pitch_deg=90.0,
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        height_agl=10.0,
    )
    assert res is not None
    dn, de = res
    assert abs(dn) < 1e-6
    assert abs(de) < 1e-6


def test_pixel_right_of_center_offsets():
    """Pixel right of center by x_n=0.5 at h=10 -> 5 m east at heading 0, 5 m south at heading 90."""
    W, H = 1280, 720
    cx, cy = W / 2.0, H / 2.0
    fx = (W / 2.0) / math.tan(math.radians(90.0) / 2.0)
    u_right = cx + 0.5 * fx

    # Heading 0 -> 5 m East
    res_h0 = project_pixel_to_ned(
        u=u_right,
        v=cy,
        img_w=W,
        img_h=H,
        hfov_deg=90.0,
        mount_pitch_deg=90.0,
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        height_agl=10.0,
    )
    assert res_h0 is not None
    dn0, de0 = res_h0
    assert abs(dn0) < 1e-6
    assert pytest.approx(de0, abs=1e-5) == 5.0

    # Heading 90 -> 5 m South (dn = -5, de = 0)
    res_h90 = project_pixel_to_ned(
        u=u_right,
        v=cy,
        img_w=W,
        img_h=H,
        hfov_deg=90.0,
        mount_pitch_deg=90.0,
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=90.0,
        height_agl=10.0,
    )
    assert res_h90 is not None
    dn90, de90 = res_h90
    assert pytest.approx(dn90, abs=1e-5) == -5.0
    assert abs(de90) < 1e-6


def test_pixel_above_center_north_at_heading0():
    """Pixel above center -> north at heading 0."""
    W, H = 1280, 720
    cx, cy = W / 2.0, H / 2.0
    fy = (W / 2.0) / math.tan(math.radians(90.0) / 2.0)
    v_above = cy - 0.4 * fy

    res = project_pixel_to_ned(
        u=cx,
        v=v_above,
        img_w=W,
        img_h=H,
        hfov_deg=90.0,
        mount_pitch_deg=90.0,
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        height_agl=10.0,
    )
    assert res is not None
    dn, de = res
    assert dn > 0.0
    assert pytest.approx(dn, abs=1e-5) == 4.0
    assert abs(de) < 1e-6


def test_forward_camera_pitched_45deg_down():
    """Forward camera pitched 45 deg down, center pixel, h=10 -> 10 m ahead."""
    W, H = 1280, 720
    cx, cy = W / 2.0, H / 2.0

    res = project_pixel_to_ned(
        u=cx,
        v=cy,
        img_w=W,
        img_h=H,
        hfov_deg=90.0,
        mount_pitch_deg=45.0,
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        height_agl=10.0,
    )
    assert res is not None
    dn, de = res
    assert pytest.approx(dn, abs=1e-5) == 10.0
    assert abs(de) < 1e-6


def test_ray_at_or_above_horizon_rejected():
    """Ray pointing horizontal or upwards must return None."""
    W, H = 1280, 720
    cx, cy = W / 2.0, H / 2.0

    # Forward-looking camera (pitch 0), center pixel points to horizon (d_down = 0)
    res_horizon = project_pixel_to_ned(
        u=cx,
        v=cy,
        img_w=W,
        img_h=H,
        hfov_deg=90.0,
        mount_pitch_deg=0.0,
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        height_agl=10.0,
    )
    assert res_horizon is None


def test_wgs84_round_trip():
    """Round-trip metres <-> degrees at latitude 22.3 (Kharagpur) and latitude 0.0."""
    for lat in [0.0, 22.3]:
        base_lon = 87.3
        dn_true = 35.5
        de_true = -62.4

        target_lat, target_lon = meters_to_lat_lon(dn_true, de_true, lat, base_lon)
        dn_calc, de_calc = lat_lon_to_meters(target_lat, target_lon, lat, base_lon)

        assert pytest.approx(dn_calc, abs=1e-5) == dn_true
        assert pytest.approx(de_calc, abs=1e-5) == de_true

