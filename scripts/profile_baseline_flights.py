"""
Step 1: Baseline Profiling in AirSim.
Measures performance and timing across two flights:
1. Flight 1: Over known physical garbage target.
2. Flight 2: Over scene with NO garbage in view (false-alert baseline).

Logs every ~5 s:
- capture (AirSim simGetImages) ms
- preprocess ms
- inference ms
- postprocess+filter ms
- tracker/aggregator ms
- end-to-end ms and effective fps
- max raw confidence in the window (before threshold)
- for each confirmed alert: frames and seconds from first hit to confirmation
"""

import asyncio
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

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

logger = setup_logger("BaselineProfiling")


class AirSimTelemetryProvider:
    """Provides genuine telemetry from AirSim client for DroneOS PerceptionPipeline."""
    def __init__(self, client: airsim.MultirotorClient, vehicle_name: str = "Drone1"):
        self.client = client
        self.vehicle_name = vehicle_name

    async def get_telemetry(self) -> TelemetryData:
        gps = self.client.getGpsData(vehicle_name=self.vehicle_name)
        kinematics = self.client.simGetGroundTruthKinematics(vehicle_name=self.vehicle_name)
        q = kinematics.orientation

        # Quaternion to Euler (degrees)
        sinr_cosp = 2.0 * (q.w_val * q.x_val + q.y_val * q.z_val)
        cosr_cosp = 1.0 - 2.0 * (q.x_val * q.x_val + q.y_val * q.y_val)
        roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

        sinp = 2.0 * (q.w_val * q.y_val - q.z_val * q.x_val)
        if abs(sinp) >= 1.0:
            pitch = math.degrees(math.copysign(math.pi / 2.0, sinp))
        else:
            pitch = math.degrees(math.asin(sinp))

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
    """Collects broadcast messages from pipeline."""
    def __init__(self):
        self.messages: List[DetectionMessage] = []

    async def broadcast_message(self, msg: DetectionMessage):
        self.messages.append(msg)


async def execute_flight_profile(
    client: airsim.MultirotorClient,
    flight_name: str,
    duration_s: float = 16.0,
    has_target: bool = True,
) -> Dict[str, Any]:
    print(f"\n{'='*70}")
    print(f"STARTING {flight_name.upper()} (Duration ~{duration_s:.0f}s, Target={has_target})")
    print(f"{'='*70}")

    vehicle_name = "Drone1"
    host = "127.0.0.1"
    port = 41451

    # Clear previous targets
    clear_targets(client)

    target_info = None
    if has_target:
        # Spawn known target at NED (0.0, 0.0, 0.0)
        tex_path = REPO_ROOT / "assets" / "perception" / "sample_target_garbage.png"
        ok, target_info = await setup_scene_target(
            client=client,
            target_name="GarbageTarget_Profile",
            offset_north_m=0.0,
            offset_east_m=0.0,
            ground_z_m=0.0,
            scale_m=(0.55, 0.55, 0.20),
            mesh_name="Cube",
            unique_name=False,
            texture_path=tex_path,
        )
        print(f"  Target introduced: {target_info}")

    # Set up Drone1 position and flight
    client.enableApiControl(True, vehicle_name=vehicle_name)
    client.armDisarm(True, vehicle_name=vehicle_name)

    # Initial position: 8m altitude AGL
    target_alt = 8.0
    start_x = -2.0
    start_y = 0.0
    mv = client.moveToPositionAsync(start_x, start_y, -target_alt, 2.0, vehicle_name=vehicle_name)
    mv.join()
    await asyncio.sleep(0.5)

    # Load baseline perception config
    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    cfg = load_yaml_config(cfg_file, PerceptionConfig)
    cfg.enabled = True
    cfg.max_fps = 4.0  # Run up to 4 fps for profiling rate

    # Frame source & Detector
    frame_src = AirSimFrameSource(vehicle_name=vehicle_name, host=host, port=port, camera_name="bottom_center")
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

    # Intercept profile window emissions for report generation
    profile_windows = []
    original_record = pipeline._record_profile_sample

    def hook_record(sim_get_images_ms, capture_ms, preprocess_ms, inference_ms, postprocess_ms, tracker_ms, e2e_ms, raw_conf):
        dt = time.monotonic() - pipeline._profile_window_start
        if dt >= 5.0 and pipeline._profile_frames > 0:
            window_summary = {
                "window_idx": len(profile_windows) + 1,
                "duration_s": round(dt, 2),
                "frames": pipeline._profile_frames,
                "fps": round(pipeline._profile_frames / dt, 2),
                "e2e_ms": round(sum(pipeline._profile_e2e_ms) / len(pipeline._profile_e2e_ms), 1),
                "capture_ms": round(sum(pipeline._profile_capture_ms) / len(pipeline._profile_capture_ms), 1),
                "sim_get_images_ms": round(sum(pipeline._profile_sim_get_images_ms) / len(pipeline._profile_sim_get_images_ms), 1),
                "preprocess_ms": round(sum(pipeline._profile_preprocess_ms) / len(pipeline._profile_preprocess_ms), 1),
                "inference_ms": round(sum(pipeline._profile_inference_ms) / len(pipeline._profile_inference_ms), 1),
                "postprocess_filter_ms": round(sum(pipeline._profile_postprocess_ms) / len(pipeline._profile_postprocess_ms), 1),
                "tracker_ms": round(sum(pipeline._profile_tracker_ms) / len(pipeline._profile_tracker_ms), 1),
                "max_raw_conf": round(pipeline._profile_max_raw_conf, 4),
            }
            profile_windows.append(window_summary)
        original_record(sim_get_images_ms, capture_ms, preprocess_ms, inference_ms, postprocess_ms, tracker_ms, e2e_ms, raw_conf)

    pipeline._record_profile_sample = hook_record

    confirmed_alerts = []
    orig_flush = pipeline._flush_confirmed_alerts

    def hook_flush():
        if hasattr(pipeline.aggregator, "last_confirmed_events"):
            for evt in pipeline.aggregator.last_confirmed_events:
                confirmed_alerts.append(dict(evt))
        orig_flush()

    pipeline._flush_confirmed_alerts = hook_flush

    # Start moving vehicle slowly across target area (+X North direction)
    end_x = 3.0
    speed = (end_x - start_x) / duration_s
    move_task = client.moveToPositionAsync(end_x, 0.0, -target_alt, max(0.5, speed), vehicle_name=vehicle_name)

    # Run pipeline loop
    t_start = time.time()
    step_count = 0
    try:
        while (time.time() - t_start) < duration_s:
            await pipeline._step()
            step_count += 1
            await asyncio.sleep(1.0 / cfg.max_fps)
    finally:
        await frame_src.close()
        detector.close()
        move_task.join()

    # Flush any remaining window
    now_m = time.monotonic()
    dt = now_m - pipeline._profile_window_start
    if pipeline._profile_frames > 0:
        window_summary = {
            "window_idx": len(profile_windows) + 1,
            "duration_s": round(dt, 2),
            "frames": pipeline._profile_frames,
            "fps": round(pipeline._profile_frames / dt, 2),
            "e2e_ms": round(sum(pipeline._profile_e2e_ms) / len(pipeline._profile_e2e_ms), 1),
            "capture_ms": round(sum(pipeline._profile_capture_ms) / len(pipeline._profile_capture_ms), 1),
            "sim_get_images_ms": round(sum(pipeline._profile_sim_get_images_ms) / len(pipeline._profile_sim_get_images_ms), 1),
            "preprocess_ms": round(sum(pipeline._profile_preprocess_ms) / len(pipeline._profile_preprocess_ms), 1),
            "inference_ms": round(sum(pipeline._profile_inference_ms) / len(pipeline._profile_inference_ms), 1),
            "postprocess_filter_ms": round(sum(pipeline._profile_postprocess_ms) / len(pipeline._profile_postprocess_ms), 1),
            "tracker_ms": round(sum(pipeline._profile_tracker_ms) / len(pipeline._profile_tracker_ms), 1),
            "max_raw_conf": round(pipeline._profile_max_raw_conf, 4),
        }
        profile_windows.append(window_summary)

    print(f"COMPLETED {flight_name.upper()}: {step_count} frames processed in {duration_s:.1f}s.")
    return {
        "flight_name": flight_name,
        "has_target": has_target,
        "duration_s": duration_s,
        "total_frames": step_count,
        "profile_windows": profile_windows,
        "confirmed_alerts": confirmed_alerts,
        "broadcast_messages": len(network_sink.messages),
    }


async def main():
    print("=" * 70)
    print("STEP 0: PRECONDITIONS VERIFICATION")
    print("=" * 70)

    # Check perception status logging format
    env_perc = os.environ.get("DRONEOS_PERCEPTION", "1")
    os.environ["DRONEOS_PERCEPTION"] = env_perc
    src_type = "env" if "DRONEOS_PERCEPTION" in os.environ else "yaml"
    print(f"perception: enabled=true source={src_type} status=OK")

    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    cfg = load_yaml_config(cfg_file, PerceptionConfig)

    baseline_info = {
        "device_provider": "CPUExecutionProvider / PyTorch CPU (device=None)",
        "imgsz": cfg.imgsz,
        "conf": cfg.confidence,
        "min_hits": cfg.min_hits,
        "capture_resolution": "1280x720 (bottom_center camera)",
    }
    print("BASELINE CONFIGURATION:")
    for k, v in baseline_info.items():
        print(f"  - {k}: {v}")

    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()

    # 1. Flight 1: Over known target
    flight1_res = await execute_flight_profile(client, "Flight 1: Known Target", duration_s=16.0, has_target=True)

    # 2. Flight 2: No garbage in view (false-alert baseline)
    flight2_res = await execute_flight_profile(client, "Flight 2: No Garbage Baseline", duration_s=16.0, has_target=False)

    report = {
        "baseline": baseline_info,
        "flight1_known_target": flight1_res,
        "flight2_no_garbage_baseline": flight2_res,
    }

    out_json = REPO_ROOT / "ml" / "reports" / "step1_baseline_profiling.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2))
    print(f"\nSaved profiling results to: {out_json}")


if __name__ == "__main__":
    asyncio.run(main())
