"""
Four-drone throughput investigation with AirSim.
Measures:
- Steps per minute per drone
- Capture ms (p50 / p95)
- Inference ms (p50 / p95)
- Rejection histogram
For:
1. 1 drone alone (Drone1)
2. 4 drones concurrent (synchronous baseline vs decoupled)
3. 4 drones concurrent with capture staggering (e.g. 80ms offset)
4. Capture latency comparison across resolutions
"""

import asyncio
from pathlib import Path
import sys
import time
from typing import Any, Dict, List
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


class MockSwarm:
    def __init__(self, drone_id: str, peers: List[str]):
        self.drone_id = drone_id
        self.registry = MockRegistry(peers)


class MockRegistry:
    def __init__(self, peers: List[str]):
        self.peers = {p: MockPeer(p) for p in peers}


class MockPeer:
    def __init__(self, p: str):
        self.drone_id = p
        self.is_active = True
        self.lat = 47.641500
        self.lon = -122.140000
        self.alt = 5.0
        self.last_position_time = time.time()
        self.velocity_x = 0.0
        self.velocity_y = 0.0
        self.velocity_z = 0.0


async def run_throughput_trial(
    name: str,
    vehicles: List[str],
    duration_s: float = 20.0,
    decoupled: bool = True,
    stagger_offset_s: float = 0.0,
    max_fps: float = 5.0,
) -> Dict[str, Any]:
    print(f"\n{'='*78}\nTRIAL: {name} (vehicles={vehicles}, decoupled={decoupled}, stagger={stagger_offset_s}s, max_fps={max_fps})\n{'='*78}")
    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()

    # Pre-flight all vehicles to hover safely
    for vname in vehicles:
        client.enableApiControl(True, vehicle_name=vname)
        client.armDisarm(True, vehicle_name=vname)
        kin = client.simGetGroundTruthKinematics(vehicle_name=vname)
        if kin.position.z_val > -1.0:
            client.takeoffAsync(vehicle_name=vname).join()
            client.moveToPositionAsync(0.0, 0.0, -5.0, 2.0, vehicle_name=vname).join()

    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    base_cfg = load_yaml_config(cfg_file, PerceptionConfig)

    # Use CUDA if available, fallback to CPU
    detector = GarbageDetector(
        weights_path=str(REPO_ROOT / base_cfg.weights),
        imgsz=base_cfg.imgsz,
        confidence=base_cfg.confidence,
        iou=base_cfg.iou,
        device="cuda" if base_cfg.device == "cuda" or base_cfg.device == "auto" else "cpu",
    )
    detector.load()
    detector.warmup(warmup_frames=2)

    pipelines = []
    sources = []
    stats = {v: {"steps": 0, "capture_ms": [], "inference_ms": []} for v in vehicles}

    for idx, vname in enumerate(vehicles):
        d_id = vname.lower()
        cfg = PerceptionConfig(
            enabled=True,
            max_fps=max_fps,
            decoupled_capture=decoupled,
            confidence=0.55,
            iou=0.5,
            min_hits=2,
            min_altitude_m=2.0,
            max_tilt_deg=20.0,
        )
        src = AirSimFrameSource(vehicle_name=vname, host="127.0.0.1", port=41451, camera_name="bottom_center")
        sources.append(src)
        telem = AirSimTelemetryProvider(client, vname)
        net = MockNetworkSink()
        peers = [o.lower() for o in vehicles if o != vname]
        swarm = MockSwarm(d_id, peers)

        pipe = PerceptionPipeline(d_id, src, detector, telem, net, cfg, swarm_manager=swarm)
        pipelines.append(pipe)

    # Instrument step execution to record timing
    async def run_drone_loop(vname: str, pipe: PerceptionPipeline, src: AirSimFrameSource, stagger: float):
        if stagger > 0:
            await asyncio.sleep(stagger)
        t_end = time.time() + duration_s
        while time.time() < t_end:
            t0 = time.perf_counter()
            await pipe._step()
            dt = (time.perf_counter() - t0) * 1000.0
            stats[vname]["steps"] += 1
            if hasattr(src, "last_capture_ms") and src.last_capture_ms > 0:
                stats[vname]["capture_ms"].append(src.last_capture_ms)
            inf_ms = pipe.last_step_stats.get("inference_latency_ms", 0.0)
            if inf_ms > 0:
                stats[vname]["inference_ms"].append(inf_ms)
            # Sleep to yield event loop according to max_fps interval
            interval = 1.0 / max_fps
            await asyncio.sleep(max(0.001, interval - (dt / 1000.0)))

    # Start capture loops if decoupled
    if decoupled:
        for p in pipelines:
            p.start_capture()

    # Launch drone loops
    tasks = []
    for idx, (vname, pipe, src) in enumerate(zip(vehicles, pipelines, sources)):
        stagger = idx * stagger_offset_s
        tasks.append(asyncio.create_task(run_drone_loop(vname, pipe, src, stagger)))

    await asyncio.gather(*tasks)

    # Cleanup
    for pipe in pipelines:
        await pipe.stop()
    for src in sources:
        await src.close()
    detector.close()

    # Summarize results
    results = {}
    for vname in vehicles:
        s = stats[vname]
        step_count = s["steps"]
        spm = (step_count / duration_s) * 60.0
        caps = s["capture_ms"] if s["capture_ms"] else [0.0]
        infs = s["inference_ms"] if s["inference_ms"] else [0.0]
        pipe_obj = [p for p in pipelines if p.drone_id == vname.lower()][0]

        results[vname] = {
            "steps": step_count,
            "steps_per_minute": round(spm, 1),
            "effective_fps": round(step_count / duration_s, 2),
            "capture_ms_p50": round(float(np.percentile(caps, 50)), 1) if caps else 0.0,
            "capture_ms_p95": round(float(np.percentile(caps, 95)), 1) if caps else 0.0,
            "inference_ms_mean": round(float(np.mean(infs)), 1) if infs else 0.0,
            "rejections": dict(getattr(pipe_obj, "_diag_rejection_histogram", {})),
        }
        print(f"[{vname}] {results[vname]['steps_per_minute']} steps/min ({results[vname]['effective_fps']} fps) | "
              f"capture p50={results[vname]['capture_ms_p50']}ms p95={results[vname]['capture_ms_p95']}ms | "
              f"inference mean={results[vname]['inference_ms_mean']}ms | "
              f"rejections={results[vname]['rejections']}")

    return results


async def main():
    print("=" * 78)
    print("FOUR-DRONE DETECTION THROUGHPUT INVESTIGATION (AIRSIM)")
    print("=" * 78)

    # 1. Single drone alone baseline (1280x720 capture, decoupled)
    res_1drone = await run_throughput_trial(
        name="1 Drone Alone (Decoupled)",
        vehicles=["Drone1"],
        duration_s=10.0,
        decoupled=True,
        stagger_offset_s=0.0,
        max_fps=5.0,
    )

    # 2. Four drones concurrent - Synchronous (decoupled=False)
    res_4drone_sync = await run_throughput_trial(
        name="4 Drones Concurrent - Synchronous (No Decoupling)",
        vehicles=["Drone1", "Drone2", "Drone3", "Drone4"],
        duration_s=10.0,
        decoupled=False,
        stagger_offset_s=0.0,
        max_fps=5.0,
    )

    # 3. Four drones concurrent - Decoupled, no stagger
    res_4drone_decoupled = await run_throughput_trial(
        name="4 Drones Concurrent - Decoupled Capture",
        vehicles=["Drone1", "Drone2", "Drone3", "Drone4"],
        duration_s=10.0,
        decoupled=True,
        stagger_offset_s=0.0,
        max_fps=5.0,
    )

    # 4. Four drones concurrent - Decoupled + Staggered Capture (80ms offset)
    res_4drone_staggered = await run_throughput_trial(
        name="4 Drones Concurrent - Decoupled + 80ms Stagger",
        vehicles=["Drone1", "Drone2", "Drone3", "Drone4"],
        duration_s=10.0,
        decoupled=True,
        stagger_offset_s=0.08,
        max_fps=5.0,
    )

    print("\n" + "=" * 78)
    print("COMPARATIVE THROUGHPUT SUMMARY (AVERAGE ACROSS DRONES)")
    print("=" * 78)
    for name, r in [
        ("1 Drone (Decoupled)", res_1drone),
        ("4 Drones (Sync)", res_4drone_sync),
        ("4 Drones (Decoupled)", res_4drone_decoupled),
        ("4 Drones (Decoupled + Stagger)", res_4drone_staggered),
    ]:
        avg_spm = np.mean([v["steps_per_minute"] for v in r.values()])
        avg_fps = np.mean([v["effective_fps"] for v in r.values()])
        avg_cap_p50 = np.mean([v["capture_ms_p50"] for v in r.values()])
        avg_cap_p95 = np.mean([v["capture_ms_p95"] for v in r.values()])
        avg_inf = np.mean([v["inference_ms_mean"] for v in r.values()])
        print(f"{name:<32} | {avg_spm:5.1f} steps/min | {avg_fps:4.2f} fps | cap p50={avg_cap_p50:5.1f}ms p95={avg_cap_p95:5.1f}ms | inf={avg_inf:4.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
