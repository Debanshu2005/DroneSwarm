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
        gps_sigma_m=1.0,  # 3 * sigma = 3.0 m gating
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


def test_stationary_drone_confirms_after_temporal_separation():
    """Stationary drone successfully confirms once required temporal separation elapses."""
    base_lat = 47.641468
    base_lon = -122.140165
    target_lat, target_lon = meters_to_lat_lon(10.0, 5.0, base_lat, base_lon)

    aggregator = TrackAggregator(
        drone_id="drone1",
        min_hits=2,
        min_temporal_separation_s=2.0,
        min_drone_displacement_m=1.5,
        gps_sigma_m=1.0,
    )

    # Frame 1 at t = 100.0s
    det1 = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=base_lat, drone_lon=base_lon,
    )
    aggregator.update([det1], now=100.0)

    # Frame 2 at t = 102.1s (2.1s later, >= 2.0s)
    det2 = GeoreferencedDetection(
        label="rubbish", confidence=0.85, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=base_lat, drone_lon=base_lon,
    )
    emitted = aggregator.update([det2], now=102.1)
    assert len(emitted) == 1
    assert emitted[0].hits == 2
    assert emitted[0].status == "new"

    tid = list(aggregator.tracks.keys())[0]
    assert aggregator.tracks[tid].confirmed is True


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
        gps_sigma_m=1.0,  # required displacement = 3.0 m
    )

    # Frame 1 at t = 100.0s
    det1 = GeoreferencedDetection(
        label="rubbish", confidence=0.80, lat=target_lat, lon=target_lon,
        altitude_agl=15.0, drone_lat=base_lat, drone_lon=base_lon,
    )
    aggregator.update([det1], now=100.0)

    # Frame 2 at t = 100.2s: drone moved 3.5m East (exceeds 3.0m threshold)
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
