"""Unit tests for multi-frame temporal confirmation and TrackAggregator."""

import math
import time
import pytest

from DroneOS.perception.aggregator import (
    AggregatedTrack,
    AggregatorItem,
    GeoreferencedDetection,
    TrackAggregator,
)
from ml.uavvaste.evaluate_temporal_confirmation import (
    CONFIGURATIONS,
    simulate_multi_frame_evaluation,
)


def test_configurations_structure():
    """Verify standard 5 experimental configurations."""
    assert len(CONFIGURATIONS) == 5
    names = [c["name"] for c in CONFIGURATIONS]
    assert names == ["Baseline", "Config A", "Config B", "Config C", "Config D"]
    assert CONFIGURATIONS[0]["min_hits"] == 1
    assert CONFIGURATIONS[2]["min_hits"] == 2
    assert CONFIGURATIONS[4]["min_hits"] == 3


def test_temporal_confirmation_simulation_execution():
    """Verify Monte Carlo simulation runs and returns valid metric bounds."""
    results = simulate_multi_frame_evaluation(
        num_sequences=4,
        frames_per_seq=6,
        gt_objects_per_seq=2,
    )
    assert len(results) == 5
    for r in results:
        assert 0.0 <= r["precision"] <= 1.0
        assert 0.0 <= r["recall"] <= 1.0
        assert 0.0 <= r["f1_score"] <= 1.0
        assert r["mean_confirmation_delay_sec"] >= 0.0
        assert r["false_alerts_per_minute"] >= 0.0


def test_aggregator_duplicate_timestamp_rejection():
    """Verify that repeated processing of identical timestamp does NOT increment hits."""
    aggregator = TrackAggregator(drone_id="drone1", min_hits=2)
    det = GeoreferencedDetection(label="rubbish", confidence=0.85, lat=40.0, lon=-74.0)

    # Frame 1 at t = 100.0
    emitted1 = aggregator.update([det], now=100.0)
    assert len(emitted1) == 0  # Not yet confirmed (min_hits=2)
    track_id = list(aggregator.tracks.keys())[0]
    assert aggregator.tracks[track_id].hits == 1

    # Frame 1 repeated at identical t = 100.0
    emitted2 = aggregator.update([det], now=100.0)
    assert len(emitted2) == 0
    assert aggregator.tracks[track_id].hits == 1  # Hits must NOT increment!

    # Frame 2 at t = 102.5 (timestamp advances >= min_temporal_separation_s)
    emitted3 = aggregator.update([det], now=102.5)
    assert len(emitted3) == 1  # Confirmed!
    assert emitted3[0].status == "new"
    assert aggregator.tracks[track_id].hits == 2


def test_aggregator_unconfirmed_track_ttl_expiry():
    """Verify that transient unconfirmed tracks are pruned after unconfirmed_ttl_s (30s)."""
    aggregator = TrackAggregator(drone_id="drone1", min_hits=2, unconfirmed_ttl_s=30.0)
    det = GeoreferencedDetection(label="rubbish", confidence=0.70, lat=40.0, lon=-74.0)

    # Transient false positive seen at t = 0
    aggregator.update([det], now=0.0)
    assert len(aggregator.tracks) == 1

    # At t = 20s (within TTL), track remains
    aggregator.update([], now=20.0)
    assert len(aggregator.tracks) == 1

    # At t = 35s (> TTL), unconfirmed track is evicted
    aggregator.update([], now=35.0)
    assert len(aggregator.tracks) == 0


def test_multi_drone_track_id_namespace_isolation():
    """Verify that different drones maintain isolated track IDs and namespaces."""
    agg1 = TrackAggregator(drone_id="drone1", min_hits=1)
    agg2 = TrackAggregator(drone_id="drone2", min_hits=1)

    det = GeoreferencedDetection(label="rubbish", confidence=0.90, lat=40.0, lon=-74.0)
    emitted1 = agg1.update([det], now=10.0)
    emitted2 = agg2.update([det], now=10.0)

    assert emitted1[0].id.startswith("drone1-g")
    assert emitted2[0].id.startswith("drone2-g")
    assert emitted1[0].id != emitted2[0].id

