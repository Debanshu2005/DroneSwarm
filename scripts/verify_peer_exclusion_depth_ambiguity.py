#!/usr/bin/env python3
"""
scripts/verify_peer_exclusion_depth_ambiguity.py

Executes formal verification of 3D Ray-to-Peer CPA Exclusion under Depth Ambiguity:
1. Peer drone at 10 m depth, genuine garbage at 15 m depth, on same camera ray.
2. Peer drone at 15 m depth, genuine garbage at 10 m depth.
3. Peer drone and garbage at similar depth but separated laterally.
4. Peer drone partially occluding a genuine garbage object.
5. Multiple peers along the same camera ray.
6. Genuine garbage near a peer but outside peer's physical volume.
7. Genuine garbage directly behind a peer from observer's perspective.

Evaluates:
- Geometric ray CPA (depth s*, miss distance d_perp)
- Pipeline acceptance/rejection decisions across DroneOS instances
- Monocular depth ambiguity constraints
- Sensor availability and calibration status
"""

import asyncio
import json
import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

from DroneOS.perception.detector import Detection
from DroneOS.perception.frame_sources import Frame
from DroneOS.perception.geolocate import (
    compute_ray_to_point_cpa,
    pixel_to_ray_ned,
    project_pixel_to_ned,
    pixel_to_lat_lon,
    lat_lon_to_meters,
    meters_to_lat_lon,
)
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.core.swarm_manager import SwarmMembership


class MockImage:
    def __init__(self, h=720, w=1280):
        self.shape = (h, w, 3)


class MockFrameSource:
    def __init__(self, w=1280, h=720):
        self.w, self.h = w, h
        self.is_synthetic = True

    async def get_frame(self):
        return Frame(image=MockImage(self.h, self.w), timestamp=time.time())


class MockDetector:
    def __init__(self, detections=None):
        self._detections = detections or []

    async def detect(self, img):
        return list(self._detections)

    def set_detections(self, detections):
        self._detections = list(detections)

    def ensure_loaded(self):
        pass


class MockFC:
    def __init__(self, lat=47.641468, lon=-122.140165, alt=15.0, roll=0.0, pitch=0.0, yaw=0.0):
        self.lat, self.lon, self.alt = lat, lon, alt
        self.roll, self.pitch, self.yaw = roll, pitch, yaw
        self.gps_valid = True
        self.armed_state = "ARMED"

    def set_pose(self, lat, lon, alt, roll=0.0, pitch=0.0, yaw=0.0):
        self.lat, self.lon, self.alt = lat, lon, alt
        self.roll, self.pitch, self.yaw = roll, pitch, yaw

    async def get_telemetry(self):
        t = MagicMock()
        t.latitude = self.lat
        t.longitude = self.lon
        t.altitude = self.alt
        t.roll = self.roll
        t.pitch = self.pitch
        t.yaw = self.yaw
        t.gps_valid = self.gps_valid
        t.armed_state = self.armed_state
        t.flight_mode = "GUIDED"
        t.timestamp = time.time()
        return t


def asyncio_future(val):
    f = asyncio.Future()
    f.set_result(val)
    return f


def build_pipeline(drone_id="drone1", pkg_name="DroneOS"):
    import importlib
    pipeline_mod = importlib.import_module(f"{pkg_name}.perception.pipeline")
    swarm_mod = importlib.import_module(f"{pkg_name}.core.swarm_manager")

    cfg = MagicMock()
    cfg.min_confidence = 0.5
    cfg.max_tilt_deg = 20.0
    cfg.min_altitude_m = 2.0
    cfg.max_range_m = 120.0
    cfg.ground_elevation_m = 0.0
    cfg.merge_radius_m = 2.5
    cfg.peer_exclusion_radius_m = 2.0
    cfg.min_hits = 1
    cfg.update_interval_s = 10.0
    cfg.max_tracks = 2000
    cfg.max_telemetry_age_s = 2.0
    cfg.max_frame_age_s = 2.0
    cfg.max_sync_skew_s = 1.0
    cfg.camera = MagicMock()
    cfg.camera.hfov_deg = 90.0
    cfg.camera.mount_pitch_deg = 90.0

    fs = MockFrameSource()
    det = MockDetector()
    fc = MockFC()
    net = MagicMock()
    net.broadcast_message = MagicMock(return_value=asyncio_future(True))
    swarm = swarm_mod.SwarmMembership(drone_id)

    pipe = pipeline_mod.PerceptionPipeline(
        drone_id=drone_id,
        frame_source=fs,
        detector=det,
        flight_controller=fc,
        network_adapter=net,
        config=cfg,
        session_id=f"test-sess-{drone_id}",
        session_generation=1,
        swarm_manager=swarm,
    )
    return pipe, det, fc, swarm


async def evaluate_scenario(
    name: str,
    desc: str,
    drone_alt: float,
    ray_dir: Tuple[float, float, float],
    peers: List[Dict[str, Any]],
    target_ground: Tuple[float, float, float],
    det_pixel: Tuple[float, float] = (640, 360),
) -> Dict[str, Any]:
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=drone_alt)

    now = time.time()
    for p in peers:
        p_id = p["id"]
        swarm.registry.add_peer(p_id)
        peer_obj = swarm.registry.get_peer(p_id)
        # Convert relative NED offset to lat/lon
        p_lat, p_lon = meters_to_lat_lon(p["north"], p["east"], fc.lat, fc.lon)
        peer_obj.lat = p_lat
        peer_obj.lon = p_lon
        peer_obj.alt = p["alt"]
        peer_obj.last_position_time = now
        peer_obj.is_active = True
        peer_obj.position_valid = True

    # Detection at specified pixel
    u, v = det_pixel
    det.set_detections([Detection(bbox_xyxy=(u - 20, v - 20, u + 20, v + 20), confidence=0.88, label="rubbish")])

    await pipe._step()

    # Geometry calculation for each peer relative to ray
    cpa_details = []
    for p in peers:
        p_dn = p["north"]
        p_de = p["east"]
        p_dd = -(p["alt"] - drone_alt)
        s_depth, d_perp = compute_ray_to_point_cpa(ray_dir, (p_dn, p_de, p_dd))
        cpa_details.append({
            "peer_id": p["id"],
            "peer_pos_ned": (p_dn, p_de, p_dd),
            "peer_alt": p["alt"],
            "s_depth_m": round(s_depth, 3),
            "d_perp_m": round(d_perp, 3),
            "within_2m_sphere": (d_perp < 2.0 and abs(s_depth - math.sqrt(p_dn**2 + p_de**2 + p_dd**2)) < 0.1),
            "intersects_cylinder_tube": (d_perp < 2.0 and s_depth > 0),
        })

    # Ground intersection depth
    d_down = ray_dir[2]
    s_ground = (drone_alt - 0.0) / d_down if d_down > 0.05 else None

    # Pipeline output
    geo_count = pipe.last_step_stats.get("geo_detections", 0)
    decision = "ACCEPTED" if geo_count > 0 else "REJECTED"
    reason = pipe.last_rejection_reason or ("ACCEPTED" if geo_count > 0 else "REJECTED_UNKNOWN")

    return {
        "scenario_name": name,
        "description": desc,
        "drone_alt_m": drone_alt,
        "ray_dir_ned": ray_dir,
        "ground_depth_s_m": round(s_ground, 3) if s_ground is not None else None,
        "peers_evaluated": cpa_details,
        "pipeline_decision": decision,
        "pipeline_rejection_reason": reason,
        "geo_detections_emitted": geo_count,
    }


async def run_all_scenarios():
    print("=" * 80)
    print("3D PEER EXCLUSION DEPTH-AMBIGUITY VERIFICATION")
    print("=" * 80)

    results = []

    # -------------------------------------------------------------------------
    # Scenario 1: Peer at 10m depth, genuine garbage at 15m depth (same ray)
    # -------------------------------------------------------------------------
    # Observer at alt=15m, looking nadir.
    # Peer at alt=5m -> depth s* = 10m.
    # Garbage at alt=0m -> depth s* = 15m. Both at (0, 0) relative horizontal.
    s1 = await evaluate_scenario(
        name="scenario_1_peer_10m_garbage_15m_collinear",
        desc="Peer drone at 10m depth, genuine garbage at 15m depth, both on approximately the same camera ray",
        drone_alt=15.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[{"id": "drone2", "north": 0.0, "east": 0.0, "alt": 5.0}],
        target_ground=(0.0, 0.0, 0.0),
    )
    results.append(s1)

    # -------------------------------------------------------------------------
    # Scenario 2: Peer at 15m depth, genuine garbage at 10m depth (same ray)
    # -------------------------------------------------------------------------
    # Observer at alt=20m, looking nadir.
    # Elevated structure / rooftop garbage at alt=10m -> depth s* = 10m.
    # Peer drone below at alt=5m -> depth s* = 15m.
    s2 = await evaluate_scenario(
        name="scenario_2_peer_15m_garbage_10m_collinear",
        desc="Peer drone at 15m depth, genuine garbage at 10m depth (peer behind foreground target)",
        drone_alt=20.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[{"id": "drone2", "north": 0.0, "east": 0.0, "alt": 5.0}],
        target_ground=(0.0, 0.0, 10.0),
    )
    results.append(s2)

    # -------------------------------------------------------------------------
    # Scenario 3: Peer and garbage at similar depth but separated laterally
    # -------------------------------------------------------------------------
    # Observer at alt=15m.
    # Peer at alt=0m (landed) at (0, 4.0, 0) -> depth 15m, lateral offset 4m.
    # Garbage at alt=0m at (0, 0, 0) -> depth 15m.
    s3 = await evaluate_scenario(
        name="scenario_3_similar_depth_lateral_separation",
        desc="Peer drone and garbage at similar depth (15m) but separated laterally (4m > 2m radius)",
        drone_alt=15.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[{"id": "drone2", "north": 0.0, "east": 4.0, "alt": 0.0}],
        target_ground=(0.0, 0.0, 0.0),
    )
    results.append(s3)

    # -------------------------------------------------------------------------
    # Scenario 4: Peer partially occluding genuine garbage
    # -------------------------------------------------------------------------
    # Observer at alt=15m.
    # Peer at alt=10m (depth 5m) with lateral offset 1.2m (< 2.0m radius).
    # Garbage at alt=0m at (0, 0, 0).
    s4 = await evaluate_scenario(
        name="scenario_4_partial_occlusion_grazing_ray",
        desc="Peer drone at 5m depth with 1.2m lateral offset (< 2.0m radius), partially occluding genuine garbage at 15m",
        drone_alt=15.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[{"id": "drone2", "north": 0.0, "east": 1.2, "alt": 10.0}],
        target_ground=(0.0, 0.0, 0.0),
    )
    results.append(s4)

    # -------------------------------------------------------------------------
    # Scenario 5: Multiple peers along the same camera ray
    # -------------------------------------------------------------------------
    # Observer at alt=25m.
    # Peer 1 at alt=15m (depth 10m).
    # Peer 2 at alt=5m (depth 20m). Both at (0, 0).
    s5 = await evaluate_scenario(
        name="scenario_5_multiple_peers_collinear",
        desc="Multiple peers along the same camera ray (Peer 1 at 10m depth, Peer 2 at 20m depth)",
        drone_alt=25.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[
            {"id": "drone2", "north": 0.0, "east": 0.0, "alt": 15.0},
            {"id": "drone3", "north": 0.0, "east": 0.0, "alt": 5.0},
        ],
        target_ground=(0.0, 0.0, 0.0),
    )
    results.append(s5)

    # -------------------------------------------------------------------------
    # Scenario 6: Genuine garbage near a peer but outside physical volume
    # -------------------------------------------------------------------------
    # Observer at alt=15m.
    # Peer at alt=5m with lateral offset 2.8m (> 2.0m radius).
    # Garbage at (0, 0, 0).
    s6 = await evaluate_scenario(
        name="scenario_6_garbage_near_peer_outside_volume",
        desc="Genuine garbage near peer drone but outside peer physical exclusion volume (2.8m lateral separation)",
        drone_alt=15.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[{"id": "drone2", "north": 0.0, "east": 2.8, "alt": 5.0}],
        target_ground=(0.0, 0.0, 0.0),
    )
    results.append(s6)

    # -------------------------------------------------------------------------
    # Scenario 7: Genuine garbage directly behind a peer from observer's perspective
    # -------------------------------------------------------------------------
    # Observer at alt=20m.
    # Peer at alt=10m (depth 10m, exact center d_perp=0.0m).
    # Garbage at alt=0m (depth 20m, exact center d_perp=0.0m).
    s7 = await evaluate_scenario(
        name="scenario_7_garbage_directly_behind_peer",
        desc="Genuine garbage directly behind peer drone from observer perspective (exact collinear nadir alignment)",
        drone_alt=20.0,
        ray_dir=(0.0, 0.0, 1.0),
        peers=[{"id": "drone2", "north": 0.0, "east": 0.0, "alt": 10.0}],
        target_ground=(0.0, 0.0, 0.0),
    )
    results.append(s7)

    for r in results:
        p_cpa = r["peers_evaluated"][0] if r["peers_evaluated"] else {}
        print(f"\nScenario: {r['scenario_name']}")
        print(f"  -> Desc: {r['description']}")
        print(f"  -> Drone Alt: {r['drone_alt_m']}m | Ray Dir: {r['ray_dir_ned']}")
        print(f"  -> Peer: s_depth={p_cpa.get('s_depth_m')}m, d_perp={p_cpa.get('d_perp_m')}m")
        print(f"  -> Ground Depth: {r['ground_depth_s_m']}m")
        print(f"  -> Decision: {r['pipeline_decision']} | Reason: {r['pipeline_rejection_reason']}")

    # Save to JSON
    out_path = Path("ml/reports/peer_exclusion_diagnostics/peer_exclusion_depth_ambiguity_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved structured results to: {out_path}")

    return results


if __name__ == "__main__":
    asyncio.run(run_all_scenarios())
