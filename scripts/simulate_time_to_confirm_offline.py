"""
Synthetic Offline Time-to-Confirm Monte Carlo Simulation (PC only, no AirSim).
Drives the real DroneOS TrackAggregator across straight survey passes over a ground target
under varying frame rates (max_fps: 1, 2, 3, 4) and detection probabilities
(p=0.45 for conf 0.55 recall, p=0.60 for conf 0.35 recall) with realistic GPS jitter.

All results from this script are labeled: 'synthetic'.
"""

import math
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from DroneOS.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS.perception.geolocate import meters_to_lat_lon

BASE_LAT = 47.641468
BASE_LON = -122.140165
VELOCITY_M_S = 1.0
ALTITUDE_M = 5.0
CAMERA_HFOV_DEG = 90.0
GPS_SIGMA_M = 0.5
TRIALS_PER_CONDITION = 2000


def simulate_single_pass(
    max_fps: float,
    p_detect: float,
    pass_length_m: float,
    gps_sigma: float = GPS_SIGMA_M,
    drone_speed: float = VELOCITY_M_S,
    alt_m: float = ALTITUDE_M,
) -> Tuple[bool, float, float, int]:
    """
    Run one single pass over a stationary ground target at (0, 0).

    Returns:
        (confirmed, time_from_first_hit_s, time_from_pass_start_s, frames_to_confirm)
    """
    aggregator = TrackAggregator(
        drone_id="drone1",
        merge_radius_m=2.5,
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        gps_sigma_m=gps_sigma,
    )

    dt = 1.0 / max_fps
    half_dist = pass_length_m / 2.0
    total_time_s = pass_length_m / drone_speed
    total_frames = int(math.ceil(total_time_s / dt)) + 1

    first_hit_time: float = -1.0
    time_from_first_hit = 0.0
    time_from_pass_start = 0.0
    frames_count = 0

    for frame_idx in range(total_frames):
        t_now = frame_idx * dt
        # True drone position moving from -half_dist to +half_dist
        x_true = -half_dist + (drone_speed * t_now)
        y_true = 0.0

        # Check if target at (0, 0) is within camera ground footprint
        # Downward nadir camera with 90 deg FOV at altitude H has ground radius H * tan(45) = H
        footprint_radius = alt_m * math.tan(math.radians(CAMERA_HFOV_DEG / 2.0))
        dist_to_target = math.hypot(x_true, y_true)
        in_view = dist_to_target <= footprint_radius

        detections: List[GeoreferencedDetection] = []

        if in_view and (random.random() < p_detect):
            # Detection occurred! Apply GPS jitter to both drone position and target georeferencing
            dx_drone = random.gauss(0.0, gps_sigma)
            dy_drone = random.gauss(0.0, gps_sigma)
            x_drone_meas = x_true + dx_drone
            y_drone_meas = y_true + dy_drone

            # Pinhole projection inherits drone GPS error plus ray/pixel projection noise
            dx_geo = random.gauss(0.0, gps_sigma)
            dy_geo = random.gauss(0.0, gps_sigma)
            x_target_meas = 0.0 + dx_geo
            y_target_meas = 0.0 + dy_geo

            drone_lat, drone_lon = meters_to_lat_lon(x_drone_meas, y_drone_meas, BASE_LAT, BASE_LON)
            det_lat, det_lon = meters_to_lat_lon(x_target_meas, y_target_meas, BASE_LAT, BASE_LON)

            det = GeoreferencedDetection(
                label="rubbish",
                confidence=0.75,
                lat=det_lat,
                lon=det_lon,
                altitude_agl=alt_m,
                drone_lat=drone_lat,
                drone_lon=drone_lon,
            )
            detections.append(det)

            if first_hit_time < 0.0:
                first_hit_time = t_now

        # Update real TrackAggregator
        aggregator.update(detections, now=t_now, frame_id=frame_idx)

        # Check confirmation status
        for track in aggregator.tracks.values():
            if track.confirmed:
                time_from_first_hit = track.seconds_to_confirm
                time_from_pass_start = t_now
                frames_count = track.frames_to_confirm
                return (True, time_from_first_hit, time_from_pass_start, frames_count)

    return (False, 0.0, 0.0, 0)


def run_experiment(
    pass_length_m: float,
    trials: int = TRIALS_PER_CONDITION,
) -> List[Dict[str, Any]]:
    fps_options = [1.0, 2.0, 3.0, 4.0]
    p_options = [
        (0.45, "0.45 (conf 0.55 recall)"),
        (0.60, "0.60 (conf 0.35 recall)"),
    ]

    results = []

    for p_val, p_label in p_options:
        for fps_val in fps_options:
            times_first_hit: List[float] = []
            times_pass_start: List[float] = []
            confirmed_count = 0

            for _ in range(trials):
                ok, t_hit, t_pass, _ = simulate_single_pass(
                    max_fps=fps_val,
                    p_detect=p_val,
                    pass_length_m=pass_length_m,
                )
                if ok:
                    confirmed_count += 1
                    times_first_hit.append(t_hit)
                    times_pass_start.append(t_pass)

            miss_count = trials - confirmed_count
            miss_rate_pct = round((miss_count / trials) * 100.0, 2)

            if times_first_hit:
                arr_hit = np.array(times_first_hit)
                arr_pass = np.array(times_pass_start)
                med_hit = round(float(np.percentile(arr_hit, 50)), 2)
                p90_hit = round(float(np.percentile(arr_hit, 90)), 2)
                mean_hit = round(float(np.mean(arr_hit)), 2)

                med_pass = round(float(np.percentile(arr_pass, 50)), 2)
                p90_pass = round(float(np.percentile(arr_pass, 90)), 2)
                mean_pass = round(float(np.mean(arr_pass)), 2)
            else:
                med_hit, p90_hit, mean_hit = 0.0, 0.0, 0.0
                med_pass, p90_pass, mean_pass = 0.0, 0.0, 0.0

            results.append({
                "p_detect": p_val,
                "p_label": p_label,
                "max_fps": fps_val,
                "trials": trials,
                "confirmed_count": confirmed_count,
                "miss_count": miss_count,
                "miss_rate_pct": miss_rate_pct,
                # Time from first hit (track.seconds_to_confirm)
                "ttc_from_first_hit_median_s": med_hit,
                "ttc_from_first_hit_p90_s": p90_hit,
                "ttc_from_first_hit_mean_s": mean_hit,
                # Time from pass start (when target enters camera FOV)
                "ttc_from_pass_start_median_s": med_pass,
                "ttc_from_pass_start_p90_s": p90_pass,
                "ttc_from_pass_start_mean_s": mean_pass,
            })

    return results


def print_table(results: List[Dict[str, Any]], pass_title: str):
    print("\n" + "=" * 105)
    print(f" {pass_title} [SYNTHETIC, PC ONLY, NO AIRSIM]")
    print(f" Parameters: Speed=1.0 m/s, GPS sigma=0.5 m, Altitude=5.0 m, Min Hits=2, Trials={TRIALS_PER_CONDITION}/cell")
    print("=" * 105)
    print(f"{'Detection Probability (p)':<26} | {'FPS':<4} | {'Miss Rate':<10} | {'TTC (from 1st hit) med/p90':<28} | {'TTC (from FOV entry) med/p90'}")
    print("-" * 105)
    for r in results:
        hit_str = f"med: {r['ttc_from_first_hit_median_s']:<4.2f}s | p90: {r['ttc_from_first_hit_p90_s']:<4.2f}s"
        pass_str = f"med: {r['ttc_from_pass_start_median_s']:<4.2f}s | p90: {r['ttc_from_pass_start_p90_s']:<4.2f}s"
        print(f"{r['p_label']:<26} | {int(r['max_fps']):<4} | {r['miss_rate_pct']:<5.1f}%     | {hit_str:<28} | {pass_str}")
    print("=" * 105)


def main():
    random.seed(42)
    np.random.seed(42)

    # 1. Full camera FOV pass (10m ground footprint at 5m AGL with 90 deg FOV = 10s pass at 1 m/s)
    print("Running Monte Carlo simulation for 10.0m Full-FOV Pass (10.0s window)...")
    res_10m = run_experiment(pass_length_m=10.0)
    print_table(res_10m, "OFFLINE TIME-TO-CONFIRM SIMULATION: 10.0m FULL-FOV PASS (10s in view)")

    # 2. Narrow pass (6.0m pass = 6.0s window, matching AirSim validation flight from -3m to +3m)
    print("\nRunning Monte Carlo simulation for 6.0m Narrow Pass (6.0s window)...")
    res_6m = run_experiment(pass_length_m=6.0)
    print_table(res_6m, "OFFLINE TIME-TO-CONFIRM SIMULATION: 6.0m NARROW PASS (6s in view)")


if __name__ == "__main__":
    main()
