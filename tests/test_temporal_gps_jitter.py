"""
Tests for GPS noise robustness in multi-frame temporal confirmation.
Verifies that 1.5 m GPS jitter on a stationary drone does NOT confirm
tracks in consecutive frames, while genuine displacement or temporal separation does.
"""

import pytest

from DroneOS.perception.aggregator import GeoreferencedDetection, TrackAggregator
from DroneOS.perception.geolocate import meters_to_lat_lon


def test_stationary_drone_with_1_5m_gps_jitter_does_not_confirm():
    """Injecting 1.5 m GPS jitter on a stationary hovering drone must NOT confirm in consecutive frames."""
    base_lat = 47.641468
    base_lon = -122.140165

    # Target static garbage on the ground
    target_lat, target_lon = meters_to_lat_lon(10.0, 5.0, base_lat, base_lon)

    aggregator = TrackAggregator(
        drone_id="drone1",
        merge_radius_m=2.5,
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        gps_sigma_m=0.5,  # 3 * sqrt(2) * sigma = 2.12 m gating
    )

    # Frame 1 at t = 100.0s: drone GPS with -0.75m jitter North
    d1_lat, d1_lon = meters_to_lat_lon(-0.75, 0.0, base_lat, base_lon)
    det1 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.80,
        lat=target_lat,
        lon=target_lon,
        altitude_agl=15.0,
        drone_lat=d1_lat,
        drone_lon=d1_lon,
    )

    emitted_1 = aggregator.update([det1], now=100.0)
    assert len(emitted_1) == 0, "First detection must not emit unconfirmed track"
    assert len(aggregator.tracks) == 1
    tid = list(aggregator.tracks.keys())[0]
    track = aggregator.tracks[tid]
    assert track.hits == 1
    assert track.distinct_hits == 1
    assert track.confirmed is False

    # Frame 2 at t = 100.1s (0.1s later): drone is stationary, but GPS has +0.75m jitter North (total 1.5m jitter)
    d2_lat, d2_lon = meters_to_lat_lon(+0.75, 0.0, base_lat, base_lon)
    det2 = GeoreferencedDetection(
        label="rubbish",
        confidence=0.82,
        lat=target_lat,
        lon=target_lon,
        altitude_agl=15.0,
        drone_lat=d2_lat,
        drone_lon=d2_lon,
    )

    emitted_2 = aggregator.update([det2], now=100.1)
    # MUST NOT confirm in two consecutive frames due to 1.5m GPS noise!
    assert len(emitted_2) == 0, "1.5m GPS jitter must NOT trigger false confirmation"
    assert track.hits == 1, "Hits must NOT increment on GPS jitter when dt < 2.0s"
    assert track.distinct_hits == 1
    assert track.confirmed is False


def test_hovering_drone_with_gps_jitter_does_not_confirm_in_30s():
    """
    A hovering drone with GPS jitter must NOT confirm across 30 s of repeated detections
    of one static false positive, because time separation alone cannot confirm a stationary drone.
    """
    base_lat = 47.641468
    base_lon = -122.140165
    target_lat, target_lon = meters_to_lat_lon(10.0, 5.0, base_lat, base_lon)

    aggregator = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        gps_sigma_m=0.5,
        min_viewpoint_angle_deg=5.0,
    )

    import random
    rng = random.Random(42)

    all_emitted = []
    # 30 seconds of detections (1 FPS, 30 frames)
    for step in range(30):
        t = 100.0 + step
        # GPS jitter within +/- 0.75m North/East (1.5m total envelope)
        j_n = rng.uniform(-0.75, 0.75)
        j_e = rng.uniform(-0.75, 0.75)
        d_lat, d_lon = meters_to_lat_lon(j_n, j_e, base_lat, base_lon)

        det = GeoreferencedDetection(
            label="rubbish",
            confidence=0.82,
            lat=target_lat,
            lon=target_lon,
            altitude_agl=15.0,
            drone_lat=d_lat,
            drone_lon=d_lon,
        )
        emitted = aggregator.update([det], now=t)
        all_emitted.extend(emitted)

    # Across 30 seconds of static false positive detections, NEVER confirm!
    assert len(all_emitted) == 0, "Static false positive must NEVER confirm on stationary hovering drone"
    tid = list(aggregator.tracks.keys())[0]
    track = aggregator.tracks[tid]
    assert track.distinct_hits == 1, "Distinct hits must not increment without displacement or viewpoint change"
    assert track.confirmed is False, "Track must remain unconfirmed"


def test_drone_confirms_when_viewpoint_angle_changes():
    """Drone confirms when observing target from a sufficiently different viewpoint angle (>= 5 deg)."""
    base_lat = 47.641468
    base_lon = -122.140165
    target_lat, target_lon = meters_to_lat_lon(10.0, 0.0, base_lat, base_lon)  # Target is due North (bearing 0 deg)

    aggregator = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=10.0,  # high displacement threshold so only viewpoint angle triggers
        gps_sigma_m=0.5,
        min_viewpoint_angle_deg=5.0,
    )

    # Frame 1 at t=100.0s: drone is due South of target -> bearing to target is 0 deg (due North)
    det1 = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=base_lat, drone_lon=base_lon,
    )
    aggregator.update([det1], now=100.0)

    # Frame 2 at t=102.0s: drone shifts position by 4.0m East so bearing to target changes by ~22 degrees
    # Target is at (10, 0) relative to base. Drone shifts 4.0m East: vector to target is (10, -4) -> bearing = -21.8 deg
    d2_lat, d2_lon = meters_to_lat_lon(0.0, 4.0, base_lat, base_lon)
    det2 = GeoreferencedDetection(
        label="rubbish", confidence=0.85, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=d2_lat, drone_lon=d2_lon,
    )
    emitted = aggregator.update([det2], now=102.0)
    assert len(emitted) == 1, "Track must confirm when viewpoint angle changes by >= 5 degrees"
    assert emitted[0].hits == 2
    assert emitted[0].status == "new"


def test_moving_drone_confirms_by_displacement_without_delay():
    """Moving drone confirms rapidly when displacement exceeds 3*gps_sigma threshold."""
    base_lat = 47.641468
    base_lon = -122.140165
    target_lat, target_lon = meters_to_lat_lon(10.0, 5.0, base_lat, base_lon)

    aggregator = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        gps_sigma_m=0.5,  # 3 * sqrt(2) * sigma = 2.12 m threshold
    )

    # Frame 1 at t = 100.0s
    det1 = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=base_lat, drone_lon=base_lon,
    )
    aggregator.update([det1], now=100.0)

    # Frame 2 at t = 100.2s: drone moved 3.5m East (exceeds 2.55m threshold)
    d2_lat, d2_lon = meters_to_lat_lon(0.0, 3.5, base_lat, base_lon)
    det2 = GeoreferencedDetection(
        label="rubbish", confidence=0.85, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=d2_lat, drone_lon=d2_lon,
    )
    emitted = aggregator.update([det2], now=100.2)
    assert len(emitted) == 1
    assert emitted[0].hits == 2
    assert emitted[0].status == "new"

    tid = list(aggregator.tracks.keys())[0]
    assert aggregator.tracks[tid].confirmed is True
