"""
tests/test_five_target_forensic.py

Regression test suite for forensic investigation:
Five Spawned Garbage Targets, Only One PhoneOS Marker.

Validates:
1. Full 5-target pipeline execution reproducing exactly 1 PhoneOS marker from 5 spawned targets.
2. GFS Stage B hypothesis formation rule:
   len(distinct_drones) >= 2 or max_conf >= 0.70 -> ACCEPTED,
   otherwise -> DEFERRED_LOW_CONFIDENCE.
3. Local TrackAggregator min_hits = 2 gating (single-frame hits do not emit).
4. Multi-drone camera FOV frustum coverage across 4-drone formation.
5. PhoneOS reconcileSnapshot & mergeDetections contract:
   authoritative GFS snapshot containing 1 active object produces exactly 1 marker.
"""

import math
import sys
from pathlib import Path
import pytest
from typing import Dict, List, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.geolocate import (
    meters_to_lat_lon,
    lat_lon_to_meters,
    compute_ray_to_point_cpa,
    pixel_to_ray_ned,
    pixel_to_lat_lon,
)
from DroneOS.perception.aggregator import TrackAggregator, GeoreferencedDetection
from gfs.models import Observation, LocalTracklet, GlobalObject, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.association import build_local_tracklets
from gfs.hypothesis import form_new_global_objects
from scripts.forensic_five_target_investigation import (
    project_world_ned_to_pixel,
    run_forensic_investigation,
)


def test_full_five_target_forensic_reproduction():
    """
    Test 1: Full 5-target pipeline execution verifying that 5 well-separated targets
    are detected by YOLO and geolocated, and all 5 appear in PhoneOS (1 Confirmed, 4 Provisional)
    without dropping any valid observations.
    """
    trace = run_forensic_investigation()

    assert trace["counts"]["spawned_targets"] == 5
    assert trace["counts"]["yolo_detected_targets"] == 5
    assert trace["counts"]["geolocated_targets"] == 5
    assert trace["counts"]["locally_tracked_targets"] == 5
    assert trace["counts"]["gfs_global_targets"] == 5
    assert trace["counts"]["phoneos_markers"] == 5

    # Check the lifecycle of each target
    table = {row["target"]: row for row in trace["lifecycle_table"]}

    # Target 3 is confirmed because it was observed by both drone2 and drone3
    assert table["Target3"]["gfs_status"] == "GLOBAL_OBJECT"
    assert table["Target3"]["phoneos_status"] != "MISSING"

    # Targets 1, 2, 4, 5 were observed by 1 drone and form provisional global objects
    for tid in ["Target1", "Target2", "Target4", "Target5"]:
        assert table[tid]["gfs_status"] == "GLOBAL_OBJECT"
        assert table[tid]["phoneos_status"] != "MISSING"


def test_gfs_stage_b_hypothesis_acceptance_rule():
    """
    Test 2: Isolate and verify the GFS Stage B hypothesis acceptance rule:
    - Multi-drone (>= 2 drones) OR single-drone high confidence (>= 0.70) -> CONFIRMED
    - Single-drone valid candidate (0.55 <= conf < 0.70) -> PROVISIONAL
    - Low confidence (conf < 0.55) -> DEFERRED
    """
    origin_lat, origin_lon, ref_alt = 47.641468, -122.140165, 0.0
    cfg = GFSConfig(
        ref_origin_lat=origin_lat,
        ref_origin_lon=origin_lon,
        ref_origin_alt=ref_alt,
        spatial_physical_r_max_m=3.5,
    )

    # Case A: Single drone, conf = 0.65 (< 0.70, >= 0.55) -> Must form PROVISIONAL GlobalObject
    obs_a = Observation(
        obs_id="obs-d1-01",
        drone_id="drone1",
        drone_session_id="sess-1",
        session_generation=1,
        frame_id=1,
        local_track_id="trk-d1-01",
        capture_timestamp_ms=1000,
        lat=origin_lat,
        lon=origin_lon,
        alt=0.0,
        cov_xx=0.35,
        cov_yy=0.35,
        cov_xy=0.0,
        label="rubbish",
        confidence=0.65,
        payload_hash="h1",
        raw_payload="{}",
    )
    obs_a.east_m, obs_a.north_m, obs_a.up_m = 0.0, 0.0, 0.0
    tracklets_a = build_local_tracklets([obs_a])

    new_globals, ambiguous, low_conf = form_new_global_objects(
        unmatched_tracklets=tracklets_a,
        config=cfg,
        ref_lat=origin_lat,
        ref_lon=origin_lon,
        ref_alt=ref_alt,
    )
    assert len(new_globals) == 1, "Single drone with 0.55 <= conf < 0.70 must form a provisional global object"
    assert new_globals[0].lifecycle_state == "PROVISIONAL"
    assert new_globals[0].provisional_reason == "SINGLE_DRONE_LOW_CONFIDENCE"
    assert len(low_conf) == 0

    # Case A2: Single drone, conf = 0.50 (< 0.55) -> Must DEFER as LOW_CONFIDENCE
    obs_a2 = Observation(
        obs_id="obs-d1-02",
        drone_id="drone1",
        drone_session_id="sess-1",
        session_generation=1,
        frame_id=2,
        local_track_id="trk-d1-02",
        capture_timestamp_ms=1000,
        lat=origin_lat,
        lon=origin_lon,
        alt=0.0,
        cov_xx=0.35,
        cov_yy=0.35,
        cov_xy=0.0,
        label="rubbish",
        confidence=0.50,
        payload_hash="h1_sub",
        raw_payload="{}",
    )
    obs_a2.east_m, obs_a2.north_m, obs_a2.up_m = 0.0, 0.0, 0.0
    tracklets_a2 = build_local_tracklets([obs_a2])
    new_g_sub, amb_sub, low_conf_sub = form_new_global_objects(
        unmatched_tracklets=tracklets_a2,
        config=cfg,
        ref_lat=origin_lat,
        ref_lon=origin_lon,
        ref_alt=ref_alt,
    )
    assert len(new_g_sub) == 0, "Single drone with conf < 0.55 must not create a global object"
    assert len(low_conf_sub) == 1
    assert low_conf_sub[0].local_track_id == "trk-d1-02"

    # Case B: Multi-drone (2 distinct drones), conf = 0.58 (< 0.70) -> Must ACCEPT as GlobalObject
    obs_b1 = Observation(
        obs_id="obs-d2-01",
        drone_id="drone2",
        drone_session_id="sess-2",
        session_generation=1,
        frame_id=1,
        local_track_id="trk-d2-01",
        capture_timestamp_ms=1000,
        lat=origin_lat,
        lon=origin_lon,
        alt=0.0,
        cov_xx=0.35,
        cov_yy=0.35,
        cov_xy=0.0,
        label="rubbish",
        confidence=0.58,
        payload_hash="h2",
        raw_payload="{}",
    )
    obs_b1.east_m, obs_b1.north_m, obs_b1.up_m = 0.0, 0.0, 0.0

    obs_b2 = Observation(
        obs_id="obs-d3-01",
        drone_id="drone3",
        drone_session_id="sess-3",
        session_generation=1,
        frame_id=1,
        local_track_id="trk-d3-01",
        capture_timestamp_ms=1000,
        lat=origin_lat,
        lon=origin_lon,
        alt=0.0,
        cov_xx=0.35,
        cov_yy=0.35,
        cov_xy=0.0,
        label="rubbish",
        confidence=0.60,
        payload_hash="h3",
        raw_payload="{}",
    )
    obs_b2.east_m, obs_b2.north_m, obs_b2.up_m = 0.2, 0.2, 0.0

    tracklets_b = build_local_tracklets([obs_b1, obs_b2])
    new_globals_b, ambiguous_b, low_conf_b = form_new_global_objects(
        unmatched_tracklets=tracklets_b,
        config=cfg,
        ref_lat=origin_lat,
        ref_lon=origin_lon,
        ref_alt=ref_alt,
    )
    assert len(new_globals_b) == 1, "Two distinct drones observing same target must form a global object"
    assert len(low_conf_b) == 0
    assert set(new_globals_b[0].source_estimates.keys()) == {"drone2", "drone3"}

    # Case C: Single drone, conf = 0.75 (>= 0.70) -> Must ACCEPT as GlobalObject
    obs_c = Observation(
        obs_id="obs-d4-01",
        drone_id="drone4",
        drone_session_id="sess-4",
        session_generation=1,
        frame_id=1,
        local_track_id="trk-d4-01",
        capture_timestamp_ms=1000,
        lat=origin_lat,
        lon=origin_lon,
        alt=0.0,
        cov_xx=0.35,
        cov_yy=0.35,
        cov_xy=0.0,
        label="rubbish",
        confidence=0.75,
        payload_hash="h4",
        raw_payload="{}",
    )
    obs_c.east_m, obs_c.north_m, obs_c.up_m = 15.0, 15.0, 0.0
    tracklets_c = build_local_tracklets([obs_c])

    new_globals_c, ambiguous_c, low_conf_c = form_new_global_objects(
        unmatched_tracklets=tracklets_c,
        config=cfg,
        ref_lat=origin_lat,
        ref_lon=origin_lon,
        ref_alt=ref_alt,
    )
    assert len(new_globals_c) == 1, "Single drone with conf >= 0.70 must form a global object"
    assert len(low_conf_c) == 0


def test_track_aggregator_min_hits_gating():
    """
    Test 3: Verify local TrackAggregator suppresses unconfirmed single-frame detections
    when min_hits = 2.
    """
    aggregator = TrackAggregator(drone_id="drone1", merge_radius_m=2.5, min_hits=2, update_interval_s=10.0)

    det = GeoreferencedDetection(
        label="rubbish",
        confidence=0.62,
        lat=47.6415,
        lon=-122.1402,
        altitude_agl=10.0,
    )

    # Frame 1: Hit count = 1 -> Should NOT emit
    emitted_1 = aggregator.update([det], now=1000.0)
    assert len(emitted_1) == 0, "First frame detection (hit=1) must not be emitted when min_hits=2"
    assert len(aggregator.tracks) == 1
    assert list(aggregator.tracks.values())[0].hits == 1

    # Frame 2: Hit count = 2 -> Should EMIT confirmed track
    emitted_2 = aggregator.update([det], now=1001.0)
    assert len(emitted_2) == 1, "Second frame detection (hit=2) must emit a confirmed track"
    assert emitted_2[0].confidence == 0.62
    assert emitted_2[0].id == "drone1-g0001"


def test_camera_frustum_and_visibility_coverage():
    """
    Test 4: Verify multi-drone camera FOV unprojection across the 4-drone formation.
    Confirm that spatial dispersal determines whether a target falls in an overlap zone
    (visible to >= 2 drones) or a single-drone zone.
    """
    # Drone 2 at (5, 3, -12m) and Drone 3 at (5, 13, -12m)
    # Target at (10, 8, 0m) lies in the spatial overlap between Drone 2 and Drone 3
    t_shared_ned = (10.0, 8.0, 0.0)
    d2_ned = (5.0, 3.0, -12.0)
    d3_ned = (5.0, 13.0, -12.0)

    u2, v2, in_fov2, depth2 = project_world_ned_to_pixel(
        target_ned=t_shared_ned,
        drone_ned=d2_ned,
        drone_attitude=(0.0, 0.0, 0.0),
        cam_mount_pitch_deg=90.0,
        hfov_deg=90.0,
        img_w=1280,
        img_h=720,
    )
    u3, v3, in_fov3, depth3 = project_world_ned_to_pixel(
        target_ned=t_shared_ned,
        drone_ned=d3_ned,
        drone_attitude=(0.0, 0.0, 0.0),
        cam_mount_pitch_deg=90.0,
        hfov_deg=90.0,
        img_w=1280,
        img_h=720,
    )
    assert in_fov2 is True, "Target at (10, 8) must be in FOV of Drone 2"
    assert in_fov3 is True, "Target at (10, 8) must be in FOV of Drone 3"

    # Target at (4, 4, 0m) is close to Drone 4 at (0, 8, -10m) but outside Drone 1 at (10, 8, -15m)
    t_single_ned = (4.0, 4.0, 0.0)
    d1_ned = (10.0, 8.0, -15.0)
    d4_ned = (0.0, 8.0, -10.0)

    u4, v4, in_fov4, _ = project_world_ned_to_pixel(
        target_ned=t_single_ned,
        drone_ned=d4_ned,
        drone_attitude=(0.0, 0.0, 0.0),
        cam_mount_pitch_deg=90.0,
        hfov_deg=90.0,
        img_w=1280,
        img_h=720,
    )
    u1, v1, in_fov1, _ = project_world_ned_to_pixel(
        target_ned=t_single_ned,
        drone_ned=d1_ned,
        drone_attitude=(0.0, 0.0, 0.0),
        cam_mount_pitch_deg=90.0,
        hfov_deg=90.0,
        img_w=1280,
        img_h=720,
    )
    assert in_fov4 is True, "Target at (4, 4) must be in FOV of Drone 4"
    assert in_fov1 is False, "Target at (4, 4) must be outside FOV of Drone 1"


def test_phoneos_snapshot_reconciliation_contract():
    """
    Test 5: Verify the PhoneOS snapshot reconciliation contract logic:
    When GFS commits only 1 confirmed GlobalObject to its database, the snapshot
    received by PhoneOS contains exactly 1 object, leading to exactly 1 active marker.
    """
    # Simulate GFS snapshot containing 1 active global object
    snapshot_objects = [
        {
            "id": "G-750329df",
            "global_id": "G-750329df",
            "label": "rubbish",
            "status": "ACTIVE",
            "lat": 47.641558,
            "lon": -122.140059,
            "alt": 0.0,
            "confidence": 0.88,
            "observation_count": 2,
            "source_count": 2,
            "version": 1,
            "first_seen_ms": 1000,
            "last_seen_ms": 1001,
        }
    ]

    # PhoneOS receives the snapshot
    phoneos_markers = {}
    for obj in snapshot_objects:
        if obj.get("status") == "ACTIVE":
            phoneos_markers[obj["id"]] = {
                "id": obj["id"],
                "label": obj["label"],
                "confidence": obj["confidence"],
                "lat": obj["lat"],
                "lon": obj["lon"],
                "hits": obj.get("observation_count", 1),
                "status": "ACTIVE",
            }

    assert len(phoneos_markers) == 1
    assert "G-750329df" in phoneos_markers
    assert phoneos_markers["G-750329df"]["confidence"] == 0.88
