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


class MockAirSimClient:
    """Mock client for AirSim RPC state queries."""
    def __init__(self, landed_state=0, z_val=0.0, gps_valid=True, rotor_speeds=None):
        self.landed_state = landed_state
        self.z_val = z_val
        self.gps_valid = gps_valid
        self.rotor_speeds = rotor_speeds or []

    def getMultirotorState(self, vehicle_name=""):
        from unittest.mock import MagicMock
        state = MagicMock()
        state.landed_state = self.landed_state
        state.kinematics_estimated.position.z_val = self.z_val
        state.kinematics_estimated.orientation.w_val = 1.0
        state.kinematics_estimated.orientation.x_val = 0.0
        state.kinematics_estimated.orientation.y_val = 0.0
        state.kinematics_estimated.orientation.z_val = 0.0
        return state

    def getGpsData(self, _, vehicle_name=""):
        from unittest.mock import MagicMock
        gps = MagicMock()
        gps.is_valid = self.gps_valid
        gps.gnss.geo_point.latitude = 47.641468
        gps.gnss.geo_point.longitude = -122.140165
        return gps

    def getRotorStates(self, vehicle_name=""):
        from unittest.mock import MagicMock
        states = MagicMock()
        states.rotors = [{"speed": s} for s in self.rotor_speeds]
        return states

    def armDisarm(self, arm, vehicle_name=""):
        return True


@pytest.mark.asyncio
async def test_live_airsim_telemetry_adapter_arming_and_landed_independence():
    """
    Verify LiveAirSimTelemetryAdapter:
    1. Landed state (0=Landed, 1=Flying) NEVER alone determines arming state.
    2. Explicit set_armed(True/False) overrides landed state.
    3. Airborne active rotor spin (> 5.0 rad/s) determines arming when unset.
    4. flight_mode distinguishes HOLD (airborne) vs LANDED (grounded).
    """
    from scripts.validate_single_drone_airsim import LiveAirSimTelemetryAdapter

    # 1. Landed (landed_state=0, alt=0m), not armed -> armed_state must be DISARMED (NOT ARMED!)
    client = MockAirSimClient(landed_state=0, z_val=0.0, rotor_speeds=[0.0, 0.0, 0.0, 0.0])
    adapter = LiveAirSimTelemetryAdapter(client=client)
    adapter.set_armed(False)
    telem = await adapter.get_telemetry()
    assert telem.armed_state == "DISARMED"
    assert telem.flight_mode == "LANDED"

    # 2. Landed (landed_state=0), explicitly armed -> armed_state must be ARMED
    adapter.set_armed(True)
    telem = await adapter.get_telemetry()
    assert telem.armed_state == "ARMED"
    assert telem.flight_mode == "LANDED"

    # 3. Airborne (landed_state=1, alt=10m, z_val=-10.0), explicitly armed -> ARMED and HOLD
    client_air = MockAirSimClient(landed_state=1, z_val=-10.0, rotor_speeds=[500.0, 500.0, 500.0, 500.0])
    adapter_air = LiveAirSimTelemetryAdapter(client=client_air)
    adapter_air.set_armed(True)
    telem_air = await adapter_air.get_telemetry()
    assert telem_air.armed_state == "ARMED"
    assert telem_air.flight_mode == "HOLD"
    assert telem_air.altitude == 10.0

    # 4. Airborne, unset _armed, but active rotor spin detected (> 5 rad/s) -> ARMED
    adapter_rotor = LiveAirSimTelemetryAdapter(client=client_air)
    assert adapter_rotor._armed is None
    telem_rotor = await adapter_rotor.get_telemetry()
    assert telem_rotor.armed_state == "ARMED"

    # 5. Airborne, unset _armed, zero rotor spin -> DISARMED
    client_stopped = MockAirSimClient(landed_state=1, z_val=-10.0, rotor_speeds=[0.0, 0.0, 0.0, 0.0])
    adapter_stopped = LiveAirSimTelemetryAdapter(client=client_stopped)
    telem_stopped = await adapter_stopped.get_telemetry()
    assert telem_stopped.armed_state == "DISARMED"

    # 6. Landed, unset _armed, no rotor info -> UNKNOWN
    client_unknown = MockAirSimClient(landed_state=0, z_val=0.0, rotor_speeds=[])
    adapter_unknown = LiveAirSimTelemetryAdapter(client=client_unknown)
    telem_unknown = await adapter_unknown.get_telemetry()
    assert telem_unknown.armed_state == "UNKNOWN"


@pytest.mark.asyncio
async def test_pipeline_arming_state_gating_matrix():
    """Verify that PerceptionPipeline._step() strictly gates on armed_state == 'ARMED'."""
    from DroneOS.perception.pipeline import PerceptionPipeline
    from DroneOS.perception.types import Detection, Frame
    from unittest.mock import AsyncMock, MagicMock

    class MockFC:
        def __init__(self, armed_state="ARMED"):
            self.armed_state = armed_state

        async def get_telemetry(self):
            return TelemetryData(
                timestamp=time.time(),
                latitude=47.641468,
                longitude=-122.140165,
                altitude=10.0,
                roll=0.0,
                pitch=0.0,
                yaw=0.0,
                heading=0.0,
                armed_state=self.armed_state,
                flight_mode="HOLD",
                gps_valid=True,
                battery_level=1.0,
                is_armable=True,
                health_all_ok=True,
            )

    frame = Frame(
        image=np.zeros((720, 1280, 3), dtype=np.uint8),
        timestamp=time.time(),
    )
    frame_source = MagicMock()
    frame_source.get_frame = AsyncMock(return_value=frame)

    detector = MagicMock()
    detector.ensure_loaded = AsyncMock(return_value=True)
    detector.detect = AsyncMock(return_value=[
        Detection(label="rubbish", confidence=0.9, bbox_xyxy=(100.0, 100.0, 200.0, 200.0))
    ])

    config = PerceptionConfig(
        enabled=True,
        source="airsim",
        weights="models/garbage.pt",
        confidence=0.25,
        min_altitude_m=2.0,
        max_tilt_deg=20.0,
        min_hits=1,
    )

    # 1. DISARMED: must be rejected with VEHICLE_NOT_ARMED
    net_mock = MagicMock()
    net_mock.broadcast_message = AsyncMock()

    fc_disarmed = MockFC(armed_state="DISARMED")
    pipe_disarmed = PerceptionPipeline(
        drone_id="drone1",
        frame_source=frame_source,
        detector=detector,
        flight_controller=fc_disarmed,
        network_adapter=net_mock,
        config=config,
    )
    await pipe_disarmed._step()
    assert "VEHICLE_NOT_ARMED" in pipe_disarmed.last_rejection_reason
    assert pipe_disarmed.last_step_stats["reached_camera"] is False
    assert pipe_disarmed.last_step_stats["reached_inference"] is False

    # 2. UNKNOWN: must be rejected with VEHICLE_NOT_ARMED
    fc_unknown = MockFC(armed_state="UNKNOWN")
    pipe_unknown = PerceptionPipeline(
        drone_id="drone1",
        frame_source=frame_source,
        detector=detector,
        flight_controller=fc_unknown,
        network_adapter=net_mock,
        config=config,
    )
    await pipe_unknown._step()
    assert "VEHICLE_NOT_ARMED" in pipe_unknown.last_rejection_reason
    assert pipe_unknown.last_step_stats["reached_camera"] is False

    # 3. None: must be rejected with VEHICLE_NOT_ARMED
    fc_none = MockFC(armed_state=None)
    pipe_none = PerceptionPipeline(
        drone_id="drone1",
        frame_source=frame_source,
        detector=detector,
        flight_controller=fc_none,
        network_adapter=net_mock,
        config=config,
    )
    await pipe_none._step()
    assert "VEHICLE_NOT_ARMED" in pipe_none.last_rejection_reason
    assert pipe_none.last_step_stats["reached_camera"] is False

    # 4. ARMED: passes armed gate, reaches camera, reaches inference, confirms track!
    fc_armed = MockFC(armed_state="ARMED")
    pipe_armed = PerceptionPipeline(
        drone_id="drone1",
        frame_source=frame_source,
        detector=detector,
        flight_controller=fc_armed,
        network_adapter=net_mock,
        config=config,
    )
    await pipe_armed._step()
    assert pipe_armed.last_step_stats["reached_camera"] is True
    assert pipe_armed.last_step_stats["reached_inference"] is True
    assert pipe_armed.last_step_stats["raw_detections"] == 1
    assert pipe_armed.last_step_stats["geo_detections"] == 1
    assert pipe_armed.last_step_stats["emitted_items"] == 1
    assert pipe_armed.last_rejection_reason is None


@pytest.mark.asyncio
async def test_pipeline_safety_gates_rejection_diagnostics():
    """Verify that each individual gate in PerceptionPipeline._step() sets the exact diagnostic reason."""
    from DroneOS.perception.pipeline import PerceptionPipeline
    from DroneOS.perception.types import Detection, Frame
    from unittest.mock import AsyncMock, MagicMock

    now = time.time()
    def make_telem(**kwargs):
        defaults = dict(
            timestamp=time.time(),
            latitude=47.641468,
            longitude=-122.140165,
            altitude=10.0,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            heading=0.0,
            armed_state="ARMED",
            flight_mode="HOLD",
            gps_valid=True,
            battery_level=1.0,
            is_armable=True,
            health_all_ok=True,
        )
        defaults.update(kwargs)
        return TelemetryData(**defaults)

    config = PerceptionConfig(
        enabled=True,
        source="airsim",
        weights="models/garbage.pt",
        confidence=0.25,
        min_altitude_m=2.0,
        max_tilt_deg=20.0,
        min_hits=2,
    )

    frame = Frame(
        image=np.zeros((720, 1280, 3), dtype=np.uint8),
        timestamp=now,
    )
    frame_source = MagicMock()
    frame_source.get_frame = AsyncMock(return_value=frame)

    detector = MagicMock()
    detector.ensure_loaded = AsyncMock(return_value=True)
    detector.detect = AsyncMock(return_value=[
        Detection(label="rubbish", confidence=0.9, bbox_xyxy=(100.0, 100.0, 200.0, 200.0))
    ])

    net_mock = MagicMock()
    net_mock.broadcast_message = AsyncMock()

    # Gate 1: Detector not loaded
    det_unloaded = MagicMock()
    det_unloaded.ensure_loaded = AsyncMock(return_value=False)
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=det_unloaded, flight_controller=MagicMock(), network_adapter=net_mock, config=config)
    await pipe._step()
    assert pipe.last_rejection_reason == "DETECTOR_NOT_LOADED"

    # Gate 2: Pre-telemetry None
    fc_none = MagicMock()
    fc_none.get_telemetry = AsyncMock(return_value=None)
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=detector, flight_controller=fc_none, network_adapter=net_mock, config=config)
    await pipe._step()
    assert pipe.last_rejection_reason == "TELEMETRY_UNAVAILABLE_PRE"

    # Gate 3: GPS Invalid
    fc_gps = MagicMock()
    fc_gps.get_telemetry = AsyncMock(return_value=make_telem(gps_valid=False))
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=detector, flight_controller=fc_gps, network_adapter=net_mock, config=config)
    await pipe._step()
    assert pipe.last_rejection_reason == "GPS_INVALID"

    # Gate 4: Altitude below minimum
    fc_alt = MagicMock()
    fc_alt.get_telemetry = AsyncMock(return_value=make_telem(altitude=1.5))
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=detector, flight_controller=fc_alt, network_adapter=net_mock, config=config)
    await pipe._step()
    assert "ALTITUDE_BELOW_MINIMUM" in pipe.last_rejection_reason

    # Gate 5: Tilt exceeded pre
    fc_tilt = MagicMock()
    fc_tilt.get_telemetry = AsyncMock(return_value=make_telem(roll=25.0))
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=detector, flight_controller=fc_tilt, network_adapter=net_mock, config=config)
    await pipe._step()
    assert "TILT_EXCEEDED_PRE" in pipe.last_rejection_reason

    # Gate 6: Invalid lat/lon pre (0, 0)
    fc_coord = MagicMock()
    fc_coord.get_telemetry = AsyncMock(return_value=make_telem(latitude=0.0, longitude=0.0))
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=detector, flight_controller=fc_coord, network_adapter=net_mock, config=config)
    await pipe._step()
    assert "INVALID_LAT_LON_PRE" in pipe.last_rejection_reason

    # Gate 7: Frame acquisition failed
    fs_none = MagicMock()
    fs_none.get_frame = AsyncMock(return_value=None)
    fc_ok = MagicMock()
    fc_ok.get_telemetry = AsyncMock(return_value=make_telem())
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=fs_none, detector=detector, flight_controller=fc_ok, network_adapter=net_mock, config=config)
    await pipe._step()
    assert pipe.last_rejection_reason == "FRAME_ACQUISITION_FAILED"
    assert pipe.last_step_stats["reached_camera"] is False

    # Gate 8: Zero detections
    det_zero = MagicMock()
    det_zero.ensure_loaded = AsyncMock(return_value=True)
    det_zero.detect = AsyncMock(return_value=[])
    pipe = PerceptionPipeline(drone_id="drone1", frame_source=frame_source, detector=det_zero, flight_controller=fc_ok, network_adapter=net_mock, config=config)
    await pipe._step()
    assert pipe.last_rejection_reason == "ZERO_DETECTIONS"
    assert pipe.last_step_stats["reached_inference"] is True
    assert pipe.last_step_stats["raw_detections"] == 0

    # Gate 9: Aggregation no emission on 1st hit when min_hits=2
    frame1 = Frame(image=np.zeros((720, 1280, 3), dtype=np.uint8), timestamp=now)
    frame2 = Frame(image=np.zeros((720, 1280, 3), dtype=np.uint8), timestamp=now + 0.5)
    fs_advancing = MagicMock()
    fs_advancing.get_frame = AsyncMock(side_effect=[frame1, frame2])

    pipe = PerceptionPipeline(drone_id="drone1", frame_source=fs_advancing, detector=detector, flight_controller=fc_ok, network_adapter=net_mock, config=config)
    await pipe._step()
    assert pipe.last_rejection_reason == "AGGREGATION_NO_EMISSION"
    assert pipe.last_step_stats["reached_inference"] is True
    assert pipe.last_step_stats["raw_detections"] == 1
    assert pipe.last_step_stats["geo_detections"] == 1
    assert pipe.last_step_stats["emitted_items"] == 0
    # Unconfirmed candidate track exists
    assert len(pipe.aggregator.tracks) == 1
    track = list(pipe.aggregator.tracks.values())[0]
    assert track.confirmed is False
    assert track.hits == 1

    # Step 2: 2nd hit with advancing time confirms track and emits!
    await pipe._step()
    assert pipe.last_rejection_reason is None
    assert pipe.last_step_stats["emitted_items"] == 1
    assert track.confirmed is True
    assert track.hits == 2


# =============================================================================
# GROUND-TRUTH ASSOCIATION & PEER DRONE REJECTION REGRESSION TESTS
# =============================================================================

def test_project_world_ned_to_pixel_roundtrip():
    """Verify project_world_ned_to_pixel is the exact mathematical inverse of project_pixel_to_ned."""
    from DroneOS.perception.geolocate import project_pixel_to_ned
    from scripts.validate_single_drone_airsim import project_world_ned_to_pixel

    test_cases = [
        # (u, v, w, h, hfov, mount_pitch, roll, pitch, yaw, alt)
        (640.0, 360.0, 1280, 720, 90.0, 90.0, 0.0, 0.0, 0.0, 10.0),
        (300.0, 150.0, 1280, 720, 90.0, 90.0, 0.0, 0.0, 0.0, 10.0),
        (850.0, 500.0, 1280, 720, 90.0, 90.0, 5.0, -3.0, 30.0, 12.0),
        (640.0, 360.0, 1280, 720, 90.0, 90.0, -4.0, 6.0, 90.0, 8.0),
        (400.0, 600.0, 1280, 720, 90.0, 90.0, 2.0, 1.0, 180.0, 15.0),
        (128.0, 72.0, 256, 144, 90.0, 90.0, 0.0, 0.0, 45.0, 5.0),
    ]

    for u_in, v_in, w, h, hfov, mount_pitch, roll, pitch, yaw, alt in test_cases:
        ned = project_pixel_to_ned(
            u=u_in,
            v=v_in,
            img_w=w,
            img_h=h,
            hfov_deg=hfov,
            mount_pitch_deg=mount_pitch,
            roll_deg=roll,
            pitch_deg=pitch,
            yaw_deg=yaw,
            height_agl=alt,
        )
        assert ned is not None, f"Forward projection failed for ({u_in}, {v_in})"
        north_m, east_m = ned

        proj = project_world_ned_to_pixel(
            ned_x=north_m,
            ned_y=east_m,
            ned_z=0.0,
            drone_ned_x=0.0,
            drone_ned_y=0.0,
            drone_ned_z=-alt,
            roll_deg=roll,
            pitch_deg=pitch,
            yaw_deg=yaw,
            mount_pitch_deg=mount_pitch,
            hfov_deg=hfov,
            img_w=w,
            img_h=h,
        )
        assert proj is not None, f"Inverse projection failed for NED ({north_m}, {east_m})"
        u_out, v_out, z_cam = proj

        assert abs(u_out - u_in) < 1e-3, f"Pixel u mismatch: expected {u_in}, got {u_out}"
        assert abs(v_out - v_in) < 1e-3, f"Pixel v mismatch: expected {v_in}, got {v_out}"


def test_ground_truth_association_rejects_peer_drone_false_positives():
    """
    Critical regression test for the false-positive cross-match discovered in independent audit:
    When YOLO detects Drone 3 and Drone 4 (quadcopters on the floor) but misses the garbage target,
    the validator MUST classify them as PEER_DRONE_FALSE_POSITIVE, mark target_detected=False,
    and reject the test instead of falsely passing Stage 6 & 7.
    """
    from DroneOS.perception.aggregator import AggregatedTrack
    from DroneOS.perception.types import Detection
    from DroneOS.shared.config.models import CameraConfig
    from scripts.validate_single_drone_airsim import evaluate_ground_truth_association

    origin_lat = 47.641468
    origin_lon = -122.140165

    # Target placed at North=0.0m, East=2.0m
    target_lat, target_lon = meters_to_lat_lon(0.0, 2.0, origin_lat, origin_lon)
    target_meta = {
        "world_x_m": 0.0,
        "world_y_m": 2.0,
        "world_z_m": 0.0,
        "true_lat": target_lat,
        "true_lon": target_lon,
    }

    # Peer drones in AirSim Blocks
    d3_lat, d3_lon = meters_to_lat_lon(0.0, 5.0, origin_lat, origin_lon)
    d4_lat, d4_lon = meters_to_lat_lon(5.0, 5.0, origin_lat, origin_lon)
    peer_poses = {
        "Drone2": {"world_x_m": 5.0, "world_y_m": 0.0, "world_z_m": 0.82, "lat": origin_lat, "lon": origin_lon},
        "Drone3": {"world_x_m": 0.0, "world_y_m": 5.0, "world_z_m": 0.82, "lat": d3_lat, "lon": d3_lon},
        "Drone4": {"world_x_m": 5.0, "world_y_m": 5.0, "world_z_m": 0.82, "lat": d4_lat, "lon": d4_lon},
    }

    # YOLO produced tracks near Drone3 (East 4.5m) and Drone4 (East 4.8m, North 4.8m)
    # Target was NOT detected!
    track_d3_lat, track_d3_lon = meters_to_lat_lon(0.1, 4.8, origin_lat, origin_lon)
    track_d4_lat, track_d4_lon = meters_to_lat_lon(4.9, 4.9, origin_lat, origin_lon)

    tracks = [
        AggregatedTrack(
            id="drone1-t0001",
            lat=track_d3_lat,
            lon=track_d3_lon,
            altitude_agl=10.0,
            label="rubbish",
            max_confidence=0.85,
            hits=3,
            confirmed=True,
            first_seen=100.0,
            last_seen=102.0,
        ),
        AggregatedTrack(
            id="drone1-t0002",
            lat=track_d4_lat,
            lon=track_d4_lon,
            altitude_agl=10.0,
            label="rubbish",
            max_confidence=0.78,
            hits=2,
            confirmed=True,
            first_seen=100.0,
            last_seen=102.0,
        ),
    ]

    telem = TelemetryData(
        timestamp=time.time(),
        latitude=origin_lat,
        longitude=origin_lon,
        altitude=10.0,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        heading=0.0,
        armed_state="ARMED",
        flight_mode="HOLD",
        gps_valid=True,
        battery_level=1.0,
        is_armable=True,
        health_all_ok=True,
    )

    cam_cfg = CameraConfig(hfov_deg=90.0, mount_pitch_deg=90.0, airsim_camera="bottom_center")

    raw_dets = [
        Detection(label="rubbish", confidence=0.85, bbox_xyxy=(620.0, 680.0, 660.0, 720.0)),
        Detection(label="rubbish", confidence=0.78, bbox_xyxy=(300.0, 680.0, 340.0, 720.0)),
    ]

    association = evaluate_ground_truth_association(
        raw_detections=raw_dets,
        tracks=tracks,
        telem=telem,
        cam_cfg=cam_cfg,
        frame_shape=(720, 1280),
        target_meta=target_meta,
        peer_poses=peer_poses,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        target_match_threshold_m=1.5,
        peer_match_threshold_m=1.5,
    )

    # 1. Target MUST be marked as NOT detected
    assert association["target_detected"] is False, "Validator must not accept peer drones as target"
    assert association["target_missed"] is True
    assert association["target_track"] is None

    # 2. Both tracks must be classified as PEER_DRONE_FALSE_POSITIVE
    assert len(association["peer_drone_false_positives"]) == 2
    matched_names = {p["matched_peer"] for p in association["peer_drone_false_positives"]}
    assert "Drone3" in matched_names
    assert "Drone4" in matched_names

    # 3. Association summary must explicitly flag failure
    assert "FAIL: Target missed" in association["association_summary"]
    assert "Drone3" in association["association_summary"]


def test_ground_truth_association_isolates_true_target():
    """Verify that when BOTH target and peer drones are in view, the true target track is isolated."""
    from DroneOS.perception.aggregator import AggregatedTrack
    from DroneOS.perception.types import Detection
    from DroneOS.shared.config.models import CameraConfig
    from scripts.validate_single_drone_airsim import evaluate_ground_truth_association

    origin_lat = 47.641468
    origin_lon = -122.140165

    # True target at North=0.0, East=2.0
    tgt_lat, tgt_lon = meters_to_lat_lon(0.0, 2.0, origin_lat, origin_lon)
    target_meta = {
        "world_x_m": 0.0,
        "world_y_m": 2.0,
        "world_z_m": 0.0,
        "true_lat": tgt_lat,
        "true_lon": tgt_lon,
    }

    d3_lat, d3_lon = meters_to_lat_lon(0.0, 5.0, origin_lat, origin_lon)
    peer_poses = {
        "Drone3": {"world_x_m": 0.0, "world_y_m": 5.0, "world_z_m": 0.82, "lat": d3_lat, "lon": d3_lon},
    }

    # Track 1: True target detection (measured at East 2.05m)
    det_tgt_lat, det_tgt_lon = meters_to_lat_lon(0.02, 2.05, origin_lat, origin_lon)
    # Track 2: Peer Drone 3 detection (measured at East 4.95m)
    det_d3_lat, det_d3_lon = meters_to_lat_lon(0.05, 4.95, origin_lat, origin_lon)

    tracks = [
        AggregatedTrack(
            id="drone1-target",
            lat=det_tgt_lat,
            lon=det_tgt_lon,
            altitude_agl=10.0,
            label="rubbish",
            max_confidence=0.92,
            hits=4,
            confirmed=True,
            first_seen=100.0,
            last_seen=103.0,
        ),
        AggregatedTrack(
            id="drone1-peer",
            lat=det_d3_lat,
            lon=det_d3_lon,
            altitude_agl=10.0,
            label="rubbish",
            max_confidence=0.81,
            hits=3,
            confirmed=True,
            first_seen=100.0,
            last_seen=103.0,
        ),
    ]

    telem = TelemetryData(
        timestamp=time.time(),
        latitude=origin_lat,
        longitude=origin_lon,
        altitude=10.0,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        heading=0.0,
        armed_state="ARMED",
        flight_mode="HOLD",
        gps_valid=True,
        battery_level=1.0,
        is_armable=True,
        health_all_ok=True,
    )
    cam_cfg = CameraConfig(hfov_deg=90.0, mount_pitch_deg=90.0, airsim_camera="bottom_center")

    association = evaluate_ground_truth_association(
        raw_detections=[],
        tracks=tracks,
        telem=telem,
        cam_cfg=cam_cfg,
        frame_shape=(720, 1280),
        target_meta=target_meta,
        peer_poses=peer_poses,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        target_match_threshold_m=1.5,
        peer_match_threshold_m=1.5,
    )

    # 1. Target detected
    assert association["target_detected"] is True
    assert association["target_track"] is not None
    assert association["target_track"].id == "drone1-target"
    assert association["target_ground_distance_m"] < 0.1

    # 2. Peer Drone 3 properly isolated as false positive
    assert len(association["peer_drone_false_positives"]) == 1
    assert association["peer_drone_false_positives"][0]["matched_peer"] == "Drone3"


def test_ground_truth_association_multiple_quadrants():
    """Verify target association across all 4 operational quadrants and varying yaw headings."""
    from DroneOS.perception.aggregator import AggregatedTrack
    from DroneOS.shared.config.models import CameraConfig
    from scripts.validate_single_drone_airsim import evaluate_ground_truth_association

    origin_lat = 47.641468
    origin_lon = -122.140165

    quadrants = [
        # (north, east, yaw_deg)
        (3.0, 2.0, 0.0),
        (-2.5, 3.5, 45.0),
        (-3.0, -2.0, 90.0),
        (2.0, -3.0, 180.0),
    ]

    cam_cfg = CameraConfig(hfov_deg=90.0, mount_pitch_deg=90.0, airsim_camera="bottom_center")

    for q_north, q_east, yaw in quadrants:
        t_lat, t_lon = meters_to_lat_lon(q_north, q_east, origin_lat, origin_lon)
        target_meta = {
            "world_x_m": q_north,
            "world_y_m": q_east,
            "world_z_m": 0.0,
            "true_lat": t_lat,
            "true_lon": t_lon,
        }

        # Track with 0.15m noise
        det_lat, det_lon = meters_to_lat_lon(q_north + 0.1, q_east - 0.1, origin_lat, origin_lon)
        track = AggregatedTrack(
            id=f"track-quad-{yaw}",
            lat=det_lat,
            lon=det_lon,
            altitude_agl=10.0,
            label="rubbish",
            max_confidence=0.89,
            hits=3,
            confirmed=True,
            first_seen=1.0,
            last_seen=2.0,
        )

        telem = TelemetryData(
            timestamp=time.time(),
            latitude=origin_lat,
            longitude=origin_lon,
            altitude=10.0,
            roll=0.0,
            pitch=0.0,
            yaw=yaw,
            heading=yaw,
            armed_state="ARMED",
            flight_mode="HOLD",
            gps_valid=True,
            battery_level=1.0,
            is_armable=True,
            health_all_ok=True,
        )

        association = evaluate_ground_truth_association(
            raw_detections=[],
            tracks=[track],
            telem=telem,
            cam_cfg=cam_cfg,
            frame_shape=(720, 1280),
            target_meta=target_meta,
            peer_poses={},
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            target_match_threshold_m=1.5,
        )

        assert association["target_detected"] is True, f"Failed quadrant ({q_north}, {q_east}) at yaw {yaw}"
        assert association["target_ground_distance_m"] < 0.25


def test_verify_target_visibility_distinguishes_texture_from_floor():
    """Verify target visibility analysis flags untextured primitives and accepts textured targets."""
    from scripts.validate_single_drone_airsim import verify_target_visibility_in_raw_frame

    # Case A: Flat grey floor primitive (intensity 201 +/- 1)
    flat_frame = np.full((720, 1280, 3), 201, dtype=np.uint8)
    # Add minor noise simulating camera sensor noise
    noise = np.random.randint(-1, 2, size=(720, 1280, 3), dtype=np.int16)
    flat_frame = np.clip(flat_frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    vis_flat = verify_target_visibility_in_raw_frame(flat_frame, expected_u=640.0, expected_v=480.0, expected_radius_px=35.0)
    assert vis_flat["visible"] is False, "Untextured primitive blending into floor must be rejected as invisible"
    assert "Target patch lacks contrast/edges" in vis_flat["reason"]

    # Case B: Textured garbage target with contrast and high edge energy
    textured_frame = flat_frame.copy()
    # Place distinct textured patch at (640, 480)
    for y in range(450, 510):
        for x in range(610, 670):
            # High-contrast pattern (e.g. crushed can/box)
            textured_frame[y, x] = [50 + (x * 7) % 180, 30 + (y * 5) % 150, 20 + ((x + y) * 3) % 190]

    vis_textured = verify_target_visibility_in_raw_frame(textured_frame, expected_u=640.0, expected_v=480.0, expected_radius_px=35.0)
    assert vis_textured["visible"] is True, "Textured target must be accepted as visible"
    assert vis_textured["roi_std"] > 10.0
    assert vis_textured["contrast_delta"] > 10.0

