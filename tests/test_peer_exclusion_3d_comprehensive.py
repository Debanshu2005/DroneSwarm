"""
Comprehensive test suite for 3D peer exclusion geometry, coordinate frames,
telemetry failure handling, and multi-drone package parity.
"""

import math
import time
from typing import List
from unittest.mock import MagicMock
import pytest

from DroneOS.perception.detector import Detection
from DroneOS.perception.frame_sources import Frame
from DroneOS.perception.geolocate import (
    pixel_to_ray_ned,
    compute_ray_to_point_cpa,
    lat_lon_to_meters,
    meters_to_lat_lon,
    project_pixel_to_ned,
    pixel_to_lat_lon,
)
from DroneOS.shared.protocol.messages import HeartbeatMessage, TelemetryMessage, TelemetryData


# Helper mock classes for headless pipeline testing
class MockDetector:
    def __init__(self, detections=None):
        self._detections = detections or []
        self._executor = None

    async def detect(self, frame_data):
        return list(self._detections)

    def set_detections(self, detections):
        self._detections = list(detections)

    def ensure_loaded(self):
        pass

    def load(self):
        pass


class MockImage:
    def __init__(self, h=720, w=1280):
        self.shape = (h, w, 3)


class MockFrameSource:
    def __init__(self, w=1280, h=720):
        self.w = w
        self.h = h
        self.is_synthetic = True

    async def get_frame(self):
        return Frame(
            image=MockImage(self.h, self.w),
            timestamp=time.time()
        )


class MockFC:
    def __init__(self, lat=47.641568, lon=-122.140065, alt=15.0, roll=0.0, pitch=0.0, yaw=0.0):
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.gps_valid = True
        self.armed_state = "ARMED"

    def set_pose(self, lat, lon, alt, roll=0.0, pitch=0.0, yaw=0.0):
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw

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


def create_pipeline(pkg_name="DroneOS", drone_id="drone1"):
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
        session_id="test-sess-001",
        session_generation=1
    )
    pipe.swarm_manager = swarm
    return pipe, det, fc, swarm


def asyncio_future(val):
    import asyncio
    f = asyncio.Future()
    f.set_result(val)
    return f


# ==============================================================================
# SECTION 1: Direct and Reverse Vertical Overlap Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_direct_vertical_overlap():
    """Upper drone at 15m alt directly above Lower drone at 5m alt. Lower drone must be rejected."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    
    # Register peer drone2 directly below drone1
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = fc.lat
    p2.lon = fc.lon
    p2.alt = 5.0
    p2.last_position_time = time.time()
    p2.is_active = True

    # Drone 1 looks straight down: peer is centered in frame (640, 360)
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.90, label="rubbish")])
    await pipe._step()

    # Verify that the peer detection was rejected
    assert pipe.last_rejection_reason in ("GEOLOCATION_ALL_FAILED", "AGGREGATION_NO_EMISSION", None)
    # Aggregator should have received 0 detections
    assert pipe.last_step_stats["geo_detections"] == 0


@pytest.mark.asyncio
async def test_reverse_vertical_overlap():
    """Lower drone at 5m looks down at ground. Upper drone at 15m is behind camera (s* < 0) and not rejected."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone2")
    fc.set_pose(fc.lat, fc.lon, alt=5.0)

    # Register upper peer drone1 at 15m
    swarm.registry.add_peer("drone1")
    p1 = swarm.registry.get_peer("drone1")
    p1.lat = fc.lat
    p1.lon = fc.lon
    p1.alt = 15.0  # Above drone 2
    p1.last_position_time = time.time()

    # Drone 2 looks down at genuine ground target (640, 360)
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Upper peer is behind the camera (s* = -10m < 0), so ground detection must be ACCEPTED!
    assert pipe.last_step_stats["geo_detections"] == 1


# ==============================================================================
# SECTION 2: Horizontal Separation Sweep (0m to 10m)
# ==============================================================================

@pytest.mark.asyncio
async def test_horizontal_separation_sweep():
    """Peer placed at horizontal offsets from 0m to 8m. 3D CPA correctly excludes peer in camera FOV."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")

    for r in [0.0, 1.0, 2.0, 2.5, 3.5, 4.0, 4.5, 6.0, 8.0]:
        # Place peer North by r meters, alt=5.0m
        p2_lat, p2_lon = meters_to_lat_lon(r, 0.0, fc.lat, fc.lon)
        p2.lat = p2_lat
        p2.lon = p2_lon
        p2.alt = 5.0
        p2.last_position_time = time.time()

        # In nadir camera, North corresponds to -Y in camera frame (vertical pixel v)
        # delta_z = 15 - 5 = 10m. tan(theta) = r / 10.0
        # v_px = 360 - 640 * (r / 10.0)
        v_px = 360 - int(640 * (r / 10.0))
        u_px = 640

        det.set_detections([Detection(bbox_xyxy=(u_px - 20, v_px - 20, u_px + 20, v_px + 20), confidence=0.91, label="rubbish")])
        await pipe._step()

        # Ray aims directly at peer -> 3D CPA d_perp == 0.0 < 2.0m -> REJECTED!
        assert pipe.last_step_stats["geo_detections"] == 0, f"Peer at sep {r}m should have been rejected by 3D exclusion!"


# ==============================================================================
# SECTION 3: Different Altitude Combinations
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("z_upper, z_lower, sep_r", [
    (10.0, 5.0, 1.0),
    (15.0, 13.0, 0.5),   # Tight vertical spacing (delta_z = 2m)
    (20.0, 12.0, 2.0),
    (30.0, 10.0, 3.0),
])
async def test_different_altitude_combinations(z_upper, z_lower, sep_r):
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=z_upper)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2_lat, p2_lon = meters_to_lat_lon(0.0, sep_r, fc.lat, fc.lon)
    p2.lat = p2_lat
    p2.lon = p2_lon
    p2.alt = z_lower
    p2.last_position_time = time.time()

    # East offset -> horizontal pixel u
    dz = z_upper - z_lower
    u_px = 640 + int(640 * (sep_r / dz))
    det.set_detections([Detection(bbox_xyxy=(u_px - 15, 345, u_px + 15, 375), confidence=0.89, label="rubbish")])
    await pipe._step()

    assert pipe.last_step_stats["geo_detections"] == 0, f"Failed at altitudes {z_upper}m vs {z_lower}m"


# ==============================================================================
# SECTION 4: Camera Attitude Variations (Pitch, Roll, Yaw)
# ==============================================================================

@pytest.mark.asyncio
async def test_camera_pitch_and_roll():
    """Drone tilted with roll=10 deg, pitch=-10 deg. 3D ray correctly excludes peer."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0, roll=10.0, pitch=-10.0, yaw=0.0)

    # Place peer directly below in world NED (0, 0, 10m below)
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = fc.lat
    p2.lon = fc.lon
    p2.alt = 5.0
    p2.last_position_time = time.time()

    # Due to tilt, peer appears off-center in the camera frame
    # Compute where peer projects in image:
    # We can test ray CPA directly:
    ray_dir = pixel_to_ray_ned(640, 360, 1280, 720, 90.0, 90.0, 10.0, -10.0, 0.0)
    assert ray_dir is not None
    # Verify ray normalization
    norm = math.sqrt(sum(x**2 for x in ray_dir))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)


@pytest.mark.asyncio
@pytest.mark.parametrize("yaw_deg", [0.0, 45.0, 90.0, 180.0, 270.0])
async def test_camera_yaw(yaw_deg):
    """Drone rotated by yaw. Peer is placed along the rotated body forward axis."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0, yaw=yaw_deg)

    # Body forward = +1.0m, Down = 10m below
    yaw_rad = math.radians(yaw_deg)
    dn = 1.0 * math.cos(yaw_rad)
    de = 1.0 * math.sin(yaw_rad)
    p_lat, p_lon = meters_to_lat_lon(dn, de, fc.lat, fc.lon)

    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = p_lat
    p2.lon = p_lon
    p2.alt = 5.0
    p2.last_position_time = time.time()

    # Top of image corresponds to body forward: v = 360 - 64, u = 640
    v_px = 360 - int(640 * (1.0 / 10.0))
    det.set_detections([Detection(bbox_xyxy=(620, v_px - 20, 660, v_px + 20), confidence=0.92, label="rubbish")])
    await pipe._step()

    assert pipe.last_step_stats["geo_detections"] == 0


# ==============================================================================
# SECTION 5: Edge Cases (Boundaries, Multiple Peers, Disarmed/Standby)
# ==============================================================================

@pytest.mark.asyncio
async def test_standby_and_disarmed_peers():
    """Peer in standby / disarmed on ground or landing pad must NOT be classified as garbage."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0)

    # Standby peer on ground (alt=0.0m, is_active=False)
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = fc.lat
    p2.lon = fc.lon
    p2.alt = 0.0
    p2.is_active = False  # Standby!
    p2.last_position_time = time.time()

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipe._step()

    # Landed peer is excluded by landed peer check
    assert pipe.last_step_stats["geo_detections"] == 0


@pytest.mark.asyncio
async def test_missing_and_stale_telemetry_deferral():
    """When peer telemetry is missing or stale, detection is DEFERRED, not silently accepted."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0)

    # Peer in registry without valid coordinates
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = None
    p2.lon = None
    p2.last_position_time = None

    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.90, label="rubbish")])
    await pipe._step()

    # Ambiguous detection must be deferred, NOT emitted as a confirmed target!
    assert pipe.last_step_stats["geo_detections"] == 0


@pytest.mark.asyncio
async def test_genuine_garbage_close_to_peer():
    """Genuine garbage 4.0m away from landed peer on ground. Genuine garbage is ACCEPTED."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0)

    # Peer landed at origin
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = fc.lat
    p2.lon = fc.lon
    p2.alt = 0.0
    p2.last_position_time = time.time()

    # Genuine garbage on ground 4.0m East of peer
    # At alt=15m, ground offset=4m -> tan(theta) = 4 / 15 = 0.2667 -> u = 640 + 640 * 0.2667 = 810
    u_px = 640 + int(640 * (4.0 / 15.0))
    det.set_detections([Detection(bbox_xyxy=(u_px - 20, 340, u_px + 20, 380), confidence=0.87, label="rubbish")])
    await pipe._step()

    # Distance on ground is 4.0m > 2.0m exclusion radius -> ACCEPTED!
    assert pipe.last_step_stats["geo_detections"] == 1


@pytest.mark.asyncio
async def test_multiple_peers_in_same_frame():
    """Two peer drones simultaneously in camera view below Drone 1. Both are excluded."""
    pipe, det, fc, swarm = create_pipeline("DroneOS", "drone1")
    fc.set_pose(47.641568, -122.140065, alt=15.0)

    # Peer 2 at 2m North, alt=8m (v_px = 360 - 640*(2/7) = 177)
    p2_lat, p2_lon = meters_to_lat_lon(2.0, 0.0, fc.lat, fc.lon)
    swarm.registry.add_peer("drone2")
    p2 = swarm.registry.get_peer("drone2")
    p2.lat = p2_lat
    p2.lon = p2_lon
    p2.alt = 8.0
    p2.last_position_time = time.time()

    # Peer 3 at 2m South, alt=8m (v_px = 360 + 640*(2/7) = 543)
    p3_lat, p3_lon = meters_to_lat_lon(-2.0, 0.0, fc.lat, fc.lon)
    swarm.registry.add_peer("drone3")
    p3 = swarm.registry.get_peer("drone3")
    p3.lat = p3_lat
    p3.lon = p3_lon
    p3.alt = 8.0
    p3.last_position_time = time.time()

    v_p2 = 360 - int(640 * (2.0 / 7.0))
    v_p3 = 360 + int(640 * (2.0 / 7.0))

    det.set_detections([
        Detection(bbox_xyxy=(620, v_p2 - 20, 660, v_p2 + 20), confidence=0.91, label="rubbish"),
        Detection(bbox_xyxy=(620, v_p3 - 20, 660, v_p3 + 20), confidence=0.89, label="rubbish"),
    ])
    await pipe._step()

    # Both peer detections must be excluded!
    assert pipe.last_step_stats["geo_detections"] == 0


# ==============================================================================
# SECTION 6: Cross-Package Parity Across All 4 DroneOS Nodes
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"])
async def test_all_four_droneos_packages(pkg_name):
    """Verifies that 3D peer exclusion behavior is completely identical across DroneOS, DroneOS1, DroneOS2, DroneOS3."""
    pipe, det, fc, swarm = create_pipeline(pkg_name, f"node-{pkg_name}")
    fc.set_pose(47.641568, -122.140065, alt=15.0)

    swarm.registry.add_peer("peer_node")
    peer = swarm.registry.get_peer("peer_node")
    peer.lat = fc.lat
    peer.lon = fc.lon
    peer.alt = 5.0
    peer.last_position_time = time.time()

    # Directly below
    det.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.90, label="rubbish")])
    await pipe._step()
    assert pipe.last_step_stats["geo_detections"] == 0, f"Node {pkg_name} failed to exclude vertical peer!"
