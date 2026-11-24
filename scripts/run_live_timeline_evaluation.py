"""
Live AirSim First-Detection Timeline Evaluation.
Runs:
1. Single drone flight (Drone1) over GarbageTarget_HighConf at 5m AGL.
2. 4-drone concurrent flight (Drone1-Drone4) with active peer discovery.
Prints real 'First-Detection Summary' per drone including rejection histograms.
"""

import asyncio
from pathlib import Path
import sys
import time
from typing import List

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


class SwarmManagerMock:
    def __init__(self, drone_id: str, all_drones: List[str]):
        self.drone_id = drone_id
        self.registry = RegistryMock(drone_id, all_drones)


class RegistryMock:
    def __init__(self, current_id: str, all_drones: List[str]):
        self.peers = {}
        for d in all_drones:
            if d != current_id:
                peer = PeerMock(d)
                self.peers[d] = peer


class PeerMock:
    def __init__(self, drone_id: str):
        self.drone_id = drone_id
        self.is_active = True
        self.lat = 47.641500
        self.lon = -122.140000
        self.alt = 5.0
        self.last_position_time = time.time()
        self.velocity_x = 0.0
        self.velocity_y = 0.0
        self.velocity_z = 0.0


async def run_single_drone_timeline():
    print("\n" + "=" * 78)
    print("PART 3A: LIVE AIRSIM 1-DRONE FIRST-DETECTION TIMELINE")
    print("=" * 78)

    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()
    vname = "Drone1"

    client.enableApiControl(True, vehicle_name=vname)
    client.armDisarm(True, vehicle_name=vname)

    clear_targets(client)

    kin = client.simGetGroundTruthKinematics(vehicle_name=vname)
    if kin.position.z_val > -1.0:
        client.takeoffAsync(vehicle_name=vname).join()

    start_x = -3.0
    end_x = 3.0
    alt_m = 5.0
    client.moveToPositionAsync(start_x, 0.0, -alt_m, 3.0, vehicle_name=vname).join()
    await asyncio.sleep(0.5)

    # Spawn target on ground while drone is at 5m AGL
    target_texture = REPO_ROOT / "ml" / "data" / "uavvaste_high_conf_target.png"
    await setup_scene_target(
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

    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    cfg = load_yaml_config(cfg_file, PerceptionConfig)
    cfg.enabled = True
    cfg.max_fps = 2.0

    detector = GarbageDetector(
        weights_path=str(REPO_ROOT / cfg.weights),
        imgsz=cfg.imgsz,
        confidence=cfg.confidence,
        iou=cfg.iou,
        device=cfg.device,
        classes=cfg.classes,
    )
    detector.load()
    detector.warmup(warmup_frames=getattr(cfg, "warmup_frames", 3))

    src = AirSimFrameSource(vehicle_name=vname, host="127.0.0.1", port=41451, camera_name="bottom_center")
    telem = AirSimTelemetryProvider(client, vname)
    net = MockNetworkSink()

    pipeline = PerceptionPipeline("drone1", src, detector, telem, net, cfg)

    # Move slowly across target: 6m in 12s = 0.5 m/s
    client.moveToPositionAsync(end_x, 0.0, -alt_m, 0.5, vehicle_name=vname)

    t_start = time.time()
    while (time.time() - t_start) < 14.0:
        await pipeline._step()
        await asyncio.sleep(0.5)

    print("\n--- Triggering pipeline.stop() for Drone 1 ---")
    await pipeline.stop()
    await src.close()
    detector.close()
    clear_targets(client)


async def run_4drone_timeline():
    print("\n" + "=" * 78)
    print("PART 3B: LIVE AIRSIM 4-DRONE FIRST-DETECTION TIMELINE")
    print("=" * 78)

    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()
    all_drones = ["drone1", "drone2", "drone3", "drone4"]
    vehicles = ["Drone1", "Drone2", "Drone3", "Drone4"]

    clear_targets(client)

    # Position Drone1 to Drone4 safely
    for vname in vehicles:
        client.enableApiControl(True, vehicle_name=vname)
        client.armDisarm(True, vehicle_name=vname)
        kin = client.simGetGroundTruthKinematics(vehicle_name=vname)
        if kin.position.z_val > -1.0:
            client.takeoffAsync(vehicle_name=vname).join()

    # Move Drone1 to -3.0 so it flies across target; others hold formation at 5m AGL
    client.moveToPositionAsync(-3.0, 0.0, -5.0, 3.0, vehicle_name="Drone1").join()
    for vname in ["Drone2", "Drone3", "Drone4"]:
        client.moveToPositionAsync(0.0, 0.0, -5.0, 3.0, vehicle_name=vname).join()
    await asyncio.sleep(0.5)

    # Spawn target on ground
    target_texture = REPO_ROOT / "ml" / "data" / "uavvaste_high_conf_target.png"
    await setup_scene_target(
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

    cfg_file = REPO_ROOT / "DroneOS" / "configs" / "perception.yaml"
    cfg = load_yaml_config(cfg_file, PerceptionConfig)
    cfg.enabled = True
    cfg.max_fps = 2.0

    detector = GarbageDetector(
        weights_path=str(REPO_ROOT / cfg.weights),
        imgsz=cfg.imgsz,
        confidence=cfg.confidence,
        iou=cfg.iou,
        device=cfg.device,
        classes=cfg.classes,
    )
    detector.load()
    detector.warmup(warmup_frames=getattr(cfg, "warmup_frames", 3))

    pipelines = []
    sources = []

    for vname in vehicles:
        d_id = vname.lower()
        src = AirSimFrameSource(vehicle_name=vname, host="127.0.0.1", port=41451, camera_name="bottom_center")
        sources.append(src)
        telem = AirSimTelemetryProvider(client, vname)
        net = MockNetworkSink()
        swarm = SwarmManagerMock(d_id, all_drones)

        pipe = PerceptionPipeline(d_id, src, detector, telem, net, cfg, swarm_manager=swarm)
        pipelines.append(pipe)

    # Start Drone1 slow movement across target
    client.moveToPositionAsync(3.0, 0.0, -5.0, 0.5, vehicle_name="Drone1")

    t_start = time.time()
    step_count = 0
    while (time.time() - t_start) < 14.0:
        step_tasks = [p._step() for p in pipelines]
        await asyncio.gather(*step_tasks)
        step_count += 1
        await asyncio.sleep(0.5)

    print(f"\nCompleted {step_count} concurrent steps. Triggering stop() on all 4 pipelines...")
    for pipe in pipelines:
        await pipe.stop()

    for src in sources:
        await src.close()
    detector.close()
    clear_targets(client)

    # Land all drones safely
    for vname in vehicles:
        try:
            client.landAsync(vehicle_name=vname).join()
            client.armDisarm(False, vehicle_name=vname)
            client.enableApiControl(False, vehicle_name=vname)
        except Exception:
            pass


async def main():
    await run_single_drone_timeline()
    await asyncio.sleep(2.0)
    await run_4drone_timeline()


if __name__ == "__main__":
    asyncio.run(main())
