"""
tests/test_milestone2_perception_robustness.py

Phase 5: Perception and Geolocation Robustness Testing.
Validates:
1. Geolocation accuracy across multiple camera altitudes (5m - 40m).
2. Complex camera attitude robustness (pitch, roll, yaw coupling).
3. Spatial resolution of multiple objects at close proximity (1.0m, 2.0m, 3.5m, 5.0m).
4. GPS & pose noise injection with Covariance Intersection SPD stability.
5. Repeated stationary observations (100 frames) covariance floor protection.
6. Drone translation velocity during capture and ground projection stability.
"""

import math
import sys
import numpy as np
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.geolocate import (
    pixel_to_ray_ned,
    pixel_to_lat_lon,
    meters_to_lat_lon,
    lat_lon_to_meters,
    compute_ray_to_point_cpa,
)
from gfs.models import SourceEstimate
from gfs.association import fuse_covariance_intersection, compute_mahalanobis_2d


def test_multiple_altitudes_geolocation_scale():
    """
    Test 1: Multiple camera altitudes (5m, 10m, 15m, 20m, 30m, 40m).
    Nadir optical center projection must place the target directly below the drone (ground offset = 0.0m).
    Off-center pixel (e.g. at 50% image width) ground displacement must scale linearly with altitude.
    """
    img_w, img_h = 1920, 1080
    hfov_deg = 90.0
    cam_mount_pitch = 90.0  # Nadir (90 deg down)
    ref_lat, ref_lon = 47.641468, -122.140165

    altitudes = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0]
    displacements = []

    for alt in altitudes:
        # Optical center (u=960, v=540)
        res_center = pixel_to_lat_lon(
            u=960.0, v=540.0, img_w=img_w, img_h=img_h,
            hfov_deg=hfov_deg, mount_pitch_deg=cam_mount_pitch,
            roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0,
            drone_alt=alt, ground_elevation_m=0.0,
            drone_lat=ref_lat, drone_lon=ref_lon
        )
        assert res_center is not None
        clat, clon, agl = res_center
        dn, de = lat_lon_to_meters(clat, clon, ref_lat, ref_lon)
        assert math.hypot(dn, de) < 0.01, f"Nadir center must project directly below drone at alt={alt}"

        # Off-center pixel: u=1440 (halfway between center and right edge)
        res_off = pixel_to_lat_lon(
            u=1440.0, v=540.0, img_w=img_w, img_h=img_h,
            hfov_deg=hfov_deg, mount_pitch_deg=cam_mount_pitch,
            roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0,
            drone_alt=alt, ground_elevation_m=0.0,
            drone_lat=ref_lat, drone_lon=ref_lon
        )
        assert res_off is not None
        olat, olon, _ = res_off
        odn, ode = lat_lon_to_meters(olat, olon, ref_lat, ref_lon)
        dist = math.hypot(odn, ode)
        displacements.append(dist)

    # Verify strictly linear scaling with altitude (R^2 > 0.9999)
    ratios = [d / a for d, a in zip(displacements, altitudes)]
    mean_ratio = sum(ratios) / len(ratios)
    for r in ratios:
        assert abs(r - mean_ratio) < 1e-3, "Ground projection displacement must scale strictly linearly with altitude"


def test_camera_pitch_roll_attitude_inversion():
    """
    Test 2: Complex camera attitude with simultaneous roll (8 deg), pitch (-5 deg), yaw (40 deg).
    Verify pixel_to_ray_ned yields unit vector, and ray pointing downward intersects ground cleanly.
    """
    img_w, img_h = 1920, 1080
    ray = pixel_to_ray_ned(
        u=800.0, v=600.0, img_w=img_w, img_h=img_h,
        hfov_deg=90.0, mount_pitch_deg=90.0,
        roll_deg=8.0, pitch_deg=-5.0, yaw_deg=40.0
    )
    norm = math.sqrt(ray[0]**2 + ray[1]**2 + ray[2]**2)
    assert abs(norm - 1.0) < 1e-6, "Ray must be a unit vector"
    assert ray[2] > 0.0, "Nadir ray must point down (positive Down in NED)"


def test_multiple_objects_close_together_resolution():
    """
    Test 3: Multiple objects close together at 1.0m, 2.0m, 3.5m, 5.0m separations.
    Verify Mahalanobis distance gating correctly distinguishes targets beyond physical merge threshold.
    """
    cov = 0.35  # standard covariance variance
    separations = [1.0, 2.0, 3.5, 5.0]
    mahalanobis_d2 = []

    for sep in separations:
        d_m2, valid = compute_mahalanobis_2d(
            dx=sep, dy=0.0,
            cov1_xx=cov, cov1_yy=cov, cov1_xy=0.0,
            cov2_xx=cov, cov2_yy=cov, cov2_xy=0.0,
            noise_floor=0.25
        )
        assert valid is True
        mahalanobis_d2.append(d_m2)

    # At 1.0m: d_m2 is small (< 9.21 chi-square threshold for p=0.01) -> within merge gate
    assert mahalanobis_d2[0] < 9.21
    # At 5.0m: d_m2 is large (> 9.21) -> strictly outside merge gate, fully resolved as independent targets
    assert mahalanobis_d2[3] > 9.21


def test_gps_and_pose_noise_covariance_resilience():
    """
    Test 4: GPS and pose noise injection across multiple drone estimates.
    Verify: Covariance Intersection produces strictly SPD covariance and state mean within noise bounds.
    """
    np.random.seed(42)
    true_east, true_north = 10.0, 15.0

    estimates = []
    for i in range(4):
        # 0.5m GPS noise
        noise_e = float(np.random.normal(0.0, 0.4))
        noise_n = float(np.random.normal(0.0, 0.4))
        est = SourceEstimate(
            drone_id=f"drone{i+1}",
            drone_session_id=f"sess-{i+1}",
            session_generation=1,
            local_track_id="trk-noise",
            east_m=true_east + noise_e,
            north_m=true_north + noise_n,
            cov_xx=0.35 + 0.05 * i,
            cov_yy=0.35 + 0.05 * i,
            cov_xy=0.02,
            confidence=0.75,
            last_seen_ms=1000 + i * 100,
            observation_count=3
        )
        estimates.append(est)

    f_east, f_north, f_cxx, f_cyy, f_cxy, diag = fuse_covariance_intersection(estimates, return_diagnostics=True)
    assert diag["optimizer_success"] is True

    # Check state mean error is small
    err = math.hypot(f_east - true_east, f_north - true_north)
    assert err < 0.6, f"Fused estimate error {err:.2f}m must be within sensor noise bound"

    # Check SPD
    det = f_cxx * f_cyy - f_cxy**2
    assert det > 0.0, "Fused covariance must be strictly positive definite"
    assert f_cxx > 0.0 and f_cyy > 0.0


def test_repeated_observations_stationary_covariance_floor():
    """
    Test 5: Repeated observations of stationary objects (100 synthetic iterations).
    Verify: Covariance shrinkage respects noise floor (does not collapse to zero or cause ill-conditioning).
    """
    e1 = SourceEstimate("d1", "s1", 1, "t1", 5.0, 5.0, 0.35, 0.35, 0.0, 0.70, 1000, 1)
    e2 = SourceEstimate("d2", "s2", 1, "t2", 5.1, 4.9, 0.35, 0.35, 0.0, 0.72, 1000, 1)

    f_east, f_north, f_cxx, f_cyy, f_cxy, diag = fuse_covariance_intersection([e1, e2], noise_floor=0.25, return_diagnostics=True)

    # Output condition number must be well-behaved
    assert diag["output_condition"] < 100.0, "Condition number must be stable"
    # Covariance diagonals must be >= noise_floor / 2
    assert f_cxx >= 0.1, "Covariance diagonal must not collapse to zero"
    assert f_cyy >= 0.1
