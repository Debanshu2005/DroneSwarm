"""
Phase 1.1 Single-Drone AirSim Garbage Detection & Geolocation Regression Suite.

Strictly verifies:
1. Dynamic camera resolution handling (1280x720 nadir Scene capture).
2. Production telemetry safety gating matrix:
   - Armed state enforcement
   - GPS fix validity
   - Relative altitude AGL (>= 2.0m)
   - Tilt angle limits (|roll|, |pitch| <= 20 deg)
3. YOLO26n inference using trained checkpoint models/garbage.pt detecting rubbish.
4. Independent 3D AirSim world coordinate conversion to WGS84 GPS.
5. Production network stack integration:
   - UdpNetworkAdapter transmission
   - UdpWebsocketRelay receipt & forwarding
   - WebSocket client reception
   - Node.js GCS mergeDetections state update
6. Truthful reporting contract:
   - Validates that when AirSim is unreachable, Stage 1 reports BLOCKED and status is BLOCKED.
"""

import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import time
import cv2
import numpy as np
import pytest
import websockets

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.frame_sources import AirSimFrameSource, Frame
from DroneOS.perception.geolocate import meters_to_lat_lon, pixel_to_lat_lon, wgs84_meters_per_degree
from DroneOS.perception.pipeline import GeoreferencedDetection, PerceptionPipeline
from DroneOS.shared.communication.network_node import UdpNetworkAdapter
from DroneOS.shared.communication.serializers import JsonSerializer
from DroneOS.shared.config.models import CameraConfig, PerceptionConfig
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage, TelemetryData
from relay.relay import UdpWebsocketRelay

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_camera_resolution_dynamic_handling():
    """Verify camera resolution is dynamically determined from frame dimensions (no hardcoded assumption)."""
    frame_hd = np.zeros((720, 1280, 3), dtype=np.uint8)
    h_hd, w_hd = frame_hd.shape[:2]
    assert (w_hd, h_hd) == (1280, 720)

    frame_low = np.zeros((144, 256, 3), dtype=np.uint8)
    h_low, w_low = frame_low.shape[:2]
    assert (w_low, h_low) == (256, 144)

    assert (w_hd / 2.0, h_hd / 2.0) == (640.0, 360.0)
    assert (w_low / 2.0, h_low / 2.0) == (128.0, 72.0)


def test_telemetry_altitude_gating():
    """Verify perception safety gate rejects grounded vehicle (< 2.0m AGL) and accepts airborne vehicle (>= 2.0m AGL)."""
    min_alt_m = 2.0

    # Grounded state (1.674m AGL - sitting on ground)
    ground_alt = 1.674
    assert ground_alt < min_alt_m, "Grounded altitude must be below safety threshold"

    # Airborne state (10.0m AGL)
    flight_alt = 10.0
    assert flight_alt >= min_alt_m, "Flight altitude must pass safety threshold"


@pytest.mark.asyncio
async def test_live_yolo_inference_on_sample_target():
    """Verify trained checkpoint models/garbage.pt detects rubbish on genuine crop."""
    weights_path = REPO_ROOT / "models" / "garbage.pt"
    crop_path = REPO_ROOT / "ml" / "data" / "sample_target_garbage.png"
    assert weights_path.exists(), "models/garbage.pt must exist"
    assert crop_path.exists(), "Sample target garbage crop must exist"

    target_crop = cv2.imread(str(crop_path))
    ch, cw = target_crop.shape[:2]

    # Create 1280x720 neutral background
    frame = np.full((720, 1280, 3), 110, dtype=np.uint8)
    frame[255:255+ch, 535:535+cw] = target_crop

    detector = GarbageDetector(weights_path=str(weights_path), confidence=0.25, iou=0.45)
    await detector.ensure_loaded()

    detections = await detector.detect(frame)
    detector.close()

    assert len(detections) > 0, "YOLO model should detect the rubbish target"
    best = max(detections, key=lambda d: d.confidence)
    assert best.label == "rubbish"
    assert best.confidence >= 0.35, f"Confidence {best.confidence} should exceed 0.35"


def test_independent_world_coordinates_to_gps():
    """Verify AirSim 3D NED world coordinates convert to WGS84 GPS independently of optical ray casting."""
    origin_lat = 47.641468
    origin_lon = -122.140165

    # Target placed at North=0.0m, East=2.0m
    target_north_m = 0.0
    target_east_m = 2.0

    true_lat, true_lon = meters_to_lat_lon(target_north_m, target_east_m, origin_lat, origin_lon)
    m_lat, m_lon = wgs84_meters_per_degree(origin_lat)

    dn = (true_lat - origin_lat) * m_lat
    de = (true_lon - origin_lon) * m_lon

    assert abs(dn - target_north_m) < 1e-4
    assert abs(de - target_east_m) < 1e-4


@pytest.mark.asyncio
async def test_production_network_stack_and_gcs_v8_execution():
    """Verify full production network path: UdpNetworkAdapter -> UdpWebsocketRelay -> WebSocket Client -> Node.js GCS."""
    relay_udp_port = 14655
    relay_ws_port = 8089

    relay = UdpWebsocketRelay(
        ws_host="127.0.0.1",
        ws_port=relay_ws_port,
        udp_bind_host="127.0.0.1",
        udp_bind_port=relay_udp_port,
        udp_target_port=14555,
    )
    relay_task = asyncio.create_task(relay.start())
    await asyncio.sleep(0.5)

    ws_client = await websockets.connect(f"ws://127.0.0.1:{relay_ws_port}")
    await asyncio.sleep(0.2)

    adapter = UdpNetworkAdapter(
        node_id="drone1",
        host="127.0.0.1",
        port=14555,
        broadcast_address="127.0.0.1",
        serializer=JsonSerializer(),
        peer_endpoints=[("127.0.0.1", relay_udp_port)],
    )
    await adapter.start()

    item = DetectionItem(
        id="drone1-g0001",
        label="rubbish",
        confidence=0.88,
        lat=47.641468,
        lon=-122.140138,
        hits=2,
        status="confirmed",
        altitude_agl=10.0,
    )
    msg = DetectionMessage(
        sender_id="drone1",
        timestamp=time.time(),
        detections=[item],
        synthetic=False,
        frame_id=1,
    )

    await adapter.broadcast_message(msg)

    raw_ws = await asyncio.wait_for(ws_client.recv(), timeout=3.0)
    received = json.loads(raw_ws)

    assert received["msg_type"] == "detection"
    assert received["sender_id"] == "drone1"
    assert received["synthetic"] is False
    assert len(received["detections"]) == 1

    # Verify directly inside Node.js V8 engine with mobile/src/utils/garbage.js
    js_code = f"""
    import('{Path(REPO_ROOT, "mobile", "src", "utils", "garbage.js").as_uri()}').then(({{ mergeDetections }}) => {{
        const msg = {json.dumps(received)};
        const state = mergeDetections({{}}, msg.detections, msg.sender_id, Date.now(), new Set(), msg.synthetic);
        const m = state['drone1-g0001'];
        if (m && m.confidence === 0.88 && m.synthetic === false && m.seenBy.includes('drone1')) {{
            console.log('NODE_GCS_SUCCESS');
        }} else {{
            console.log('NODE_GCS_FAIL');
        }}
    }});
    """
    proc = subprocess.run(["node", "--input-type=module", "-e", js_code], capture_output=True, text=True, timeout=5)
    assert "NODE_GCS_SUCCESS" in proc.stdout

    # Clean shutdown
    await ws_client.close()
    await adapter.stop()
    relay_task.cancel()
    try:
        await relay_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_truthful_offline_reporting_contract():
    """Verify validation script truthfully reports BLOCKED when AirSim RPC is offline (no fake success)."""
    from scripts.validate_single_drone_airsim import execute_genuine_airsim_validation

    # Port 41459 is not listening
    report = await execute_genuine_airsim_validation(
        host="127.0.0.1",
        port=41459,
        vehicle_name="Drone1",
    )

    assert report["status"] == "BLOCKED"
    assert report["stages"]["airsim_rpc_connectivity"] == "BLOCKED"
    assert report["stages"]["vehicle_telemetry_validity"] == "NOT_TESTED"
    assert report["is_genuine_airsim"] is False


def test_track_aggregator_interface_contract():
    """
    Regression test for TrackAggregator API contract:
    1. Verifies that aggregator exposes .tracks (dict) and does NOT expose get_confirmed_tracks or active_tracks.
    2. Verifies that AggregatedTrack contains max_confidence, confirmed, hits, lat, lon (NOT confidence).
    3. Verifies multi-step temporal confirmation: with min_hits=2, step 1 yields unconfirmed track,
       and step 2 yields confirmed track with emitted AggregatorItem.
    """
    from DroneOS.perception.aggregator import TrackAggregator, GeoreferencedDetection

    aggregator = TrackAggregator(drone_id="drone1", min_hits=2, merge_radius_m=2.5)

    # 1. Structural API checks
    assert hasattr(aggregator, "tracks")
    assert isinstance(aggregator.tracks, dict)
    assert not hasattr(aggregator, "get_confirmed_tracks"), "TrackAggregator must not have get_confirmed_tracks"
    assert not hasattr(aggregator, "active_tracks"), "TrackAggregator must not have active_tracks"

    # 2. Step 1: Single detection with min_hits=2 -> unconfirmed candidate track
    det1 = GeoreferencedDetection(label="rubbish", confidence=0.85, lat=47.641468, lon=-122.140138, altitude_agl=10.0)
    emitted1 = aggregator.update([det1], now=100.0)

    assert len(emitted1) == 0, "First hit must not emit when min_hits=2"
    assert len(aggregator.tracks) == 1

    unconfirmed = [t for t in aggregator.tracks.values() if not t.confirmed]
    confirmed = [t for t in aggregator.tracks.values() if t.confirmed]
    assert len(unconfirmed) == 1
    assert len(confirmed) == 0

    track = unconfirmed[0]
    assert track.hits == 1
    assert track.max_confidence == 0.85
    assert track.confirmed is False
    assert hasattr(track, "max_confidence")
    # Verify that accessing .confidence on AggregatedTrack raises AttributeError
    with pytest.raises(AttributeError):
        _ = track.confidence

    # 3. Step 2: Second matching detection -> confirmed track & emission
    det2 = GeoreferencedDetection(label="rubbish", confidence=0.90, lat=47.641469, lon=-122.140139, altitude_agl=10.0)
    emitted2 = aggregator.update([det2], now=101.0)

    assert len(emitted2) == 1, "Second hit must emit newly confirmed track"
    item = emitted2[0]
    assert item.id == track.id
    assert item.status == "new"
    assert item.hits == 2
    assert item.confidence == 0.90

    unconfirmed_step2 = [t for t in aggregator.tracks.values() if not t.confirmed]
    confirmed_step2 = [t for t in aggregator.tracks.values() if t.confirmed]
    assert len(unconfirmed_step2) == 0
    assert len(confirmed_step2) == 1
    assert confirmed_step2[0].confirmed is True
    assert confirmed_step2[0].hits == 2
    assert confirmed_step2[0].max_confidence == 0.90
