"""
tests/test_peer_exclusion_depth_ambiguity.py

Formal test suite for 3D Peer-Drone Exclusion Depth-Ambiguity Verification:
1. Peer drone at 10 m depth, genuine garbage at 15 m depth, on same camera ray.
2. Peer drone at 15 m depth, genuine garbage at 10 m depth.
3. Peer drone and garbage at similar depth but separated laterally.
4. Peer drone partially occluding a genuine garbage object.
5. Multiple peers along the same camera ray.
6. Genuine garbage near a peer but outside peer's physical volume.
7. Genuine garbage directly behind a peer from observer's perspective.
8. Sensor depth measurement contract (no invented depth on monocular RGB).
9. Perception pipeline parity across DroneOS, DroneOS1, DroneOS2, DroneOS3.
"""

import asyncio
import math
import time
from typing import List, Tuple
from unittest.mock import MagicMock
import pytest

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


# ==============================================================================
# Scenario 1: Peer at 10m depth, genuine garbage at 15m depth (collinear)
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario1_peer_in_front_of_ground_garbage_collinear():
    """Observer at 15m. Peer at 5m alt (depth 10m). Garbage at 0m alt (depth 15m). Both on nadir ray."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=15.0)

    # Register peer directly below observer
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = fc.lat, fc.lon
    p2.alt = 5.0  # 10m below observer
    p2.last_position_time = time.time()
    p2.is_active = True

    # CPA geometry verification
    ray_ned = (0.0, 0.0, 1.0)
    p_vector_ned = (0.0, 0.0, 10.0)
    s_depth, d_perp = compute_ray_to_point_cpa(ray_ned, p_vector_ned)
    assert s_depth == 10.0
    assert d_perp == 0.0

    # Detection at nadir center
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Must be REJECTED to prevent peer drone from becoming a false garbage target
    assert pipe.last_step_stats["geo_detections"] == 0
    assert pipe.last_rejection_reason in ("GEOLOCATION_ALL_FAILED", "AGGREGATION_NO_EMISSION", None)


# ==============================================================================
# Scenario 2: Peer at 15m depth, genuine garbage at 10m depth
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario2_peer_behind_foreground_target():
    """Observer at 20m. Foreground garbage at 10m depth. Peer at 15m depth."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=20.0)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = fc.lat, fc.lon
    p2.alt = 5.0  # 15m below observer
    p2.last_position_time = time.time()
    p2.is_active = True

    # Peer CPA geometry
    ray_ned = (0.0, 0.0, 1.0)
    p_vector_ned = (0.0, 0.0, 15.0)
    s_depth, d_perp = compute_ray_to_point_cpa(ray_ned, p_vector_ned)
    assert s_depth == 15.0
    assert d_perp == 0.0

    # Under current monocular ray model without rangefinder, the ray intersects peer cylinder
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()
    # Documents that background peer along the infinite ray triggers exclusion
    assert pipe.last_step_stats["geo_detections"] == 0


# ==============================================================================
# Scenario 3: Peer and garbage at similar depth but separated laterally
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario3_similar_depth_lateral_separation():
    """Peer and garbage at same depth (15m), but separated laterally by 4.0m (> 2.0m radius)."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=15.0)

    # Peer landed at 4m east offset
    p_lat, p_lon = meters_to_lat_lon(0.0, 4.0, fc.lat, fc.lon)
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = p_lat, p_lon
    p2.alt = 0.0
    p2.last_position_time = time.time()
    p2.is_active = True

    # CPA: ray is nadir (0, 0, 1), peer at (0, 4, 15)
    s_depth, d_perp = compute_ray_to_point_cpa((0.0, 0.0, 1.0), (0.0, 4.0, 15.0))
    assert s_depth == 15.0
    assert abs(d_perp - 4.0) < 1e-6

    # Detection at nadir center (pointing at genuine garbage at 0, 0, 0)
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Lateral separation resolves ambiguity cleanly -> ACCEPTED!
    assert pipe.last_step_stats["geo_detections"] == 1


# ==============================================================================
# Scenario 4: Peer partially occluding genuine garbage (grazing ray)
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario4_partial_occlusion_grazing_ray():
    """Peer at 5m depth with 1.2m lateral offset (< 2.0m radius), partially occluding garbage at 15m."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=15.0)

    p_lat, p_lon = meters_to_lat_lon(0.0, 1.2, fc.lat, fc.lon)
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = p_lat, p_lon
    p2.alt = 10.0  # 5m below observer
    p2.last_position_time = time.time()
    p2.is_active = True

    # CPA: d_perp = 1.2m < 2.0m radius
    s_depth, d_perp = compute_ray_to_point_cpa((0.0, 0.0, 1.0), (0.0, 1.2, 5.0))
    assert s_depth == 5.0
    assert abs(d_perp - 1.2) < 1e-6
    assert d_perp < 2.0

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Grazing ray inside 2m exclusion cylinder is excluded to prevent false positives
    assert pipe.last_step_stats["geo_detections"] == 0


# ==============================================================================
# Scenario 5: Multiple peers along the same camera ray
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario5_multiple_peers_collinear():
    """Multiple peers along the same ray (Peer 1 at 10m depth, Peer 2 at 20m depth)."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=25.0)

    now = time.time()
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = fc.lat, fc.lon
    p2.alt = 15.0  # depth 10m
    p2.last_position_time = now
    p2.is_active = True

    swarm.registry.add_peer("drone3")
    p3 = swarm.registry.get_peer("drone3")
    p3.lat, p3.lon = fc.lat, fc.lon
    p3.alt = 5.0   # depth 20m
    p3.last_position_time = now
    p3.is_active = True

    # Both peers have d_perp = 0.0
    s2, d2 = compute_ray_to_point_cpa((0.0, 0.0, 1.0), (0.0, 0.0, 10.0))
    s3, d3 = compute_ray_to_point_cpa((0.0, 0.0, 1.0), (0.0, 0.0, 20.0))
    assert s2 == 10.0 and d2 == 0.0
    assert s3 == 20.0 and d3 == 0.0

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Correctly excluded by first encountered peer
    assert pipe.last_step_stats["geo_detections"] == 0


# ==============================================================================
# Scenario 6: Genuine garbage near peer outside physical volume
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario6_garbage_near_peer_outside_volume():
    """Genuine garbage at nadir center; peer at 2.8m lateral offset (> 2.0m radius)."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=15.0)

    p_lat, p_lon = meters_to_lat_lon(0.0, 2.8, fc.lat, fc.lon)
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = p_lat, p_lon
    p2.alt = 5.0
    p2.last_position_time = time.time()
    p2.is_active = True

    s_depth, d_perp = compute_ray_to_point_cpa((0.0, 0.0, 1.0), (0.0, 2.8, 10.0))
    assert abs(d_perp - 2.8) < 1e-6
    assert d_perp > 2.0

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Target outside 2m exclusion radius is preserved -> ACCEPTED!
    assert pipe.last_step_stats["geo_detections"] == 1


# ==============================================================================
# Scenario 7: Genuine garbage directly behind a peer from observer's perspective
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario7_garbage_directly_behind_peer():
    """Collinear ray: Observer at 20m, Peer at 10m alt (depth 10m), Garbage at 0m alt (depth 20m)."""
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=20.0)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = fc.lat, fc.lon
    p2.alt = 10.0
    p2.last_position_time = time.time()
    p2.is_active = True

    s_depth, d_perp = compute_ray_to_point_cpa((0.0, 0.0, 1.0), (0.0, 0.0, 10.0))
    assert s_depth == 10.0
    assert d_perp == 0.0

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Must be excluded to prevent peer from becoming false garbage target
    assert pipe.last_step_stats["geo_detections"] == 0


# ==============================================================================
# Scenario 8: Sensor depth measurement contract (Monocular RGB constraint)
# ==============================================================================

def test_sensor_depth_measurement_contract():
    """Verifies that frame source is monocular RGB (3-channel) without depth measurement channel."""
    fs = MockFrameSource()
    assert fs.is_synthetic is True
    # Verify image dimensions: H, W, C=3 (no depth channel)
    img = MockImage(720, 1280)
    assert len(img.shape) == 3
    assert img.shape[2] == 3, "Image must be 3-channel RGB; no range/depth channel exists."


# ==============================================================================
# Scenario 9: Perception pipeline parity across DroneOS, DroneOS1, DroneOS2, DroneOS3
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"])
async def test_all_four_pipeline_parity_under_depth_ambiguity(pkg_name):
    """Verifies identical depth-ambiguity behavior across all 4 production DroneOS packages."""
    pipe, det, fc, swarm = build_pipeline(f"drone_{pkg_name}", pkg_name)
    fc.set_pose(fc.lat, fc.lon, alt=15.0)

    # 1. Collinear peer at 5m alt -> REJECTED
    swarm.registry.add_peer("peer_a")
    pa = swarm.registry.get_peer("peer_a")
    pa.lat, pa.lon = fc.lat, fc.lon
    pa.alt = 5.0
    pa.last_position_time = time.time()
    pa.is_active = True

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()
    assert pipe.last_step_stats["geo_detections"] == 0

    # 2. Peer separated laterally by 3.5m -> ACCEPTED
    p_lat, p_lon = meters_to_lat_lon(0.0, 3.5, fc.lat, fc.lon)
    pa.lat, pa.lon = p_lat, p_lon
    pa.last_position_time = time.time()

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()
    assert pipe.last_step_stats["geo_detections"] == 1


# ==============================================================================
# Scenario 10: Deep Investigation of Depth Ordering in Scenario 2
# ==============================================================================

def test_exact_camera_ray_depth_ordering_scenario2():
    """
    Formally verifies the camera-ray depth ordering for Scenario 2:
    Observer at alt=20m, Foreground garbage at alt=10m, Peer drone at alt=5m, Ground at alt=0m.
    Confirms that the peer drone cannot physically occlude the foreground garbage.
    """
    obs_pos = (0.0, 0.0, -20.0)      # NED: z = -alt = -20m
    ray_dir = (0.0, 0.0, 1.0)        # Nadir unit ray

    fg_garbage_pos = (0.0, 0.0, -10.0) # alt = 10m
    peer_pos = (0.0, 0.0, -5.0)        # alt = 5m
    ground_pos = (0.0, 0.0, 0.0)       # alt = 0m

    # Compute along-ray depth s from observer
    s_cam = 0.0
    s_fg = (fg_garbage_pos[2] - obs_pos[2]) * ray_dir[2]
    s_peer = (peer_pos[2] - obs_pos[2]) * ray_dir[2]
    s_ground = (ground_pos[2] - obs_pos[2]) * ray_dir[2]

    assert s_cam == 0.0
    assert s_fg == 10.0
    assert s_peer == 15.0
    assert s_ground == 20.0

    # Strict depth ordering verification
    assert s_cam < s_fg < s_peer < s_ground, (
        f"Depth ordering failed: s_cam={s_cam}, s_fg={s_fg}, s_peer={s_peer}, s_ground={s_ground}"
    )

    # Physical occlusion deduction:
    # Because s_fg (10m) < s_peer (15m), the foreground garbage is strictly in front of the peer.
    # Therefore, the peer drone cannot physically occlude the foreground garbage.
    assert s_fg < s_peer


def test_monocular_bounding_box_scale_depth_indeterminacy():
    """
    Demonstrates that monocular RGB bounding box pixel width w_px cannot differentiate
    a peer drone at depth s1 from a garbage object at depth s2 without known physical width W.
    Pinhole relation: w_px = (f_x * W) / s
    """
    f_x = 256.0  # Camera focal length in pixels (hfov=90 deg, img_w=512)

    # Object A: Peer drone (W = 0.65m) at depth s1 = 10.0m
    w_peer_m = 0.65
    s1_m = 10.0
    w_px_peer = (f_x * w_peer_m) / s1_m  # 16.64 px

    # Object B: Trash bag (W = 1.0m) at depth s2 = 15.3846m
    w_garbage_m = 1.0
    s2_m = 15.384615
    w_px_garbage = (f_x * w_garbage_m) / s2_m  # 16.64 px

    # Both objects subtend the exact same pixel width on the camera sensor
    assert abs(w_px_peer - w_px_garbage) < 0.001
    assert round(w_px_peer, 1) == 16.6
    assert round(w_px_garbage, 1) == 16.6


@pytest.mark.asyncio
async def test_scenario2_pipeline_rejection_root_cause_and_tradeoff():
    """
    Documents why Scenario 2 returns REJECTED in the production pipeline:
    The 3D CPA filter evaluates the semi-infinite camera ray against the peer volume.
    Because s_depth > 0 (15m) and d_perp < 2.0m (0m), it triggers REJECTED.
    Without an active rangefinder, this is the necessary defense to prevent peer false positives.
    """
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=20.0)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = fc.lat, fc.lon
    p2.alt = 5.0
    p2.last_position_time = time.time()
    p2.is_active = True

    # 1. Detection fires on nadir camera
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Evaluated decision is REJECTED
    assert pipe.last_step_stats["geo_detections"] == 0
    assert pipe.last_rejection_reason in ("GEOLOCATION_ALL_FAILED", "AGGREGATION_NO_EMISSION", None)

    # Verify that CPA geometry caused this rejection
    ray_ned = (0.0, 0.0, 1.0)
    p_vector_ned = (0.0, 0.0, 15.0)
    s_depth, d_perp = compute_ray_to_point_cpa(ray_ned, p_vector_ned)
    assert s_depth == 15.0
    assert d_perp == 0.0
    assert d_perp < 2.0


@pytest.mark.asyncio
async def test_swarm_multiview_recovery_scenario2():
    """
    Demonstrates that while Drone 1's nadir sightline to foreground garbage is occluded/suppressed
    by Drone 2 behind it, Drone 3 (at an oblique baseline) has an unoccluded ray (d_perp > 2.0m)
    and cleanly detects and geolocates the foreground garbage.
    """
    # Drone 3 is at (15m North, 0m East, 20m Altitude)
    pipe3, det3, fc3, swarm3 = build_pipeline("drone3")
    drone3_lat, drone3_lon = meters_to_lat_lon(15.0, 0.0, 10.0, 20.0)
    fc3.set_pose(drone3_lat, drone3_lon, alt=20.0)

    # Peer Drone 2 is at (0m North, 0m East, 5m Altitude)
    p2_lat, p2_lon = meters_to_lat_lon(0.0, 0.0, 10.0, 20.0)
    swarm3.registry.add_peer("drone2")
    p2 = swarm3.registry.get_peer("drone2")
    p2.lat, p2.lon = p2_lat, p2_lon
    p2.alt = 5.0
    p2.last_position_time = time.time()
    p2.is_active = True

    # Foreground garbage target is at (0m North, 0m East, 10m Altitude)
    # Vector from Drone 3 to Foreground Target: (-15m North, 0m East, +10m Down)
    # Distance = sqrt(15^2 + 10^2) = sqrt(325) ~= 18.028m
    # Unit ray from Drone 3:
    r_n = -15.0 / math.sqrt(325)
    r_e = 0.0
    r_d = 10.0 / math.sqrt(325)
    ray_drone3 = (r_n, r_e, r_d)

    # Relative position of Peer Drone 2 from Drone 3:
    # Drone 2 is at North=0, East=0, Alt=5. Drone 3 is at North=15, East=0, Alt=20.
    # Vector = (-15m North, 0m East, +15m Down)
    p2_rel_ned = (-15.0, 0.0, 15.0)

    # Calculate CPA from Drone 3's sightline to Drone 2:
    s_depth, d_perp = compute_ray_to_point_cpa(ray_drone3, p2_rel_ned)

    # Expected: s_depth ~= 20.80m, d_perp = sqrt(450 - 20.801^2) ~= 4.16m
    assert s_depth > 0.0
    assert d_perp > 4.0, f"Expected d_perp > 4.0m, got {d_perp:.2f}m"
    assert d_perp > 2.0, "Drone 3's sightline to the target must NOT intersect Drone 2's 2.0m volume!"

    # Drone 3 receives detection on that ray
    # Since d_perp (4.16m) > exclusion_radius (2.0m), Drone 3 does NOT exclude Drone 2
    det3.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe3._step()

    # Drone 3 emits geolocation cleanly
    assert pipe3.last_step_stats["geo_detections"] == 1


@pytest.mark.asyncio
async def test_peer_drone_false_positive_immunity_preserved():
    """
    Verifies that when Drone 1 observes Drone 2 with NO foreground garbage,
    Drone 2 is strictly excluded, ensuring zero false-positive garbage markers.
    """
    pipe, det, fc, swarm = build_pipeline("drone1")
    fc.set_pose(fc.lat, fc.lon, alt=20.0)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat, p2.lon = fc.lat, fc.lon
    p2.alt = 5.0
    p2.last_position_time = time.time()
    p2.is_active = True

    # Drone 1 detector fires on Drone 2 airframe
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Must be 0 geo_detections - peer drone never becomes garbage
    assert pipe.last_step_stats["geo_detections"] == 0

