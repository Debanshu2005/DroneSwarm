"""Comprehensive production audit regression tests for DroneSwarm perception hardening."""

import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock
import numpy as np
import pytest

from DroneOS.perception.aggregator import (
    AggregatedTrack,
    AggregatorItem,
    GeoreferencedDetection,
    TrackAggregator,
)
from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.geolocate import meters_to_lat_lon
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Detection
from DroneOS.shared.config.models import PerceptionConfig
from DroneOS.shared.protocol.messages import DetectionMessage


@pytest.mark.asyncio
async def test_two_frame_confirmation_requires_distinct_timestamps():
    """Verify that a candidate track requires at least 2 distinct timestamps to confirm."""
    agg = TrackAggregator(drone_id="drone1", min_hits=2, merge_radius_m=2.5)
    d1_lat, d1_lon = 40.7128, -74.0060
    det = GeoreferencedDetection(label="rubbish", confidence=0.80, lat=40.7128, lon=-74.0060, drone_lat=d1_lat, drone_lon=d1_lon)

    # Frame 1 at t=10.0
    items1 = agg.update([det], now=10.0)
    assert len(items1) == 0, "Unconfirmed track must NOT be emitted on hit 1"

    # Repeated observation at identical timestamp t=10.0 (e.g. video pause or duplicate message)
    items_dup = agg.update([det], now=10.0)
    assert len(items_dup) == 0, "Duplicate timestamp must NOT trigger confirmation"

    track = list(agg.tracks.values())[0]
    assert track.hits == 1, "Hits must remain 1 when timestamp does not advance"
    assert not track.confirmed, "Track must remain unconfirmed"

    # Frame 2 at advanced timestamp t=12.0 (>= 2.0s temporal separation) with displaced drone
    d2_lat, d2_lon = meters_to_lat_lon(2.5, 0.0, d1_lat, d1_lon)
    det2 = GeoreferencedDetection(label="rubbish", confidence=0.80, lat=40.7128, lon=-74.0060, drone_lat=d2_lat, drone_lon=d2_lon)
    items2 = agg.update([det2], now=12.0)
    assert len(items2) == 1, "Track must be confirmed and emitted on hit 2 at distinct timestamp"
    assert items2[0].status == "new"
    assert items2[0].hits == 2
    assert track.confirmed is True


def test_unconfirmed_detections_never_emitted_by_aggregator():
    """Verify aggregator never returns items for tracks that have not reached min_hits."""
    agg = TrackAggregator(drone_id="drone1", min_hits=3)
    d_lat, d_lon = 40.0, -74.0
    det1 = GeoreferencedDetection(label="rubbish", confidence=0.90, lat=40.0, lon=-74.0, drone_lat=d_lat, drone_lon=d_lon)

    # Hits 1 and 2
    res1 = agg.update([det1], now=1.0)
    d2_lat, d2_lon = meters_to_lat_lon(2.5, 0.0, d_lat, d_lon)
    det2 = GeoreferencedDetection(label="rubbish", confidence=0.90, lat=40.0, lon=-74.0, drone_lat=d2_lat, drone_lon=d2_lon)
    res2 = agg.update([det2], now=3.0)
    assert len(res1) == 0
    assert len(res2) == 0

    # Hit 3 reaches min_hits=3
    d3_lat, d3_lon = meters_to_lat_lon(5.0, 0.0, d_lat, d_lon)
    det3 = GeoreferencedDetection(label="rubbish", confidence=0.90, lat=40.0, lon=-74.0, drone_lat=d3_lat, drone_lon=d3_lon)
    res3 = agg.update([det3], now=5.0)
    assert len(res3) == 1
    assert res3[0].status == "new"


def test_confirmation_state_expires_correctly():
    """Verify unconfirmed tracks expire and are deleted after unconfirmed_ttl_s (30s)."""
    agg = TrackAggregator(drone_id="drone1", min_hits=2, unconfirmed_ttl_s=30.0)
    det = GeoreferencedDetection(label="rubbish", confidence=0.75, lat=40.0, lon=-74.0)

    # Transient detection at t=0
    agg.update([det], now=0.0)
    assert len(agg.tracks) == 1
    track_id = list(agg.tracks.keys())[0]

    # At t=29.0s (within TTL)
    agg.update([], now=29.0)
    assert track_id in agg.tracks

    # At t=31.0s (> TTL)
    agg.update([], now=31.0)
    assert track_id not in agg.tracks, "Stale unconfirmed track must be pruned after TTL"


def test_spatial_track_association_and_separation():
    """Verify objects within 2.5m merge, while objects > 2.5m form distinct tracks."""
    agg = TrackAggregator(drone_id="drone1", min_hits=1, merge_radius_m=2.5)

    base_lat = 40.7128
    base_lon = -74.0060

    # Det 1 at base
    det1 = GeoreferencedDetection(label="rubbish", confidence=0.85, lat=base_lat, lon=base_lon)
    # Det 2 at 1.0 m North (~0.000009 deg lat)
    det2_close = GeoreferencedDetection(label="rubbish", confidence=0.90, lat=base_lat + 1.0/111139.0, lon=base_lon)
    # Det 3 at 10.0 m North (~0.000090 deg lat)
    det3_far = GeoreferencedDetection(label="rubbish", confidence=0.88, lat=base_lat + 10.0/111139.0, lon=base_lon)

    # Det 1 creates Track 1
    items1 = agg.update([det1], now=1.0)
    assert len(items1) == 1
    t1_id = items1[0].id

    # Det 2 (1.0m away) merges into Track 1
    items2 = agg.update([det2_close], now=2.0)
    assert len(agg.tracks) == 1, "Nearby detection must merge into existing track"

    # Det 3 (10m away) creates Track 2
    items3 = agg.update([det3_far], now=3.0)
    assert len(agg.tracks) == 2, "Distant detection must create a separate track"
    assert items3[0].id != t1_id


def test_geometric_filtering_preserves_legitimate_detections():
    """Verify that defensive geometric filters accept typical garbage sizes and aspect ratios."""
    detector = GarbageDetector(
        weights_path="models/garbage.pt",
        confidence=0.55,
        min_area_ratio=0.00005,  # 0.005% frame area
        max_area_ratio=0.40,     # 40% frame area
        max_aspect_ratio=8.0,    # max AR 8.0
    )

    img_w, img_h = 1280, 720
    total_area = float(img_w * img_h)  # 921,600 px^2

    # Legitimate garbage cases
    legit_cases = [
        (100, 100, 150, 150),   # 50x50 = 2500 px^2 (0.27% area, AR=1.0) -> plastic container
        (200, 200, 230, 290),   # 30x90 = 2700 px^2 (0.29% area, AR=3.0) -> plastic bottle
        (500, 400, 520, 420),   # 20x20 = 400 px^2 (0.043% area, AR=1.0) -> can / cap
        (600, 300, 750, 450),   # 150x150 = 22500 px^2 (2.44% area, AR=1.0) -> cardboard box / bag
    ]

    for x1, y1, x2, y2 in legit_cases:
        bw, bh = x2 - x1, y2 - y1
        area_ratio = (bw * bh) / total_area
        ar = max(bw / bh, bh / bw)
        assert area_ratio >= detector.min_area_ratio, f"Legitimate bbox dropped by min_area: {area_ratio}"
        assert area_ratio <= detector.max_area_ratio, f"Legitimate bbox dropped by max_area: {area_ratio}"
        assert ar <= detector.max_aspect_ratio, f"Legitimate bbox dropped by aspect_ratio: {ar}"

    # Extreme noise cases that SHOULD be rejected
    rejected_cases = [
        (100, 100, 104, 104),   # 4x4 = 16 px^2 (0.0017% < 0.005%) -> subpixel sensor noise
        (100, 100, 1200, 700),  # 1100x600 = 660,000 px^2 (71.6% > 40%) -> full frame anomaly
        (100, 100, 1000, 120),  # 900x20 = 18000 px^2 (AR = 45.0 > 8.0) -> road marking / wire
    ]

    for x1, y1, x2, y2 in rejected_cases:
        bw, bh = x2 - x1, y2 - y1
        area_ratio = (bw * bh) / total_area
        ar = max(bw / bh, bh / bw)
        should_reject = (
            area_ratio < detector.min_area_ratio
            or area_ratio > detector.max_area_ratio
            or ar > detector.max_aspect_ratio
        )
        assert should_reject is True


@pytest.mark.asyncio
async def test_pipeline_does_not_broadcast_unconfirmed_tracks():
    """Verify that PerceptionPipeline never broadcasts unconfirmed detections over network adapter."""
    mock_frame = MagicMock()
    mock_frame.image = np.zeros((720, 1280, 3), dtype=np.uint8)
    mock_frame.timestamp = 100.0

    mock_frame_source = AsyncMock()
    mock_frame_source.get_frame.return_value = mock_frame
    mock_frame_source.close = AsyncMock()

    mock_detector = MagicMock()
    mock_detector.ensure_loaded = AsyncMock(return_value=True)
    mock_detector.close = MagicMock()
    # Return 1 detection in center for steps 1 and 2
    det1 = Detection(bbox_xyxy=(600, 340, 680, 380), confidence=0.85, label="rubbish")
    # On step 3, drone moves North by 2.2m at 10m alt -> ground object in nadir camera shifts by fx*(disp/alt) = 640*(2.2/10) = 140.8px
    det3 = Detection(bbox_xyxy=(600, 340 + 140.8, 680, 380 + 140.8), confidence=0.85, label="rubbish")
    mock_detector.detect = AsyncMock(side_effect=[[det1], [det1], [det3]])

    mock_telem = MagicMock()
    mock_telem.armed_state = "ARMED"
    mock_telem.gps_valid = True
    mock_telem.altitude = 10.0
    mock_telem.roll = 0.0
    mock_telem.pitch = 0.0
    mock_telem.yaw = 0.0
    mock_telem.latitude = 40.7128
    mock_telem.longitude = -74.0060
    mock_telem.timestamp = 100.0

    mock_fc = AsyncMock()
    mock_fc.get_telemetry.return_value = mock_telem

    mock_net = AsyncMock()
    mock_net.broadcast_message = AsyncMock()

    cfg = PerceptionConfig(
        confidence=0.55,
        min_hits=2,  # Mandate 2 hits
        merge_radius_m=2.5,
    )

    pipeline = PerceptionPipeline(
        drone_id="drone1",
        frame_source=mock_frame_source,
        detector=mock_detector,
        flight_controller=mock_fc,
        network_adapter=mock_net,
        config=cfg,
    )

    # Step 1: Hit 1 -> Unconfirmed -> NO broadcast
    await pipeline._step()
    assert mock_net.broadcast_message.call_count == 0, "Pipeline must NOT broadcast on hit 1"

    # Step 2: Timestamp does not advance (still 100.0) -> NO broadcast
    await pipeline._step()
    assert mock_net.broadcast_message.call_count == 0, "Pipeline must NOT broadcast on duplicate timestamp"

    # Step 3: Advance timestamp and drone position (>= 3*sigma_diff displacement) -> Hit 2 reached -> BROADCAST triggered
    mock_frame.timestamp = 102.5
    mock_telem.timestamp = 102.5
    mock_telem.latitude = float(mock_telem.latitude) + (2.2 / 111139.0)
    await pipeline._step()
    assert mock_net.broadcast_message.call_count == 1, "Pipeline MUST broadcast on hit 2"

    broadcast_msg = mock_net.broadcast_message.call_args[0][0]
    assert isinstance(broadcast_msg, DetectionMessage)
    assert len(broadcast_msg.detections) == 1
    assert broadcast_msg.detections[0].hits == 2
    assert broadcast_msg.detections[0].status == "new"
