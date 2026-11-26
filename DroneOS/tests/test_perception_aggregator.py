"""Unit tests for multi-frame detection aggregator."""

import math
import pytest

from DroneOS.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS.perception.geolocate import meters_to_lat_lon


def test_aggregator_new_and_update_emission():
    """Verify new emission at min_hits, running average position, and throttled update."""
    agg = TrackAggregator(
        drone_id="drone1",
        merge_radius_m=2.5,
        min_hits=2,
        update_interval_s=10.0,
    )

    t0 = 1000.0
    # Frame 1: first sighting
    det1 = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314000, lon=87.310000, drone_lat=22.314, drone_lon=87.310)
    items1 = agg.update([det1], now=t0)
    assert len(items1) == 0  # min_hits is 2, not yet confirmed

    # Frame 2: second sighting after min_temporal_separation_s (2.0s) with drone flight displacement
    # Confidence-weighted mean: (0.8*lat1 + 0.6*lat2) / (0.8 + 0.6)
    from DroneOS.perception.geolocate import meters_to_lat_lon
    d2_lat, d2_lon = meters_to_lat_lon(3.0, 0.0, 22.314, 87.310)
    det2 = GeoreferencedDetection(label="rubbish", confidence=0.6, lat=22.314008, lon=87.310008, drone_lat=d2_lat, drone_lon=d2_lon)
    items2 = agg.update([det2], now=t0 + 2.0)
    assert len(items2) == 1
    new_item = items2[0]
    assert new_item.status == "new"
    assert new_item.id == "drone1-g0001"
    assert new_item.hits == 2
    assert new_item.confidence == 0.8  # max confidence
    expected_lat = (0.8 * 22.314000 + 0.6 * 22.314008) / 1.4
    assert pytest.approx(new_item.lat, abs=1e-6) == expected_lat

    # Frame 3 (only 2s later): sighting 3, but update_interval_s (10s) hasn't passed
    det3 = GeoreferencedDetection(label="rubbish", confidence=0.9, lat=22.314005, lon=87.310005, drone_lat=22.314, drone_lon=87.310)
    items3 = agg.update([det3], now=t0 + 4.0)
    assert len(items3) == 0  # throttled

    # Frame 4 (12s later): sighting 4, update_interval_s has passed -> emit update
    det4 = GeoreferencedDetection(label="rubbish", confidence=0.7, lat=22.314004, lon=87.310004, drone_lat=22.314, drone_lon=87.310)
    items4 = agg.update([det4], now=t0 + 16.0)
    assert len(items4) == 1
    upd_item = items4[0]
    assert upd_item.status == "update"
    assert upd_item.id == "drone1-g0001"
    assert upd_item.hits == 4
    assert upd_item.confidence == 0.9  # new max


def test_aggregator_unconfirmed_ttl_pruning():
    """Unconfirmed tracks older than 30s must be pruned."""
    agg = TrackAggregator(drone_id="drone2", min_hits=2, unconfirmed_ttl_s=30.0)
    t0 = 500.0

    # Sighting 1 (never confirmed)
    det = GeoreferencedDetection(label="rubbish", confidence=0.5, lat=22.0, lon=87.0)
    agg.update([det], now=t0)
    assert len(agg.tracks) == 1

    # Sighting at t0 + 20s: still within TTL
    agg.update([], now=t0 + 20.0)
    assert len(agg.tracks) == 1

    # Sighting at t0 + 35s: exceeds 30s TTL, pruned
    agg.update([], now=t0 + 35.0)
    assert len(agg.tracks) == 0


def test_aggregator_max_tracks_eviction():
    """Tracks beyond max_tracks must evict the oldest."""
    agg = TrackAggregator(drone_id="drone1", max_tracks=3, min_hits=1)

    for i in range(5):
        # Spaced far enough apart so they don't merge (e.g. 0.01 deg > 1 km)
        det = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.0 + i * 0.01, lon=87.0)
        agg.update([det], now=100.0 + i)

    assert len(agg.tracks) == 3
    # Tracks 0 and 1 should have been evicted; tracks 2, 3, 4 remain
    assert "drone1-g0001" not in agg.tracks
    assert "drone1-g0002" not in agg.tracks
    assert "drone1-g0003" in agg.tracks
    assert "drone1-g0004" in agg.tracks
    assert "drone1-g0005" in agg.tracks


def test_aggregator_repeated_frame_timestamp_rejection():
    """Identical frame timestamps must not increment hits or trigger false confirmation."""
    agg = TrackAggregator(drone_id="drone1", min_hits=2)
    t0 = 1000.0
    det = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314, lon=87.310, drone_lat=22.314, drone_lon=87.310)

    # Frame 1 at t0
    res1 = agg.update([det], now=t0)
    assert len(res1) == 0
    track = list(agg.tracks.values())[0]
    assert track.hits == 1

    # Identical timestamp t0 (e.g. repeated frame or zero-delta call)
    res2 = agg.update([det], now=t0)
    assert len(res2) == 0
    assert track.hits == 1  # Must not increment hits

    # Genuine subsequent frame at t0 + 2.0 with drone displacement
    d3_lat, d3_lon = meters_to_lat_lon(2.5, 0.0, 22.314, 87.310)
    det3 = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314, lon=87.310, drone_lat=d3_lat, drone_lon=d3_lon)
    res3 = agg.update([det3], now=t0 + 2.0)
    assert len(res3) == 1
    assert track.hits == 2
    assert res3[0].status == "new"


def test_detections_without_drone_coordinates_never_reach_confirmed():
    """Tracks without drone coordinates may NOT reach confirmed status on time separation alone."""
    agg = TrackAggregator(drone_id="drone1", min_hits=2, min_temporal_separation_s=2.0)
    t0 = 100.0
    # Detections without drone position
    det = GeoreferencedDetection(label="rubbish", confidence=0.85, lat=22.314, lon=87.310, drone_lat=None, drone_lon=None)

    # Frame 1
    r1 = agg.update([det], now=t0)
    assert len(r1) == 0
    track = list(agg.tracks.values())[0]
    assert track.hits == 1
    assert track.distinct_hits == 1
    assert not track.confirmed

    # Multiple subsequent frames arriving across 30 seconds
    for dt in [2.0, 5.0, 10.0, 20.0, 30.0]:
        emitted = agg.update([det], now=t0 + dt)
        assert len(emitted) == 0, f"Must not confirm on time alone at dt={dt}s without drone coordinates"
        assert not track.confirmed
        assert track.distinct_hits == 1, "distinct_hits must not increment without spatial verification"
        assert track.hits == 1, "Hits must not increment without distinct spatial verification"


def test_stationary_drone_cannot_self_confirm_in_consecutive_frames():
    """A stationary drone cannot self-confirm in two consecutive frames if dt < min_temporal_separation_s."""
    agg = TrackAggregator(drone_id="drone1", min_hits=2, min_temporal_separation_s=2.0, min_drone_displacement_m=1.5)
    t0 = 100.0
    det1 = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314, lon=87.310, drone_lat=22.314, drone_lon=87.310)
    res1 = agg.update([det1], now=t0)
    assert len(res1) == 0

    # Frame 2 arriving 1.0s later with zero drone movement (stationary)
    det2 = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314, lon=87.310, drone_lat=22.314, drone_lon=87.310)
    res2 = agg.update([det2], now=t0 + 1.0)
    assert len(res2) == 0, "Stationary drone must NOT self-confirm in consecutive frames"
    track = list(agg.tracks.values())[0]
    assert track.hits == 1, "Hits must not increment when stationary and dt < min_temporal_separation_s"
    assert not track.confirmed

    # Frame 3 arriving after min_temporal_separation_s (>= 2.0s from frame 1):
    # Still stationary -> MUST NOT self-confirm by time separation alone!
    res3 = agg.update([det2], now=t0 + 2.0)
    assert len(res3) == 0, "Stationary drone must NOT self-confirm on time separation alone"
    assert track.hits == 1
    assert not track.confirmed

    # Frame 4 arriving after drone displaces along flight path (>= 3*sigma_diff) -> confirms!
    d4_lat, d4_lon = meters_to_lat_lon(2.3, 0.0, 22.314, 87.310)
    det4 = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314, lon=87.310, drone_lat=d4_lat, drone_lon=d4_lon)
    res4 = agg.update([det4], now=t0 + 2.5)
    assert len(res4) == 1, "Drone confirms once displaced by >= 3*sigma_diff"
    assert track.hits == 2
    assert track.confirmed is True


def test_stationary_pixel_track_while_drone_moves_is_rejected_as_vehicle_artifact():
    """A track whose pixel-space centroid stays nearly fixed while the drone moves is a vehicle artifact and must not confirm."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=35.0,
    )
    t0 = 100.0
    # First hit: drone at origin (22.314000, 87.310000), shadow at ground pos 2.0m North, image pixel (679.0, 77.0)
    d0_lat, d0_lon = 22.314000, 87.310000
    s1_lat, s1_lon = meters_to_lat_lon(2.0, 0.0, d0_lat, d0_lon)
    det1 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.60,
        lat=s1_lat,
        lon=s1_lon,
        drone_lat=d0_lat,
        drone_lon=d0_lon,
        pixel_x=679.0,
        pixel_y=77.0,
    )
    res1 = agg.update([det1], now=t0)
    assert len(res1) == 0
    assert len(agg.tracks) == 1
    track = list(agg.tracks.values())[0]
    assert not track.confirmed
    assert not track.is_vehicle_artifact

    # Second hit: drone moves 1.6m North (speed ~0.8 m/s * 2s, >= min_drone_displacement_m 1.5m and < merge_radius_m 2.5m)
    # The shadow follows the drone and also moves 1.6m North on the ground (1.6m < 2.5m merge_radius -> merges into same track!)
    d2_lat, d2_lon = meters_to_lat_lon(1.6, 0.0, d0_lat, d0_lon)
    s2_lat, s2_lon = meters_to_lat_lon(1.6, 0.0, s1_lat, s1_lon)
    det2 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.62,
        lat=s2_lat,
        lon=s2_lon,
        drone_lat=d2_lat,
        drone_lon=d2_lon,
        pixel_x=679.5,
        pixel_y=77.5,
    )
    res2 = agg.update([det2], now=t0 + 2.0)
    assert len(agg.tracks) == 1, "Second detection must land on the SAME track within merge_radius_m"
    assert len(res2) == 0, "Vehicle artifact with stationary pixel centroid must NOT confirm"
    assert track.confirmed is False
    assert track.is_vehicle_artifact is True
    assert track.distinct_hits == 1, "distinct_hits must not increment for vehicle artifact"

    # Non-artifact check: a nearby genuine ground object seen while the drone moves confirms
    agg_real = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=35.0,
    )
    g_lat, g_lon = meters_to_lat_lon(1.0, 0.0, d0_lat, d0_lon)
    r1 = agg_real.update([
        GeoreferencedDetection(label="rubbish", confidence=0.80, lat=g_lat, lon=g_lon, drone_lat=d0_lat, drone_lon=d0_lon, pixel_x=640.0, pixel_y=360.0)
    ], now=t0)
    assert len(r1) == 0
    # On 2nd hit, ground object is still at g_lat, g_lon, but camera pixel shifted by 150px (> 35px)
    r2 = agg_real.update([
        GeoreferencedDetection(label="rubbish", confidence=0.82, lat=g_lat, lon=g_lon, drone_lat=d2_lat, drone_lon=d2_lon, pixel_x=640.0, pixel_y=510.0)
    ], now=t0 + 2.0)
    assert len(r2) == 1, "Real ground target with moving pixel centroid must confirm on same track"
    real_track = list(agg_real.tracks.values())[0]
    assert real_track.confirmed is True
    assert real_track.is_vehicle_artifact is False


def test_artifact_track_does_not_absorb_subsequent_ground_target_within_merge_radius():
    """When a candidate track is flagged as a vehicle artifact, subsequent real ground detections
    within merge_radius_m must NOT be swallowed by the artifact track; they must create their own track and confirm."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=50.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000

    # 1. Shadow detection at t0
    s1_lat, s1_lon = meters_to_lat_lon(0.5, 0.0, d0_lat, d0_lon)
    agg.update([
        GeoreferencedDetection(label="rubbish", confidence=0.60, lat=s1_lat, lon=s1_lon, drone_lat=d0_lat, drone_lon=d0_lon, pixel_x=678.0, pixel_y=60.0)
    ], now=t0)

    # 2. Shadow detection at t0 + 2.0s after drone moves 1.6m North (shadow pixel stays stationary)
    d2_lat, d2_lon = meters_to_lat_lon(1.6, 0.0, d0_lat, d0_lon)
    s2_lat, s2_lon = meters_to_lat_lon(1.6, 0.0, s1_lat, s1_lon)
    agg.update([
        GeoreferencedDetection(label="rubbish", confidence=0.60, lat=s2_lat, lon=s2_lon, drone_lat=d2_lat, drone_lon=d2_lon, pixel_x=678.5, pixel_y=61.0)
    ], now=t0 + 2.0)

    assert len(agg.tracks) == 1
    artifact_track = list(agg.tracks.values())[0]
    assert artifact_track.is_vehicle_artifact is True
    assert artifact_track.confirmed is False

    # 3. Drone moves to 2.2m North; genuine ground target is located at g_lat, g_lon (which is only 1.0m from artifact track)
    g_lat, g_lon = meters_to_lat_lon(2.0, 0.0, d0_lat, d0_lon)
    d3_lat, d3_lon = meters_to_lat_lon(2.2, 0.0, d0_lat, d0_lon)
    res3 = agg.update([
        GeoreferencedDetection(label="rubbish", confidence=0.80, lat=g_lat, lon=g_lon, drone_lat=d3_lat, drone_lon=d3_lon, pixel_x=640.0, pixel_y=250.0)
    ], now=t0 + 4.0)

    # Should create a NEW track, NOT merge into artifact_track
    assert len(res3) == 0
    assert len(agg.tracks) == 2
    tracks = list(agg.tracks.values())
    real_track = tracks[1]
    assert real_track.id != artifact_track.id
    assert real_track.is_vehicle_artifact is False

    # 4. Drone moves to 5.2m North (3.0m further >= 3*sigma_diff ~2.12m), target pixel shifts to 450.0 (> 50px tol)
    d4_lat, d4_lon = meters_to_lat_lon(5.2, 0.0, d0_lat, d0_lon)
    res4 = agg.update([
        GeoreferencedDetection(label="rubbish", confidence=0.85, lat=g_lat, lon=g_lon, drone_lat=d4_lat, drone_lon=d4_lon, pixel_x=640.0, pixel_y=450.0)
    ], now=t0 + 6.0)

    # Real track must confirm!
    assert len(res4) == 1
    assert res4[0].id == real_track.id
    assert real_track.confirmed is True
    assert real_track.is_vehicle_artifact is False


def test_hovering_drone_below_displacement_threshold_does_not_reject_as_artifact():
    """A hovering drone with displacement below threshold must NOT be flagged as an artifact."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=35.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000
    det1 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.75,
        lat=22.314010,
        lon=87.310000,
        drone_lat=d0_lat,
        drone_lon=d0_lon,
        pixel_x=640.0,
        pixel_y=360.0,
    )
    agg.update([det1], now=t0)
    track = list(agg.tracks.values())[0]

    # Drone drifts by only 0.4m (< 1.5m min displacement)
    d_drift_lat, d_drift_lon = meters_to_lat_lon(0.4, 0.0, d0_lat, d0_lon)
    det2 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.75,
        lat=22.314010,
        lon=87.310000,
        drone_lat=d_drift_lat,
        drone_lon=d_drift_lon,
        pixel_x=640.5,
        pixel_y=360.5,
    )
    res2 = agg.update([det2], now=t0 + 2.0)
    assert len(res2) == 0, "Hovering drone without required displacement does not confirm"
    assert track.is_vehicle_artifact is False, "Must NOT reject as artifact when drone displacement is below threshold"
    assert track.confirmed is False


def test_moving_pixel_ground_target_confirms_when_drone_moves():
    """A real ground target whose pixel position shifts as the drone moves confirms normally."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=35.0,
    )
    t0 = 100.0
    # First hit: drone at origin, target at image center (640.0, 360.0)
    det1 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.80,
        lat=22.314000,
        lon=87.310000,
        drone_lat=22.314000,
        drone_lon=87.310000,
        pixel_x=640.0,
        pixel_y=360.0,
    )
    agg.update([det1], now=t0)
    track = list(agg.tracks.values())[0]

    # Second hit: drone moves 3 meters north, target pixel shifts by 192 px (>> 35.0 px tolerance)
    d2_lat, d2_lon = meters_to_lat_lon(3.0, 0.0, 22.314000, 87.310000)
    det2 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.85,
        lat=22.314001,
        lon=87.310000,
        drone_lat=d2_lat,
        drone_lon=d2_lon,
        pixel_x=832.0,
        pixel_y=360.0,
    )
    res2 = agg.update([det2], now=t0 + 0.5)
    assert len(res2) == 1, "Real ground target with moving pixel centroid must confirm"
    assert track.confirmed is True
    assert track.is_vehicle_artifact is False
    assert track.hits == 2
    assert track.distinct_hits == 2


def test_perception_config_defaults_include_artifact_rejection_and_masks():
    """Verify PerceptionConfig and CameraConfig load default values for artifact rejection and mask regions."""
    from DroneOS.shared.config.models import PerceptionConfig
    from DroneOS.shared.config.loader import load_yaml_config
    from pathlib import Path

    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "perception.yaml"
    cfg = load_yaml_config(cfg_path, PerceptionConfig)
    assert cfg.vehicle_artifact_pixel_tol_px == 50.0
    assert cfg.camera.mask_regions == []


@pytest.mark.parametrize("altitude_m", [5.0, 10.0, 20.0, 40.0])
@pytest.mark.parametrize("img_w", [640, 1280, 1920])
def test_expected_shift_model_real_ground_object_never_flagged(altitude_m, img_w):
    """A real ground object is NOT flagged at altitudes 5, 10, 20 and 40 m, and image widths 640, 1280 and 1920."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_shift_ratio=0.25,
        vehicle_artifact_pixel_floor_px=8.0,
        hfov_deg=90.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000
    g_lat, g_lon = meters_to_lat_lon(1.0, 0.0, d0_lat, d0_lon)
    img_h = int(img_w * 9 / 16)

    # Hit 1: Drone at origin, object at center of image
    u1, v1 = float(img_w / 2.0), float(img_h / 2.0)
    det1 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.85,
        lat=g_lat,
        lon=g_lon,
        drone_lat=d0_lat,
        drone_lon=d0_lon,
        altitude_agl=altitude_m,
        pixel_x=u1,
        pixel_y=v1,
        img_w=img_w,
        img_h=img_h,
    )
    r1 = agg.update([det1], now=t0)
    assert len(r1) == 0

    # Hit 2: Drone moves 2.0m North (displacement >= 1.5m and >= 3*sigma_diff)
    disp_m = 2.0
    d2_lat, d2_lon = meters_to_lat_lon(disp_m, 0.0, d0_lat, d0_lon)
    # Expected shift in pixels for ground object
    focal_px = (float(img_w) / 2.0) / math.tan(math.radians(45.0))
    expected_shift_px = focal_px * disp_m / altitude_m
    u2 = u1
    v2 = v1 + expected_shift_px  # Moves in image plane along flight axis

    det2 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.88,
        lat=g_lat,
        lon=g_lon,
        drone_lat=d2_lat,
        drone_lon=d2_lon,
        altitude_agl=altitude_m,
        pixel_x=u2,
        pixel_y=v2,
        img_w=img_w,
        img_h=img_h,
    )
    r2 = agg.update([det2], now=t0 + 2.0)
    assert len(r2) == 1, f"Real ground object MUST confirm at alt={altitude_m}m, width={img_w}px"
    track = list(agg.tracks.values())[0]
    assert track.confirmed is True
    assert track.is_vehicle_artifact is False, f"Real ground object must NOT be flagged at alt={altitude_m}m, width={img_w}px"


@pytest.mark.parametrize("altitude_m", [5.0, 10.0, 20.0, 40.0])
@pytest.mark.parametrize("img_w", [640, 1280, 1920])
def test_expected_shift_model_zero_shift_shadow_is_flagged(altitude_m, img_w):
    """A zero-shift (shadow-like) track IS flagged as a vehicle artifact at altitudes 5, 10, 20 and 40 m, and image widths 640, 1280 and 1920."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_shift_ratio=0.25,
        vehicle_artifact_pixel_floor_px=8.0,
        hfov_deg=90.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000
    img_h = int(img_w * 9 / 16)
    # Shadow ground position at 0.5m from drone
    s1_lat, s1_lon = meters_to_lat_lon(0.5, 0.0, d0_lat, d0_lon)
    u1, v1 = float(img_w / 2.0), float(img_h / 2.0)

    det1 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.60,
        lat=s1_lat,
        lon=s1_lon,
        drone_lat=d0_lat,
        drone_lon=d0_lon,
        altitude_agl=altitude_m,
        pixel_x=u1,
        pixel_y=v1,
        img_w=img_w,
        img_h=img_h,
    )
    r1 = agg.update([det1], now=t0)
    assert len(r1) == 0

    # Drone moves 2.0m North; shadow follows drone (moves 2.0m on ground, stationary in image)
    disp_m = 2.0
    d2_lat, d2_lon = meters_to_lat_lon(disp_m, 0.0, d0_lat, d0_lon)
    s2_lat, s2_lon = meters_to_lat_lon(disp_m, 0.0, s1_lat, s1_lon)
    # Pixel centroid remains at center (zero shift with tiny 0.5px jitter)
    u2, v2 = u1 + 0.5, v1 + 0.5

    det2 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.62,
        lat=s2_lat,
        lon=s2_lon,
        drone_lat=d2_lat,
        drone_lon=d2_lon,
        altitude_agl=altitude_m,
        pixel_x=u2,
        pixel_y=v2,
        img_w=img_w,
        img_h=img_h,
    )
    r2 = agg.update([det2], now=t0 + 2.0)
    assert len(r2) == 0, f"Shadow track must NOT confirm at alt={altitude_m}m, width={img_w}px"
    track = list(agg.tracks.values())[0]
    assert track.confirmed is False
    assert track.is_vehicle_artifact is True, f"Shadow track MUST be flagged as artifact at alt={altitude_m}m, width={img_w}px"


def test_hovering_drone_not_flagged_under_expected_shift_model():
    """A hovering drone (below min_drone_displacement_m) is not flagged as an artifact."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_shift_ratio=0.25,
        vehicle_artifact_pixel_floor_px=8.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000
    det1 = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=d0_lat, lon=d0_lon, drone_lat=d0_lat, drone_lon=d0_lon,
        altitude_agl=5.0, pixel_x=640.0, pixel_y=360.0, img_w=1280, img_h=720,
    )
    agg.update([det1], now=t0)

    # Frame 2: drone moved only 0.2m (hovering / GPS jitter < 1.5m)
    d2_lat, d2_lon = meters_to_lat_lon(0.2, 0.0, d0_lat, d0_lon)
    det2 = GeoreferencedDetection(
        label="rubbish", confidence=0.82, lat=d0_lat, lon=d0_lon, drone_lat=d2_lat, drone_lon=d2_lon,
        altitude_agl=5.0, pixel_x=640.0, pixel_y=360.0, img_w=1280, img_h=720,
    )
    r2 = agg.update([det2], now=t0 + 2.0)
    assert len(r2) == 0
    track = list(agg.tracks.values())[0]
    assert track.confirmed is False
    assert track.is_vehicle_artifact is False, "Hovering drone below displacement threshold must NOT be flagged as artifact"


def test_expected_shift_model_fallback_path():
    """When altitude or intrinsics are unknown, the aggregator falls back to fixed tolerance."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=50.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000
    g_lat, g_lon = meters_to_lat_lon(1.0, 0.0, d0_lat, d0_lon)

    # Detection with altitude=None, img_w=None (fallback path)
    det1 = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=g_lat, lon=g_lon, drone_lat=d0_lat, drone_lon=d0_lon,
        altitude_agl=None, pixel_x=640.0, pixel_y=360.0, img_w=None, img_h=None,
    )
    agg.update([det1], now=t0)

    # Frame 2 with small pixel shift (10px <= fallback 50px) while drone moves 2.0m -> flagged as artifact
    d2_lat, d2_lon = meters_to_lat_lon(2.0, 0.0, d0_lat, d0_lon)
    det2_artifact = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=g_lat, lon=g_lon, drone_lat=d2_lat, drone_lon=d2_lon,
        altitude_agl=None, pixel_x=640.0, pixel_y=370.0, img_w=None, img_h=None,
    )
    r2 = agg.update([det2_artifact], now=t0 + 2.0)
    assert len(r2) == 0
    track = list(agg.tracks.values())[0]
    assert track.is_vehicle_artifact is True, "Fallback path must flag stationary pixel shift <= fallback_pixel_tol_px"

    # Frame 2 with large pixel shift (80px > fallback 50px) -> confirms
    agg2 = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_pixel_tol_px=50.0,
    )
    agg2.update([det1], now=t0)
    det2_real = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=g_lat, lon=g_lon, drone_lat=d2_lat, drone_lon=d2_lon,
        altitude_agl=None, pixel_x=640.0, pixel_y=445.0, img_w=None, img_h=None,
    )
    r_real = agg2.update([det2_real], now=t0 + 2.0)
    assert len(r_real) == 1, "Fallback path must confirm shift > fallback_pixel_tol_px"
    track2 = list(agg2.tracks.values())[0]
    assert track2.confirmed is True
    assert track2.is_vehicle_artifact is False


def test_shadow_hit_does_not_poison_real_target_track():
    """An initial spurious shadow detection followed by real ground detections does NOT poison the track and confirms."""
    agg = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        merge_radius_m=2.5,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        vehicle_artifact_shift_ratio=0.25,
        vehicle_artifact_pixel_floor_px=8.0,
        hfov_deg=90.0,
    )
    t0 = 100.0
    d0_lat, d0_lon = 22.314000, 87.310000
    # Ground target at (1.5m North, 0m East)
    target_lat, target_lon = meters_to_lat_lon(1.5, 0.0, d0_lat, d0_lon)

    # 1. Spurious shadow detection near target at t0 (drone at origin, shadow at nadir 640, 360, geolocated 1.0m North)
    s_lat, s_lon = meters_to_lat_lon(1.0, 0.0, d0_lat, d0_lon)
    det1_shadow = GeoreferencedDetection(
        label="rubbish", confidence=0.55, lat=s_lat, lon=s_lon, drone_lat=d0_lat, drone_lon=d0_lon,
        altitude_agl=5.0, pixel_x=640.0, pixel_y=360.0, img_w=1280, img_h=720,
    )
    agg.update([det1_shadow], now=t0)
    assert len(agg.tracks) == 1

    # 2. Real target detection at t0 + 1.0s (drone moved 1.0m North, target pixel at 768)
    d1_lat, d1_lon = meters_to_lat_lon(1.0, 0.0, d0_lat, d0_lon)
    det2_target = GeoreferencedDetection(
        label="rubbish", confidence=0.85, lat=target_lat, lon=target_lon, drone_lat=d1_lat, drone_lon=d1_lon,
        altitude_agl=5.0, pixel_x=704.0, pixel_y=360.0, img_w=1280, img_h=720,
    )
    agg.update([det2_target], now=t0 + 1.0)

    # 3. Second real target detection at t0 + 2.5s (drone moved 3.0m North, target pixel at 448)
    # Between hit 2 (drone at 1.0m) and hit 3 (drone at 3.0m), displacement is 2.0m >= 1.5m
    # Pixel shift between hit 2 and hit 3 is |448 - 704| = 256px >= threshold (48px)
    d2_lat, d2_lon = meters_to_lat_lon(3.0, 0.0, d0_lat, d0_lon)
    det3_target = GeoreferencedDetection(
        label="rubbish", confidence=0.90, lat=target_lat, lon=target_lon, drone_lat=d2_lat, drone_lon=d2_lon,
        altitude_agl=5.0, pixel_x=448.0, pixel_y=360.0, img_w=1280, img_h=720,
    )
    res = agg.update([det3_target], now=t0 + 2.5)

    # Real target MUST confirm! Spurious shadow at hit 1 must not poison the track!
    assert len(res) == 1, "Track with genuine ground observations MUST confirm despite earlier shadow hit"
    confirmed_track = list(agg.tracks.values())[0]
    assert confirmed_track.confirmed is True
    assert confirmed_track.is_vehicle_artifact is False







