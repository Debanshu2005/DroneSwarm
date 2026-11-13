"""
AirSim Single-Drone Ground-Truth Geolocation Validation & Sensitivity Benchmark.

Simulates camera-to-ground projection for controlled ground targets and evaluates:
1. Horizontal position error (metres) across camera frame regions (center, edges, corners).
2. Error variations across drone altitudes (5m, 10m, 15m, 20m, 30m).
3. Error variations across drone yaw orientations (0, 45, 90, 180, 270 deg).
4. Error variations across roll/pitch tilts (0 to 18 deg).
5. Error sensitivity to altitude, attitude, and pixel quantization noise.
6. Execution of YOLO garbage detector on simulated frames with known targets.
"""

import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Dict, List, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from DroneOS.perception.geolocate import (
    lat_lon_to_meters,
    meters_to_lat_lon,
    pixel_to_lat_lon,
    project_pixel_to_ned,
    wgs84_meters_per_degree,
)
from DroneOS.perception.types import Detection, Frame
from DroneOS.perception.detector import GarbageDetector


def ground_to_pixel(
    target_n: float,
    target_e: float,
    drone_n: float,
    drone_e: float,
    height_agl: float,
    roll_deg: float,
    pitch_deg: float,
    yaw_deg: float,
    mount_pitch_deg: float = 90.0,
    hfov_deg: float = 90.0,
    img_w: int = 1280,
    img_h: int = 720,
) -> Tuple[float, float, bool]:
    """
    Project ground target (target_n, target_e, z=0) into image pixel coordinates (u, v).
    Returns (u, v, in_frame_bool).
    """
    # Relative target vector in NED
    d_ned = [target_n - drone_n, target_e - drone_e, height_agl]

    # NED to Body: R_ned_to_body = R_body_to_ned^T
    # R_body_to_ned = Rz(yaw) * Ry(pitch) * Rx(roll)
    phi = math.radians(roll_deg)
    theta = math.radians(pitch_deg)
    psi = math.radians(yaw_deg)

    c_p, s_p = math.cos(phi), math.sin(phi)
    c_t, s_t = math.cos(theta), math.sin(theta)
    c_ps, s_ps = math.cos(psi), math.sin(psi)

    # Rotation matrix Body to NED
    R11 = c_ps * c_t
    R12 = c_ps * s_t * s_p - s_ps * c_p
    R13 = c_ps * s_t * c_p + s_ps * s_p

    R21 = s_ps * c_t
    R22 = s_ps * s_t * s_p + c_ps * c_p
    R23 = s_ps * s_t * c_p - c_ps * s_p

    R31 = -s_t
    R32 = c_t * s_p
    R33 = c_t * c_p

    # Multiply by transpose to transform NED vector to body frame
    v_body_x = R11 * d_ned[0] + R21 * d_ned[1] + R31 * d_ned[2]
    v_body_y = R12 * d_ned[0] + R22 * d_ned[1] + R32 * d_ned[2]
    v_body_z = R13 * d_ned[0] + R23 * d_ned[1] + R33 * d_ned[2]

    # Body to Camera: inverse of Ry(-mount_pitch)
    # Ry(-90) rotates camera to body. Inverting is Ry(+mount_pitch):
    m_rad = math.radians(mount_pitch_deg)
    c_m, s_m = math.cos(m_rad), math.sin(m_rad)

    # v_cam0 = Ry(mount_pitch) * v_body
    # Ry(m) = [[cos m, 0, sin m], [0, 1, 0], [-sin m, 0, cos m]]
    v_cam0_x = c_m * v_body_x + s_m * v_body_z
    v_cam0_y = v_body_y
    v_cam0_z = -s_m * v_body_x + c_m * v_body_z

    # Camera frame: x right = v_cam0_y, y down = v_cam0_z, z forward = v_cam0_x
    cam_x = v_cam0_y
    cam_y = v_cam0_z
    cam_z = v_cam0_x

    if cam_z <= 0.05:
        return -1.0, -1.0, False

    cx = img_w / 2.0
    cy = img_h / 2.0
    fx = (img_w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    fy = fx

    u = cx + fx * (cam_x / cam_z)
    v = cy + fy * (cam_y / cam_z)

    in_frame = (0 <= u < img_w) and (0 <= v < img_h)
    return u, v, in_frame


def run_benchmark():
    print("=" * 78)
    print("AirSim Single-Drone Ground-Truth Geolocation Accuracy Benchmark")
    print("=" * 78)

    base_lat = 22.3149000
    base_lon = 87.3105000
    drone_n = 0.0
    drone_e = 0.0

    # Ground-truth targets on the flat ground
    # (id, name, target_n, target_e, frame_region)
    targets = [
        ("T01", "Nadir Center", 0.0, 0.0, "Center"),
        ("T02", "Mid Left", 0.0, -3.5, "Left"),
        ("T03", "Mid Right", 0.0, 3.5, "Right"),
        ("T04", "Mid Top (Ahead)", 2.5, 0.0, "Top"),
        ("T05", "Mid Bottom (Aft)", -2.5, 0.0, "Bottom"),
        ("T06", "Top-Left", 2.5, -3.5, "Quadrant Top-Left"),
        ("T07", "Top-Right", 2.5, 3.5, "Quadrant Top-Right"),
        ("T08", "Bottom-Left", -2.5, -3.5, "Quadrant Bottom-Left"),
        ("T09", "Bottom-Right", -2.5, 3.5, "Quadrant Bottom-Right"),
        ("T10", "Boundary Near Left", 0.0, -7.0, "Boundary Left"),
        ("T11", "Boundary Near Right", 0.0, 7.0, "Boundary Right"),
        ("T12", "Boundary Near Top", 4.5, 0.0, "Boundary Top"),
        ("T13", "Boundary Near Bottom", -4.5, 0.0, "Boundary Bottom"),
    ]

    # Test parameter variations
    altitudes = [5.0, 10.0, 15.0, 20.0, 30.0]
    yaws = [0.0, 45.0, 90.0, 180.0, 270.0]
    tilts = [
        (0.0, 0.0, "Level"),
        (2.0, 3.0, "Gentle Tilt"),
        (-4.0, 5.0, "Moderate Tilt"),
        (8.0, -10.0, "Dynamic Maneuver"),
        (15.0, 12.0, "Near Maximum Tilt Gate"),
    ]

    all_records = []
    region_errors: Dict[str, List[float]] = {}
    altitude_errors: Dict[float, List[float]] = {alt: [] for alt in altitudes}
    yaw_errors: Dict[float, List[float]] = {yaw: [] for yaw in yaws}
    tilt_errors: Dict[str, List[float]] = {desc: [] for _, _, desc in tilts}

    total_projections = 0
    in_view_projections = 0

    # 1. Systematic Matrix Evaluation
    for alt in altitudes:
        for yaw in yaws:
            for roll, pitch, tilt_desc in tilts:
                for tid, tname, tn, te, region in targets:
                    total_projections += 1
                    u, v, in_frame = ground_to_pixel(
                        target_n=tn,
                        target_e=te,
                        drone_n=drone_n,
                        drone_e=drone_e,
                        height_agl=alt,
                        roll_deg=roll,
                        pitch_deg=pitch,
                        yaw_deg=yaw,
                        mount_pitch_deg=90.0,
                        hfov_deg=90.0,
                        img_w=1280,
                        img_h=720,
                    )
                    if not in_frame:
                        continue

                    in_view_projections += 1

                    # Project pixel back to NED
                    proj_ned = project_pixel_to_ned(
                        u=u,
                        v=v,
                        img_w=1280,
                        img_h=720,
                        hfov_deg=90.0,
                        mount_pitch_deg=90.0,
                        roll_deg=roll,
                        pitch_deg=pitch,
                        yaw_deg=yaw,
                        height_agl=alt,
                        max_range_m=120.0,
                    )
                    assert proj_ned is not None, f"Projection failed for target {tid} at alt={alt}"

                    proj_n, proj_e = proj_ned
                    err_n = proj_n - tn
                    err_e = proj_e - te
                    horiz_err = math.hypot(err_n, err_e)

                    # Geodesic WGS84 roundtrip validation
                    t_lat, t_lon = meters_to_lat_lon(tn, te, base_lat, base_lon)
                    p_lat, p_lon, p_agl = pixel_to_lat_lon(
                        u=u,
                        v=v,
                        img_w=1280,
                        img_h=720,
                        hfov_deg=90.0,
                        mount_pitch_deg=90.0,
                        roll_deg=roll,
                        pitch_deg=pitch,
                        yaw_deg=yaw,
                        drone_alt=alt,
                        ground_elevation_m=0.0,
                        drone_lat=base_lat,
                        drone_lon=base_lon,
                        max_range_m=120.0,
                    )
                    calc_n, calc_e = lat_lon_to_meters(p_lat, p_lon, base_lat, base_lon)
                    geo_err = math.hypot(calc_n - tn, calc_e - te)

                    region_errors.setdefault(region, []).append(horiz_err)
                    altitude_errors[alt].append(horiz_err)
                    yaw_errors[yaw].append(horiz_err)
                    tilt_errors[tilt_desc].append(horiz_err)

                    all_records.append({
                        "target_id": tid,
                        "target_name": tname,
                        "region": region,
                        "target_ned": (tn, te),
                        "drone_alt": alt,
                        "drone_attitude": (roll, pitch, yaw),
                        "pixel_uv": (round(u, 2), round(v, 2)),
                        "proj_ned": (round(proj_n, 4), round(proj_e, 4)),
                        "horiz_error_m": horiz_err,
                        "geo_error_m": geo_err,
                    })

    # Overall Statistics
    all_errors = [r["horiz_error_m"] for r in all_records]
    mean_err = float(np.mean(all_errors))
    median_err = float(np.median(all_errors))
    max_err = float(np.max(all_errors))
    rmse_err = float(np.sqrt(np.mean(np.square(all_errors))))

    print(f"Total test cases evaluated: {total_projections}")
    print(f"Total in-FOV valid projections: {in_view_projections}")
    print(f"Mean Horizontal Error:   {mean_err * 1000:.4f} mm ({mean_err:.8f} m)")
    print(f"Median Horizontal Error: {median_err * 1000:.4f} mm ({median_err:.8f} m)")
    print(f"Maximum Horizontal Error:{max_err * 1000:.4f} mm ({max_err:.8f} m)")
    print(f"RMSE Horizontal Error:   {rmse_err * 1000:.4f} mm ({rmse_err:.8f} m)")

    # 2. Frame Region Error Breakdown
    print("\n--- Error Breakdown by Frame Region ---")
    region_stats = {}
    for region, errs in region_errors.items():
        r_mean = float(np.mean(errs))
        r_max = float(np.max(errs))
        r_rmse = float(np.sqrt(np.mean(np.square(errs))))
        region_stats[region] = {"count": len(errs), "mean_m": r_mean, "max_m": r_max, "rmse_m": r_rmse}
        print(f"  {region:<22} Count={len(errs):<4} Mean={r_mean*1000:.4f} mm, Max={r_max*1000:.4f} mm, RMSE={r_rmse*1000:.4f} mm")

    # 3. Altitude Error Breakdown
    print("\n--- Error Breakdown by Altitude ---")
    alt_stats = {}
    for alt, errs in altitude_errors.items():
        a_mean = float(np.mean(errs))
        a_max = float(np.max(errs))
        a_rmse = float(np.sqrt(np.mean(np.square(errs))))
        alt_stats[alt] = {"count": len(errs), "mean_m": a_mean, "max_m": a_max, "rmse_m": a_rmse}
        print(f"  Alt {alt:>4.1f}m: Count={len(errs):<4} Mean={a_mean*1000:.4f} mm, Max={a_max*1000:.4f} mm, RMSE={a_rmse*1000:.4f} mm")

    # 4. Sensor Noise Sensitivity Simulation
    print("\n--- Sensor Perturbation & Noise Sensitivity Analysis ---")
    noise_scenarios = [
        ("Ideal Sensor (Zero Noise)", 0.0, 0.0, 0.0, 0.0),
        ("Pixel Quantization (±1 px jitter)", 1.0, 0.0, 0.0, 0.0),
        ("Altitude Uncertainty (±0.25 m barometer noise)", 0.0, 0.25, 0.0, 0.0),
        ("IMU Tilt Noise (±0.5 deg pitch/roll)", 0.0, 0.0, 0.5, 0.0),
        ("Compass Heading Noise (±1.0 deg yaw)", 0.0, 0.0, 0.0, 1.0),
        ("Combined Realistic Sensor Suite", 1.0, 0.25, 0.5, 1.0),
    ]

    sensitivity_results = {}
    np.random.seed(42)

    for desc, px_sigma, alt_sigma, tilt_sigma, yaw_sigma in noise_scenarios:
        pert_errors = []
        for r in all_records[:300]:  # Sample representative subset
            u, v = r["pixel_uv"]
            tn, te = r["target_ned"]
            alt = r["drone_alt"]
            roll, pitch, yaw = r["drone_attitude"]

            # Add perturbation
            u_p = u + np.random.normal(0, px_sigma)
            v_p = v + np.random.normal(0, px_sigma)
            alt_p = alt + np.random.normal(0, alt_sigma)
            roll_p = roll + np.random.normal(0, tilt_sigma)
            pitch_p = pitch + np.random.normal(0, tilt_sigma)
            yaw_p = yaw + np.random.normal(0, yaw_sigma)

            proj = project_pixel_to_ned(
                u=u_p,
                v=v_p,
                img_w=1280,
                img_h=720,
                hfov_deg=90.0,
                mount_pitch_deg=90.0,
                roll_deg=roll_p,
                pitch_deg=pitch_p,
                yaw_deg=yaw_p,
                height_agl=alt_p,
            )
            if proj is not None:
                pn, pe = proj
                pert_errors.append(math.hypot(pn - tn, pe - te))

        p_mean = float(np.mean(pert_errors))
        p_median = float(np.median(pert_errors))
        p_max = float(np.max(pert_errors))
        p_rmse = float(np.sqrt(np.mean(np.square(pert_errors))))

        sensitivity_results[desc] = {
            "mean_m": p_mean,
            "median_m": p_median,
            "max_m": p_max,
            "rmse_m": p_rmse,
        }
        print(f"  {desc:<36} RMSE: {p_rmse:6.3f} m  (Mean: {p_mean:6.3f} m, Max: {p_max:6.3f} m)")

    # 5. Live YOLO Inference Verification on Synthetic AirSim Target Frame
    print("\n--- Live YOLO Inference Verification with models/garbage.pt ---")
    detector = GarbageDetector(weights_path="models/garbage.pt", confidence=0.35, iou=0.5)
    load_success = detector.load()
    print(f"GarbageDetector load result: {load_success}")

    # Generate synthetic 1280x720 simulated frame
    sim_frame = np.full((720, 1280, 3), 120, dtype=np.uint8)  # Neutral grey terrain

    # Render a realistic textured garbage item at optical center
    cv2_available = True
    try:
        import cv2
        # White plastic bottle / bag pattern
        cv2.rectangle(sim_frame, (620, 340), (660, 380), (240, 240, 245), -1)
        cv2.circle(sim_frame, (640, 360), 8, (200, 200, 210), -1)
    except ImportError:
        cv2_available = False

    t_start = time.perf_counter()
    import asyncio
    dets = asyncio.run(detector.detect(sim_frame))
    t_inference = (time.perf_counter() - t_start) * 1000.0
    print(f"YOLO inference executed in {t_inference:.2f} ms on 1280x720 frame.")
    print(f"Detections returned: {len(dets)}")
    detector.close()

    # Save summary output
    output_path = Path("ml/reports/airsim_geolocation_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "benchmark_timestamp": time.time(),
        "total_test_cases": total_projections,
        "valid_projections": in_view_projections,
        "ideal_geometry_metrics": {
            "mean_m": mean_err,
            "median_m": median_err,
            "max_m": max_err,
            "rmse_m": rmse_err,
        },
        "region_breakdown": region_stats,
        "altitude_breakdown": alt_stats,
        "sensor_noise_sensitivity": sensitivity_results,
        "sample_records": all_records[:20],
    }
    output_path.write_text(json.dumps(summary_data, indent=2))
    print(f"\nSaved benchmark results to {output_path}")
    print("=" * 78)


if __name__ == "__main__":
    run_benchmark()
