"""
Benchmark 10 flights per max_fps in {1.0, 2.0} at speeds 0.50 m/s and 1.00 m/s over textured target in AirSim.
Includes empty-scene baseline to verify 0 false alerts.

PIPELINE TEST, NOT ACCURACY EVIDENCE:
Uses test-only confidence override (default: 0.35 via --test-conf or TEST_CONF_OVERRIDE).
Production perception config is NEVER modified.
"""

import argparse
import asyncio
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import airsim
from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.frame_sources import AirSimFrameSource
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.shared.config.loader import load_yaml_config
from DroneOS.shared.config.models import PerceptionConfig
from scripts.measure_time_to_confirm import AirSimTelemetryProvider, MockNetworkSink
from scripts.spawn_garbage import clear_targets
from scripts.validate_single_drone_airsim import setup_scene_target

PASS_DISTANCE_M = 6.0   # -3.0m to +3.0m
TARGET_TEXTURE = REPO_ROOT / "ml" / "data" / "uavvaste_high_conf_target.png"


async def run_single_pass(
    client: airsim.MultirotorClient,
    detector: GarbageDetector,
    src: AirSimFrameSource,
    telem: AirSimTelemetryProvider,
    cfg: PerceptionConfig,
    speed_mps: float,
    flight_idx: int,
    has_target: bool = True,
) -> Dict[str, Any]:
    vname = "Drone1"

    clear_targets(client)

    kin = client.simGetGroundTruthKinematics(vehicle_name=vname)
    if kin.position.z_val > -1.0:
        client.takeoffAsync(vehicle_name=vname).join()

    start_x = -3.0
    end_x = 3.0
    alt_m = 5.0

    # Move to start position at hover
    client.moveToPositionAsync(start_x, 0.0, -alt_m, 3.0, vehicle_name=vname).join()
    await asyncio.sleep(0.2)

    # Spawn target at origin (0, 0, 0) on ground if requested
    if has_target:
        await setup_scene_target(
            client=client,
            target_name="GarbageTarget_HighConf",
            offset_north_m=0.0,
            offset_east_m=0.0,
            ground_z_m=0.0,
            scale_m=(0.60, 0.60, 0.25),
            mesh_name="Cube",
            unique_name=False,
            texture_path=TARGET_TEXTURE,
        )

    net = MockNetworkSink()
    pipeline = PerceptionPipeline("drone1", src, detector, telem, net, cfg)

    confirmed_alerts: List[Dict[str, Any]] = []
    orig_flush = pipeline._flush_confirmed_alerts

    def hook_flush():
        if hasattr(pipeline.aggregator, "last_confirmed_events"):
            for evt in pipeline.aggregator.last_confirmed_events:
                confirmed_alerts.append(dict(evt))
        orig_flush()

    pipeline._flush_confirmed_alerts = hook_flush

    if cfg.decoupled_capture:
        pipeline.start_capture()
        # Warmup: ensure at least one frame is in the buffer before starting movement
        t_wait_frame = time.time()
        while pipeline._latest_frame is None and (time.time() - t_wait_frame) < 3.0:
            await asyncio.sleep(0.02)
    else:
        # Sync warmup frame
        await src.get_frame()

    # Now start pass at fixed speed
    flight_duration_s = PASS_DISTANCE_M / speed_mps
    client.moveToPositionAsync(end_x, 0.0, -alt_m, speed_mps, vehicle_name=vname)

    t_start = time.time()
    frames_processed = 0
    first_hit_time: Optional[float] = None

    while (time.time() - t_start) < (flight_duration_s + 1.0):
        t_step_start = time.monotonic()
        await pipeline._step()
        frames_processed += 1

        for trk in pipeline.aggregator.tracks.values():
            if trk.hits >= 1 and first_hit_time is None:
                first_hit_time = trk.first_seen

        step_dur = time.monotonic() - t_step_start
        sleep_needed = max(0.002, (1.0 / cfg.max_fps) - step_dur)
        await asyncio.sleep(sleep_needed)

    await pipeline.stop(close_detector=False, close_source=False)
    clear_targets(client)

    confirmed = len(confirmed_alerts) > 0
    sec_to_confirm = confirmed_alerts[0].get("seconds") if confirmed else None
    frames_to_confirm = confirmed_alerts[0].get("frames") if confirmed else None

    res = {
        "flight_idx": flight_idx,
        "max_fps": cfg.max_fps,
        "speed_mps": speed_mps,
        "has_target": has_target,
        "confirmed": confirmed,
        "confirmed_count": len(confirmed_alerts),
        "seconds_to_confirm": sec_to_confirm,
        "frames_to_confirm": frames_to_confirm,
        "frames_processed": frames_processed,
    }
    return res


async def evaluate_batch(
    client: airsim.MultirotorClient,
    detector: GarbageDetector,
    src: AirSimFrameSource,
    telem: AirSimTelemetryProvider,
    base_cfg: PerceptionConfig,
    max_fps_val: float,
    speed_mps: float,
    test_conf: float,
    num_flights: int = 10,
) -> Dict[str, Any]:
    print(f"\n{'='*78}")
    print(f"RUNNING {num_flights} FLIGHTS: max_fps={max_fps_val} | Speed={speed_mps} m/s | Test Conf={test_conf}")
    print(f"{'='*78}")

    cfg = PerceptionConfig(
        enabled=True,
        max_fps=max_fps_val,
        decoupled_capture=True,
        confidence=test_conf,  # Test-only override
        iou=base_cfg.iou,
        min_hits=base_cfg.min_hits,
        min_temporal_separation_s=base_cfg.min_temporal_separation_s,
        min_drone_displacement_m=base_cfg.min_drone_displacement_m,
        min_altitude_m=base_cfg.min_altitude_m,
        max_tilt_deg=base_cfg.max_tilt_deg,
        vehicle_artifact_pixel_tol_px=base_cfg.vehicle_artifact_pixel_tol_px,
    )

    flight_results = []
    for i in range(1, num_flights + 1):
        res = await run_single_pass(client, detector, src, telem, cfg, speed_mps, i, has_target=True)
        flight_results.append(res)
        sec_str = f"{res['seconds_to_confirm']:.2f}s" if res['seconds_to_confirm'] is not None else "MISS"
        print(f"  Flight {i:2d}/{num_flights}: Confirmed={res['confirmed']} | Time to Confirm: {sec_str} | Frames: {res['frames_to_confirm']} | Total Frames: {res['frames_processed']}")

    confirmed_times = [r["seconds_to_confirm"] for r in flight_results if r["confirmed"] and r["seconds_to_confirm"] is not None]
    confirmed_frames = [r["frames_to_confirm"] for r in flight_results if r["confirmed"] and r["frames_to_confirm"] is not None]
    total_frames = [r["frames_processed"] for r in flight_results]

    miss_count = sum(1 for r in flight_results if not r["confirmed"])
    miss_rate = miss_count / num_flights

    median_time = float(np.median(confirmed_times)) if confirmed_times else None
    p90_time = float(np.percentile(confirmed_times, 90)) if confirmed_times else None
    median_frames = float(np.median(confirmed_frames)) if confirmed_frames else None
    p90_frames = float(np.percentile(confirmed_frames, 90)) if confirmed_frames else None
    avg_frames_per_pass = float(np.mean(total_frames)) if total_frames else 0.0

    summary = {
        "max_fps": max_fps_val,
        "speed_mps": speed_mps,
        "num_flights": num_flights,
        "confirmed_count": len(confirmed_times),
        "miss_count": miss_count,
        "miss_rate": round(miss_rate, 3),
        "median_seconds_to_confirm": round(median_time, 3) if median_time is not None else None,
        "p90_seconds_to_confirm": round(p90_time, 3) if p90_time is not None else None,
        "median_frames_to_confirm": median_frames,
        "p90_frames_to_confirm": p90_frames,
        "avg_frames_per_pass": round(avg_frames_per_pass, 1),
        "flights": flight_results,
    }

    print(f"\n--- Batch Summary: max_fps={max_fps_val}, speed={speed_mps} m/s ---")
    print(f"  Confirmed: {len(confirmed_times)}/{num_flights} (Miss Rate: {miss_rate * 100:.1f}%)")
    print(f"  Median Time to Confirm: {summary['median_seconds_to_confirm']} s")
    print(f"  P90 Time to Confirm:    {summary['p90_seconds_to_confirm']} s")
    print(f"  Avg Frames per Pass:    {summary['avg_frames_per_pass']}")
    return summary


async def main():
    parser = argparse.ArgumentParser(description="Pipeline-Timing Benchmark (10 Flights Sweep)")
    parser.add_argument("--test-conf", type=float, default=None, help="TEST-ONLY confidence override")
    parser.add_argument("--num-flights", type=int, default=10, help="Number of flights per condition")
    args = parser.parse_args()

    test_conf = args.test_conf
    if test_conf is None and "TEST_CONF_OVERRIDE" in os.environ:
        try:
            test_conf = float(os.environ["TEST_CONF_OVERRIDE"])
        except ValueError:
            pass
    if test_conf is None:
        test_conf = 0.35  # Default test override for pipeline timing validation

    print("=" * 80)
    print("PIPELINE TEST, NOT ACCURACY EVIDENCE")
    print(f"TEST CONFIDENCE OVERRIDE: {test_conf:.2f} (TEST-ONLY, production config unchanged)")
    print("=" * 80)

    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()
    client.enableApiControl(True, vehicle_name="Drone1")
    client.armDisarm(True, vehicle_name="Drone1")

    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    base_cfg = load_yaml_config(cfg_file, PerceptionConfig)

    detector = GarbageDetector(
        weights_path=str(REPO_ROOT / base_cfg.weights),
        imgsz=base_cfg.imgsz,
        confidence=test_conf,
        iou=base_cfg.iou,
        device="cuda" if base_cfg.device in ("cuda", "auto") else "cpu",
    )
    detector.load()
    detector.warmup(warmup_frames=2)

    src = AirSimFrameSource(vehicle_name="Drone1", host="127.0.0.1", port=41451, camera_name="bottom_center")
    telem = AirSimTelemetryProvider(client, "Drone1")

    conditions = [
        {"fps": 1.0, "speed": 0.5},
        {"fps": 2.0, "speed": 0.5},
        {"fps": 1.0, "speed": 1.0},
        {"fps": 2.0, "speed": 1.0},
    ]

    all_summaries = []
    for cond in conditions:
        summary = await evaluate_batch(
            client=client,
            detector=detector,
            src=src,
            telem=telem,
            base_cfg=base_cfg,
            max_fps_val=cond["fps"],
            speed_mps=cond["speed"],
            test_conf=test_conf,
            num_flights=args.num_flights,
        )
        all_summaries.append(summary)

    # Empty Scene Flight (False Alert Check)
    print(f"\n{'='*78}\nEMPTY SCENE FLIGHT (FALSE ALERT CHECK)\n{'='*78}")
    empty_cfg = PerceptionConfig(
        enabled=True,
        max_fps=2.0,
        decoupled_capture=True,
        confidence=test_conf,
        iou=base_cfg.iou,
        min_hits=base_cfg.min_hits,
        min_temporal_separation_s=base_cfg.min_temporal_separation_s,
        min_drone_displacement_m=base_cfg.min_drone_displacement_m,
        vehicle_artifact_pixel_tol_px=base_cfg.vehicle_artifact_pixel_tol_px,
    )
    empty_res = await run_single_pass(client, detector, src, telem, empty_cfg, speed_mps=0.5, flight_idx=0, has_target=False)
    print(f"Empty Flight Confirmed Alerts: {empty_res['confirmed_count']} (PASS: 0 false alerts)" if empty_res['confirmed_count'] == 0 else f"FAIL: {empty_res['confirmed_count']} false alerts")

    # Cleanup
    await src.close()
    detector.close()
    try:
        clear_targets(client)
        client.moveToPositionAsync(0.0, 0.0, -2.0, 2.0, timeout_sec=5, vehicle_name="Drone1").join()
        client.landAsync(timeout_sec=5, vehicle_name="Drone1").join()
        client.armDisarm(False, vehicle_name="Drone1")
        client.enableApiControl(False, vehicle_name="Drone1")
    except Exception:
        pass

    print("\n" + "=" * 80)
    print("PIPELINE TEST, NOT ACCURACY EVIDENCE")
    print("FINAL 10-FLIGHT TIME-TO-CONFIRM BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"{'max_fps':<8} | {'Speed (m/s)':<12} | {'Miss Rate':<12} | {'Median Sec':<12} | {'P90 Sec':<12} | {'Frames/Pass'}")
    print("-" * 80)
    for s in all_summaries:
        med_s = f"{s['median_seconds_to_confirm']:.2f} s" if s['median_seconds_to_confirm'] is not None else "N/A"
        p90_s = f"{s['p90_seconds_to_confirm']:.2f} s" if s['p90_seconds_to_confirm'] is not None else "N/A"
        print(f"{s['max_fps']:<8.1f} | {s['speed_mps']:<12.2f} | {s['miss_rate']*100:<11.1f}% | {med_s:<12} | {p90_s:<12} | {s['avg_frames_per_pass']:<11.1f}")
    print(f"\nEmpty Scene False Alerts: {empty_res['confirmed_count']} (Expected: 0)")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
