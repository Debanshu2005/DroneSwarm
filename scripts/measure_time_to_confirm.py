"""
Measure pipeline time-to-confirm at max_fps = 1, 2, 3, 4 over textured target
and verify zero false alerts on empty scene flight.

NOTE: Textured target results are labeled 'pipeline test, not accuracy evidence'.
"""

import asyncio
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import airsim
from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.frame_sources import AirSimFrameSource
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.shared.config.loader import load_yaml_config
from DroneOS.shared.config.models import PerceptionConfig
from DroneOS.shared.protocol.messages import DetectionMessage, TelemetryData
from DroneOS.shared.utils.logger import setup_logger
from scripts.spawn_garbage import clear_targets
from scripts.validate_single_drone_airsim import setup_scene_target

logger = setup_logger("TimeToConfirm")


class AirSimTelemetryProvider:
    def __init__(self, client: airsim.MultirotorClient, vehicle_name: str = "Drone1"):
        self.client = client
        self.vehicle_name = vehicle_name

    async def get_telemetry(self) -> TelemetryData:
        gps = self.client.getGpsData(vehicle_name=self.vehicle_name)
        kinematics = self.client.simGetGroundTruthKinematics(vehicle_name=self.vehicle_name)
        q = kinematics.orientation

        sinr_cosp = 2.0 * (q.w_val * q.x_val + q.y_val * q.z_val)
        cosr_cosp = 1.0 - 2.0 * (q.x_val * q.x_val + q.y_val * q.y_val)
        roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

        sinp = 2.0 * (q.w_val * q.y_val - q.z_val * q.x_val)
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, sinp))))

        siny_cosp = 2.0 * (q.w_val * q.z_val + q.x_val * q.y_val)
        cosy_cosp = 1.0 - 2.0 * (q.y_val * q.y_val + q.z_val * q.z_val)
        yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

        alt = max(2.0, abs(kinematics.position.z_val))

        return TelemetryData(
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
            altitude=alt,
            latitude=gps.gnss.geo_point.latitude,
            longitude=gps.gnss.geo_point.longitude,
            roll=roll,
            pitch=pitch,
            yaw=yaw,
            heading=yaw,
            timestamp=time.time(),
        )


class MockNetworkSink:
    def __init__(self):
        self.messages: List[DetectionMessage] = []

    async def broadcast_message(self, msg: DetectionMessage):
        self.messages.append(msg)


async def run_single_flight(
    client: airsim.MultirotorClient,
    max_fps_val: float,
    has_target: bool,
    flight_duration_s: float = 12.0,
) -> Dict[str, Any]:
    vehicle_name = "Drone1"
    host = "127.0.0.1"
    port = 41451

    # Clear previous targets
    clear_targets(client)

    target_texture = REPO_ROOT / "ml" / "data" / "uavvaste_high_conf_target.png"
    target_info = None

    if has_target:
        # Spawn target at origin (0, 0, 0)
        ok, target_info = await setup_scene_target(
            client=client,
            target_name="GarbageTarget_HighConf",
            offset_north_m=0.0,
            offset_east_m=0.0,
            ground_z_m=0.0,
            scale_m=(0.60, 0.60, 0.25),
            mesh_name="Cube",
            unique_name=False,
            texture_path=target_texture,
        )
        print(f"  Target Spawned (textured with {target_texture.name}): {target_info}")

    client.enableApiControl(True, vehicle_name=vehicle_name)
    client.armDisarm(True, vehicle_name=vehicle_name)

    # Position drone at start position (-3.0, 0.0, -5.0) -> 5m AGL
    # Moving to (3.0, 0.0, -5.0) ensures 6.0m total displacement across target
    # This comfortably exceeds required_disp (~2.12m)
    start_x = -3.0
    end_x = 3.0
    alt_m = 5.0
    client.moveToPositionAsync(start_x, 0.0, -alt_m, 3.0, vehicle_name=vehicle_name).join()
    await asyncio.sleep(0.5)

    # Load perception config
    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    cfg = load_yaml_config(cfg_file, PerceptionConfig)
    cfg.enabled = True
    cfg.max_fps = max_fps_val

    # Set up detector using models/garbage.pt
    detector = GarbageDetector(
        weights_path=str(REPO_ROOT / cfg.weights),
        imgsz=cfg.imgsz,
        confidence=cfg.confidence,
        iou=cfg.iou,
        device=cfg.device,
        classes=cfg.classes,
        min_area_ratio=getattr(cfg, "min_area_ratio", 0.0),
        max_area_ratio=getattr(cfg, "max_area_ratio", 1.0),
        max_aspect_ratio=getattr(cfg, "max_aspect_ratio", 0.0),
    )
    detector.load()

    frame_src = AirSimFrameSource(vehicle_name=vehicle_name, host=host, port=port, camera_name="bottom_center")
    telem_prov = AirSimTelemetryProvider(client, vehicle_name)
    network_sink = MockNetworkSink()

    pipeline = PerceptionPipeline(
        drone_id="drone1",
        frame_source=frame_src,
        detector=detector,
        flight_controller=telem_prov,
        network_adapter=network_sink,
        config=cfg,
    )

    confirmed_alerts: List[Dict[str, Any]] = []
    orig_flush = pipeline._flush_confirmed_alerts

    def hook_flush():
        if hasattr(pipeline.aggregator, "last_confirmed_events"):
            for evt in pipeline.aggregator.last_confirmed_events:
                confirmed_alerts.append(dict(evt))
        orig_flush()

    pipeline._flush_confirmed_alerts = hook_flush

    # Start slow movement across target
    speed = (end_x - start_x) / flight_duration_s
    client.moveToPositionAsync(end_x, 0.0, -alt_m, max(0.2, speed), vehicle_name=vehicle_name)

    t_start = time.time()
    frames_processed = 0
    first_hit_time: Optional[float] = None
    first_hit_frame: Optional[int] = None

    try:
        while (time.time() - t_start) < flight_duration_s:
            t_step_start = time.monotonic()
            await pipeline._step()
            frames_processed += 1

            # Check if aggregator recorded hits
            for trk in pipeline.aggregator.tracks.values():
                if trk.hits >= 1 and first_hit_time is None:
                    first_hit_time = trk.first_seen
                    first_hit_frame = frames_processed

            # Sleep to match max_fps
            step_dur = time.monotonic() - t_step_start
            sleep_needed = max(0.0, (1.0 / cfg.max_fps) - step_dur)
            await asyncio.sleep(sleep_needed)
    finally:
        await frame_src.close()
        detector.close()
        try:
            client.hoverAsync(vehicle_name=vehicle_name)
        except Exception:
            pass

    print(f"  [DEBUG] Total Tracks in Aggregator: {len(pipeline.aggregator.tracks)}")
    for tid, trk in pipeline.aggregator.tracks.items():
        print(f"    Track {tid}: hits={trk.hits}, distinct_hits={trk.distinct_hits}, max_conf={trk.max_confidence:.4f}, confirmed={trk.confirmed}")

    # Collect confirmed alerts
    alert_summary = []
    for alert in confirmed_alerts:
        alert_summary.append({
            "id": alert.get("id"),
            "label": alert.get("label"),
            "confidence": alert.get("confidence"),
            "lat": alert.get("lat"),
            "lon": alert.get("lon"),
            "frames_to_confirm": alert.get("frames"),
            "seconds_to_confirm": alert.get("seconds"),
            "label_note": "pipeline test, not accuracy evidence",
        })

    flight_type = "textured_target" if has_target else "empty_scene"
    result = {
        "flight_type": flight_type,
        "label_note": "pipeline test, not accuracy evidence" if has_target else "false alert baseline",
        "max_fps": max_fps_val,
        "flight_duration_s": round(time.time() - t_start, 2),
        "frames_processed": frames_processed,
        "effective_fps": round(frames_processed / (time.time() - t_start), 2),
        "target_detected": first_hit_time is not None,
        "first_hit_frame": first_hit_frame,
        "confirmed_alerts_count": len(alert_summary),
        "confirmed_alerts": alert_summary,
        "broadcast_messages_count": len(network_sink.messages),
    }

    if alert_summary:
        first_alert = alert_summary[0]
        result["seconds_to_confirm"] = first_alert["seconds_to_confirm"]
        result["frames_to_confirm"] = first_alert["frames_to_confirm"]
    else:
        result["seconds_to_confirm"] = None
        result["frames_to_confirm"] = None

    return result


async def main():
    print("=" * 80)
    print("AIRSIM TIME-TO-CONFIRM AND FALSE-ALERT EVALUATION")
    print("NOTE: Textured target results are 'pipeline test, not accuracy evidence'.")
    print("=" * 80)

    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()

    fps_levels = [1.0, 2.0, 3.0, 4.0]
    results = {
        "metadata": {
            "timestamp": time.time(),
            "target_texture": "ml/data/uavvaste_high_conf_target.png",
            "altitude_agl_m": 5.0,
            "min_hits": 2,
            "min_temporal_separation_s": 2.0,
            "min_drone_displacement_m": 1.5,
        },
        "target_flights": [],
        "empty_scene_flight": None,
    }

    # 1. Target Flights across max_fps = 1, 2, 3, 4
    for fps in fps_levels:
        print(f"\n--- Testing Target Flight @ max_fps={fps} ---")
        res = await run_single_flight(client, max_fps_val=fps, has_target=True)
        results["target_flights"].append(res)
        print(
            f"  max_fps={fps:<3.1f} | Frames Processed: {res['frames_processed']:<2} | "
            f"Confirmed Alerts: {res['confirmed_alerts_count']} | "
            f"Secs to Confirm: {res.get('seconds_to_confirm')}s | "
            f"Frames to Confirm: {res.get('frames_to_confirm')}"
        )

    # 2. Empty Scene Baseline (0 targets)
    print("\n--- Testing Empty Scene Flight (False-Alert Gate) @ max_fps=2.0 ---")
    empty_res = await run_single_flight(client, max_fps_val=2.0, has_target=False)
    results["empty_scene_flight"] = empty_res
    print(
        f"  Empty Flight | Confirmed Alerts: {empty_res['confirmed_alerts_count']} "
        f"({'PASS (0 false alerts)' if empty_res['confirmed_alerts_count'] == 0 else 'FAIL'})"
    )

    out_file = REPO_ROOT / "ml" / "reports" / "time_to_confirm_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80)
    print("SUMMARY TABLE (pipeline test, not accuracy evidence)")
    print("=" * 80)
    print(f"{'Condition':<25} | {'max_fps':<8} | {'Confirmed Alerts':<18} | {'Sec to Confirm':<15} | {'Frames to Confirm'}")
    print("-" * 80)
    for tf in results["target_flights"]:
        sec_str = f"{tf['seconds_to_confirm']:.2f} s" if tf['seconds_to_confirm'] is not None else "N/A"
        frm_str = str(tf['frames_to_confirm']) if tf['frames_to_confirm'] is not None else "N/A"
        print(f"{'Textured Target (Test)':<25} | {tf['max_fps']:<8.1f} | {tf['confirmed_alerts_count']:<18} | {sec_str:<15} | {frm_str}")
    empty_cnt = empty_res['confirmed_alerts_count']
    print(f"{'Empty Scene (Baseline)':<25} | {empty_res['max_fps']:<8.1f} | {empty_cnt:<18} | {'N/A (0 alerts)':<15} | N/A")
    print("=" * 80)
    print(f"Results saved to {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
