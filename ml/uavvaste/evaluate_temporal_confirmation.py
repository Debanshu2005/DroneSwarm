#!/usr/bin/env python3
"""
Evaluate multi-frame temporal confirmation configurations for DroneSwarm.

Evaluates 5 configurations across multi-frame sequence data:
- Baseline: conf = 0.35, min_hits = 1
- Config A: conf = 0.50, min_hits = 1
- Config B: conf = 0.55, min_hits = 2
- Config C: conf = 0.60, min_hits = 2
- Config D: conf = 0.60, min_hits = 3

Computes:
- Precision after confirmation
- Recall after confirmation
- F1 score
- Confirmation delay (frames / seconds at 1 FPS)
- False alerts per minute (at 1 FPS flight rate)
- Duplicate event rate
- Missed detections count

Exports results to CSV, JSON, and visual charts.
"""

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.aggregator import (
    GeoreferencedDetection,
    TrackAggregator,
)


CONFIGURATIONS = [
    {"name": "Baseline", "confidence": 0.35, "min_hits": 1},
    {"name": "Config A", "confidence": 0.50, "min_hits": 1},
    {"name": "Config B", "confidence": 0.55, "min_hits": 2},
    {"name": "Config C", "confidence": 0.60, "min_hits": 2},
    {"name": "Config D", "confidence": 0.60, "min_hits": 3},
]


def simulate_multi_frame_evaluation(
    configs: List[Dict] = CONFIGURATIONS,
    num_sequences: int = 25,
    frames_per_seq: int = 12,
    gt_objects_per_seq: int = 4,
    fp_rate_per_frame_base: float = 0.45,  # False positives per frame at conf=0.35
    fps: float = 1.0,
    seed: int = 42,
) -> List[Dict]:
    """
    Run Monte Carlo sequence simulation of drone flight over stationary ground targets
    and transient aerial / ground false positives.

    Models:
    - True targets remain stationary at known ground GPS coordinates.
    - True targets are detected with probability p_det(conf) determined from UAVVaste validation curve.
    - False positives occur transiently (mostly single-frame glints, shadows, peer drones) with rate lambda(conf).
    - Timestamp advances by 1.0/fps each step.
    - Enforces TrackAggregator update contract (duplicate timestamp rejection, TTL eviction, min_hits).
    """
    rng = np.random.RandomState(seed)

    # Empirical detection probability p(conf) and false positive rate lambda(conf) from UAVVaste val split
    # Val split: 616 GT instances, 116 images (~5.3 GT/image)
    # At conf=0.35: Recall=77.89%, FP=65 across 116 frames (0.56 FP/frame)
    # At conf=0.50: Recall=73.70%, FP=38 across 116 frames (0.33 FP/frame)
    # At conf=0.55: Recall=71.10%, FP=28 across 116 frames (0.24 FP/frame)
    # At conf=0.60: Recall=66.72%, FP=23 across 116 frames (0.20 FP/frame)
    empirical_p_det = {
        0.35: 0.7789,
        0.50: 0.7370,
        0.55: 0.7110,
        0.60: 0.6672,
    }
    empirical_fp_rate = {
        0.35: 0.560,
        0.50: 0.328,
        0.55: 0.241,
        0.60: 0.198,
    }

    results = []

    for cfg in configs:
        name = cfg["name"]
        conf_thresh = cfg["confidence"]
        min_hits = cfg["min_hits"]

        p_det = empirical_p_det.get(conf_thresh, 0.70)
        lam_fp = empirical_fp_rate.get(conf_thresh, 0.25)

        total_gt_targets = num_sequences * gt_objects_per_seq
        confirmed_true_targets = set()
        confirmed_false_alerts = 0
        total_broadcast_events = 0
        duplicate_events = 0
        confirmation_delays = []

        total_simulated_seconds = (num_sequences * frames_per_seq) / fps
        total_simulated_minutes = total_simulated_seconds / 60.0

        for seq_idx in range(num_sequences):
            aggregator = TrackAggregator(
                drone_id=f"drone-sim-{seq_idx:02d}",
                merge_radius_m=2.5,
                min_hits=min_hits,
                update_interval_s=10.0,
                max_tracks=2000,
                unconfirmed_ttl_s=30.0,
                min_temporal_separation_s=2.0,
                min_drone_displacement_m=1.5,
            )

            # Generate true ground targets for this sequence
            # Placed along a flight transect spaced > 5m apart to test spatial separation
            seq_gt_targets = {}
            base_lat = 40.7128 + (seq_idx * 0.005)
            base_lon = -74.0060 + (seq_idx * 0.005)

            for target_id in range(gt_objects_per_seq):
                # Target offset along north axis: 6m, 12m, 18m, 24m
                dn = (target_id + 1) * 6.0
                de = rng.uniform(-2.0, 2.0)
                # Convert to lat/lon (approx 111,139 m/deg lat, 84,400 m/deg lon)
                t_lat = base_lat + (dn / 111139.0)
                t_lon = base_lon + (de / 84400.0)
                target_key = f"seq{seq_idx}_tgt{target_id}"
                seq_gt_targets[target_key] = {
                    "lat": t_lat,
                    "lon": t_lon,
                    "first_frame_visible": max(0, target_id * 2),
                    "last_frame_visible": min(frames_per_seq - 1, target_id * 2 + 6),
                    "first_confirmed_frame": None,
                }

            # Run sequence frame by frame
            seq_time_start = 1700000000.0 + (seq_idx * 1000.0)

            for frame_idx in range(frames_per_seq):
                current_time = seq_time_start + (frame_idx / fps)
                # Drone moves forward along transect at 3.0 m/s
                drone_dn = frame_idx * (3.0 / fps)
                sim_drone_lat = base_lat + (drone_dn / 111139.0)
                sim_drone_lon = base_lon
                frame_detections: List[GeoreferencedDetection] = []

                # 1. Check which GT targets are within field of view and detected
                for t_key, t_info in seq_gt_targets.items():
                    if t_info["first_frame_visible"] <= frame_idx <= t_info["last_frame_visible"]:
                        # Target is visible; detection occurs with probability p_det
                        if rng.uniform(0.0, 1.0) < p_det:
                            # Add small GPS measurement noise (sigma = 0.4 m)
                            noise_n = rng.normal(0.0, 0.4)
                            noise_e = rng.normal(0.0, 0.4)
                            meas_lat = t_info["lat"] + (noise_n / 111139.0)
                            meas_lon = t_info["lon"] + (noise_e / 84400.0)
                            meas_conf = rng.uniform(conf_thresh, 0.98)
                            frame_detections.append(
                                GeoreferencedDetection(
                                    label="rubbish",
                                    confidence=meas_conf,
                                    lat=meas_lat,
                                    lon=meas_lon,
                                    altitude_agl=10.0,
                                    drone_lat=sim_drone_lat,
                                    drone_lon=sim_drone_lon,
                                )
                            )

                # 2. Generate transient false positives
                # Number of FPs in this frame drawn from Poisson(lam_fp)
                num_fps = rng.poisson(lam_fp)
                for _ in range(num_fps):
                    # Random transient location in FOV
                    fp_dn = rng.uniform(0.0, 30.0)
                    fp_de = rng.uniform(-10.0, 10.0)
                    fp_lat = base_lat + (fp_dn / 111139.0)
                    fp_lon = base_lon + (fp_de / 84400.0)
                    fp_conf = rng.uniform(conf_thresh, min(0.92, conf_thresh + 0.25))
                    frame_detections.append(
                        GeoreferencedDetection(
                            label="rubbish",
                            confidence=fp_conf,
                            lat=fp_lat,
                            lon=fp_lon,
                            altitude_agl=10.0,
                            drone_lat=sim_drone_lat,
                            drone_lon=sim_drone_lon,
                        )
                    )

                # 3. Update TrackAggregator with frame detections
                emitted = aggregator.update(frame_detections, now=current_time)

                for item in emitted:
                    total_broadcast_events += 1
                    if item.status == "update":
                        duplicate_events += 1
                        continue

                    # status == "new" (a freshly confirmed track)
                    # Check whether this track matches any true target within merge_radius_m
                    matched_tgt = None
                    for t_key, t_info in seq_gt_targets.items():
                        dn_m = (item.lat - t_info["lat"]) * 111139.0
                        de_m = (item.lon - t_info["lon"]) * 84400.0
                        dist = math.hypot(dn_m, de_m)
                        if dist <= 2.5:
                            matched_tgt = t_key
                            break

                    if matched_tgt is not None:
                        confirmed_true_targets.add(matched_tgt)
                        t_info = seq_gt_targets[matched_tgt]
                        if t_info["first_confirmed_frame"] is None:
                            t_info["first_confirmed_frame"] = frame_idx
                            # Confirmation delay = frames between first visible and confirmed
                            delay_frames = frame_idx - t_info["first_frame_visible"] + 1
                            confirmation_delays.append(delay_frames)
                    else:
                        # False alert: an ungrounded cluster reached min_hits
                        confirmed_false_alerts += 1

        # Calculate metrics
        tp = len(confirmed_true_targets)
        fp = confirmed_false_alerts
        fn = total_gt_targets - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / total_gt_targets if total_gt_targets > 0 else 0.0
        f1 = (2.0 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        mean_delay_frames = float(np.mean(confirmation_delays)) if confirmation_delays else float(min_hits)
        mean_delay_sec = mean_delay_frames / fps
        false_alerts_per_min = confirmed_false_alerts / total_simulated_minutes if total_simulated_minutes > 0 else 0.0
        dup_rate_pct = (duplicate_events / total_broadcast_events * 100.0) if total_broadcast_events > 0 else 0.0

        res_row = {
            "experiment": name,
            "confidence": conf_thresh,
            "min_hits": min_hits,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "true_positives": tp,
            "false_alerts": fp,
            "missed_detections": fn,
            "total_ground_truth": total_gt_targets,
            "mean_confirmation_delay_sec": round(mean_delay_sec, 2),
            "false_alerts_per_minute": round(false_alerts_per_min, 3),
            "duplicate_event_rate_pct": round(dup_rate_pct, 2),
        }
        results.append(res_row)

    return results


def export_and_plot(results: List[Dict], out_dir: Path):
    """Save results to CSV, JSON, and visual charts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_dir = out_dir / "curves"
    curves_dir.mkdir(parents=True, exist_ok=True)

    # 1. Export CSV
    csv_file = out_dir / "temporal_confirmation_results.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = list(results[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"[OK] Exported CSV: {csv_file}")

    # 2. Export JSON
    json_file = out_dir / "temporal_confirmation_results.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "description": "Multi-frame temporal confirmation evaluation across DroneSwarm configurations",
                "configurations_evaluated": len(results),
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"[OK] Exported JSON: {json_file}")

    # 3. Generate Trade-Off Visualization
    names = [r["experiment"] for r in results]
    precs = [r["precision"] * 100 for r in results]
    recs = [r["recall"] * 100 for r in results]
    f1s = [r["f1_score"] * 100 for r in results]
    fa_rates = [r["false_alerts_per_minute"] for r in results]
    delays = [r["mean_confirmation_delay_sec"] for r in results]

    x = np.arange(len(names))
    width = 0.25

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5), dpi=150)

    # Subplot 1: Precision, Recall, F1
    rects1 = ax1.bar(x - width, precs, width, label="Precision (%)", color="#10b981", alpha=0.9)
    rects2 = ax1.bar(x, recs, width, label="Recall (%)", color="#3b82f6", alpha=0.9)
    rects3 = ax1.bar(x + width, f1s, width, label="F1 Score (%)", color="#f59e0b", alpha=0.9)

    ax1.set_title("Detection Accuracy by Temporal Configuration", fontsize=12, fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(names, rotation=15, ha="right", fontsize=9)
    ax1.set_ylabel("Percentage (%)", fontsize=10)
    ax1.set_ylim([40, 105])
    ax1.grid(True, linestyle="--", alpha=0.4, axis="y")
    ax1.legend(loc="lower left", frameon=True)

    for rects in [rects1, rects2, rects3]:
        for rect in rects:
            height = rect.get_height()
            ax1.annotate(
                f"{height:.1f}%",
                xy=(rect.get_x() + rect.get_width() / 2, height),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7.5,
            )

    # Subplot 2: False Alerts/min vs Confirmation Delay
    color_fa = "#ef4444"
    color_del = "#8b5cf6"

    ax2_twin = ax2.twinx()
    p1 = ax2.plot(x, fa_rates, "o-", color=color_fa, linewidth=2.4, markersize=7, label="False Alerts/min")
    p2 = ax2_twin.plot(x, delays, "s--", color=color_del, linewidth=2.4, markersize=7, label="Confirmation Delay (s)")

    ax2.set_title("Operational Safety & Responsiveness Trade-off", fontsize=12, fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(names, rotation=15, ha="right", fontsize=9)
    ax2.set_ylabel("False Alerts / Minute", color=color_fa, fontsize=10, fontweight="bold")
    ax2.tick_params(axis="y", labelcolor=color_fa)
    ax2.set_ylim([0, max(fa_rates) * 1.25 + 0.1])
    ax2.grid(True, linestyle="--", alpha=0.4)

    ax2_twin.set_ylabel("Confirmation Delay (seconds)", color=color_del, fontsize=10, fontweight="bold")
    ax2_twin.tick_params(axis="y", labelcolor=color_del)
    ax2_twin.set_ylim([0, max(delays) * 1.35 + 0.5])

    # Annotate data points
    for i, (fa, d) in enumerate(zip(fa_rates, delays)):
        ax2.annotate(f"{fa:.2f}/min", (i, fa), textcoords="offset points", xytext=(0, 6), ha="center", color=color_fa, fontsize=8)
        ax2_twin.annotate(f"{d:.1f}s", (i, d), textcoords="offset points", xytext=(0, -12), ha="center", color=color_del, fontsize=8)

    lines = p1 + p2
    labels = [l.get_label() for l in lines]
    ax2.legend(lines, labels, loc="upper right", frameon=True)

    plt.tight_layout()
    chart_path = curves_dir / "temporal_confirmation_tradeoffs.png"
    plt.savefig(chart_path)
    plt.close()
    print(f"[OK] Saved chart: {chart_path}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate multi-frame temporal confirmation.")
    parser.add_argument("--out-dir", default="ml/reports", help="Output directory")
    args = parser.parse_args()

    print("=" * 72)
    print("DroneSwarm — Multi-Frame Temporal Confirmation Optimization")
    print("=" * 72)

    results = simulate_multi_frame_evaluation()

    print("\n--- Summary of Results ---")
    print(f"{'Experiment':<12} {'Conf':<6} {'Hits':<5} {'Precision':<10} {'Recall':<10} {'F1':<8} {'FA/min':<10} {'Delay(s)':<8}")
    print("-" * 72)
    for r in results:
        print(
            f"{r['experiment']:<12} {r['confidence']:<6.2f} {r['min_hits']:<5d} "
            f"{r['precision']*100:<9.2f}% {r['recall']*100:<9.2f}% {r['f1_score']*100:<7.2f}% "
            f"{r['false_alerts_per_minute']:<10.2f} {r['mean_confirmation_delay_sec']:<8.2f}"
        )
    print("-" * 72)

    export_and_plot(results, Path(args.out_dir))


if __name__ == "__main__":
    main()

