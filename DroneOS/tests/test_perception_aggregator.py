"""Unit tests for multi-frame detection aggregator."""

import pytest

from DroneOS.perception.aggregator import GeoreferencedDetection, TrackAggregator


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
    det1 = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314000, lon=87.310000)
    items1 = agg.update([det1], now=t0)
    assert len(items1) == 0  # min_hits is 2, not yet confirmed

    # Frame 2: second sighting within 1m
    # Confidence-weighted mean: (0.8*lat1 + 0.6*lat2) / (0.8 + 0.6)
    det2 = GeoreferencedDetection(label="rubbish", confidence=0.6, lat=22.314008, lon=87.310008)
    items2 = agg.update([det2], now=t0 + 1.0)
    assert len(items2) == 1
    new_item = items2[0]
    assert new_item.status == "new"
    assert new_item.id == "drone1-g0001"
    assert new_item.hits == 2
    assert new_item.confidence == 0.8  # max confidence
    expected_lat = (0.8 * 22.314000 + 0.6 * 22.314008) / 1.4
    assert pytest.approx(new_item.lat, abs=1e-6) == expected_lat

    # Frame 3 (only 2s later): sighting 3, but update_interval_s (10s) hasn't passed
    det3 = GeoreferencedDetection(label="rubbish", confidence=0.9, lat=22.314005, lon=87.310005)
    items3 = agg.update([det3], now=t0 + 3.0)
    assert len(items3) == 0  # throttled

    # Frame 4 (12s later): sighting 4, update_interval_s has passed -> emit update
    det4 = GeoreferencedDetection(label="rubbish", confidence=0.7, lat=22.314004, lon=87.310004)
    items4 = agg.update([det4], now=t0 + 15.0)
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
    det = GeoreferencedDetection(label="rubbish", confidence=0.8, lat=22.314, lon=87.310)

    # Frame 1 at t0
    res1 = agg.update([det], now=t0)
    assert len(res1) == 0
    track = list(agg.tracks.values())[0]
    assert track.hits == 1

    # Identical timestamp t0 (e.g. repeated frame or zero-delta call)
    res2 = agg.update([det], now=t0)
    assert len(res2) == 0
    assert track.hits == 1  # Must not increment hits

    # Genuine subsequent frame at t0 + 1.0
    res3 = agg.update([det], now=t0 + 1.0)
    assert len(res3) == 1
    assert track.hits == 2
    assert res3[0].status == "new"


