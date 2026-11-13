"""
Tests for Phase 2: Multi-Drone (4 Drones) Concurrent Perception & Cross-Drone Spatial Fusion.

Validates:
1. Four-drone concurrent perception isolation (zero state leakage, unique sender IDs and track IDs).
2. Cross-drone GCS spatial fusion under 2.0m merge radius (Target Alpha corroborated by Drone1 and Drone2).
3. Cross-drone spatial separation over 2.0m threshold (Target Beta at 2.8m kept distinct).
4. Repeated sightings in-place (Rule A: confidence/hits/lastSeen update without marker proliferation).
5. Fault injection mid-session: Camera disconnect on Drone2 (graceful skip, other drones unaffected).
6. Fault injection mid-session: GPS fix loss on Drone3 (safety gating suppresses broadcasts, recovery on fix).
7. Live AirSim probe verification: truthful BLOCKED status reporting when port 41451 is offline.
"""

import asyncio
import math
import time
from typing import Dict, List, Optional
import numpy as np
import pytest

from DroneOS.perception.frame_sources import FrameSource
from DroneOS.perception.geolocate import pixel_to_lat_lon
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.types import Detection, Frame
from DroneOS.shared.config.models import CameraConfig, PerceptionConfig
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage, TelemetryData


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

class MockCameraSource(FrameSource):
    def __init__(self, vehicle_name: str, width: int = 1280, height: int = 720):
        self.vehicle_name = vehicle_name
        self.width = width
        self.height = height
        self.connected = True
        self.is_synthetic = False
        self._timestamp = time.time()

    async def get_frame(self) -> Optional[Frame]:
        if not self.connected:
            return None
        self._timestamp += 0.05
        return Frame(image=np.zeros((self.height, self.width, 3), dtype=np.uint8), timestamp=self._timestamp)

    async def close(self):
        self.connected = False


class MockFlightController:
    def __init__(self, drone_id: str, lat: float, lon: float, alt: float = 15.0):
        self.drone_id = drone_id
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.armed_state = "ARMED"
        self.gps_valid = True
        self.source: Optional[MockCameraSource] = None

    async def get_telemetry(self) -> TelemetryData:
        ts = self.source._timestamp if self.source is not None else time.time()
        return TelemetryData(
            flight_mode="OFFBOARD",
            armed_state=self.armed_state,
            gps_valid=self.gps_valid,
            altitude=self.alt,
            latitude=self.lat,
            longitude=self.lon,
            roll=self.roll,
            pitch=self.pitch,
            yaw=self.yaw,
            heading=self.yaw,
            timestamp=ts,
        )


class MockDetector:
    def __init__(self, detections: Optional[List[Detection]] = None):
        self.detections = detections if detections is not None else []
        self._loaded = True

    async def ensure_loaded(self) -> bool:
        return True

    async def detect(self, image: np.ndarray) -> List[Detection]:
        return self.detections

    def close(self):
        pass


class MockNetwork:
    def __init__(self):
        self.sent_messages: List[DetectionMessage] = []

    async def broadcast_message(self, msg: DetectionMessage):
        self.sent_messages.append(msg)


class GCSFusionEngine:
    """Simulates React Native Ground Control Station fusion logic (mobile/src/utils/garbage.js)."""
    def __init__(self, merge_radius_m: float = 2.0):
        self.merge_radius_m = merge_radius_m
        self.garbage: Dict[str, dict] = {}

    def _geo_dist(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371000.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2.0) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2.0) ** 2
        )
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return R * c

    def merge_message(self, msg: DetectionMessage):
        now_ms = int(time.time() * 1000)
        drone_id = msg.sender_id

        for raw_item in msg.detections:
            # Rule A: Same ID
            if raw_item.id in self.garbage:
                existing = self.garbage[raw_item.id]
                if drone_id not in existing["seenBy"]:
                    existing["seenBy"].append(drone_id)
                existing["confidence"] = max(existing["confidence"], raw_item.confidence)
                existing["hits"] = max(existing["hits"], raw_item.hits)
                existing["lastSeen"] = now_ms
                continue

            # Rule B: Different ID within merge radius -> same physical target
            matched_key = None
            min_dist = float("inf")
            for key, existing in self.garbage.items():
                d = self._geo_dist(raw_item.lat, raw_item.lon, existing["lat"], existing["lon"])
                if d <= self.merge_radius_m and d < min_dist:
                    min_dist = d
                    matched_key = key

            if matched_key is not None:
                existing = self.garbage[matched_key]
                if drone_id not in existing["seenBy"]:
                    existing["seenBy"].append(drone_id)
                existing["confidence"] = max(existing["confidence"], raw_item.confidence)
                existing["hits"] += raw_item.hits
                existing["lastSeen"] = now_ms
                continue

            # Rule C: New distinct target
            self.garbage[raw_item.id] = {
                "id": raw_item.id,
                "label": raw_item.label,
                "confidence": raw_item.confidence,
                "lat": raw_item.lat,
                "lon": raw_item.lon,
                "hits": raw_item.hits,
                "altitude_agl": raw_item.altitude_agl,
                "firstSeen": now_ms,
                "lastSeen": now_ms,
                "reportingDrone": drone_id,
                "seenBy": [drone_id],
                "synthetic": msg.synthetic,
            }


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_four_drone_concurrent_perception_isolation():
    """Verify 4 concurrent drone pipelines run concurrently with isolated state and distinct IDs."""
    pipelines = []
    networks = []
    base_lat = 22.3149
    base_lon = 87.3105

    for i in range(4):
        node_id = f"drone{i+1}"
        src = MockCameraSource(f"Drone{i+1}")
        fc = MockFlightController(node_id, base_lat + i * 0.0001, base_lon + i * 0.0001)
        fc.source = src
        net = MockNetwork()
        networks.append(net)
        det = MockDetector([Detection(bbox_xyxy=(630.0, 350.0, 650.0, 370.0), confidence=0.85, label="rubbish")])
        cfg = PerceptionConfig(
            enabled=True,
            min_hits=1,
            camera=CameraConfig(mount_pitch_deg=90.0, hfov_deg=90.0),
        )
        pipe = PerceptionPipeline(node_id, src, det, fc, net, cfg)
        pipelines.append(pipe)

    # Step all 4 pipelines concurrently
    await asyncio.gather(*(p._step() for p in pipelines))

    # Verify each published exactly one message with its own unique sender_id and track prefix
    for i in range(4):
        node_id = f"drone{i+1}"
        net = networks[i]
        assert len(net.sent_messages) == 1
        msg = net.sent_messages[0]
        assert msg.sender_id == node_id
        assert len(msg.detections) == 1
        assert msg.detections[0].id.startswith(f"{node_id}-g")
        assert msg.detections[0].label == "rubbish"


@pytest.mark.asyncio
async def test_cross_drone_gcs_fusion_merging_under_2m():
    """Verify that sightings within 2.0m from two different drones merge into a single GCS marker."""
    gcs = GCSFusionEngine(merge_radius_m=2.0)

    # Drone1 observes Target Alpha
    msg1 = DetectionMessage(
        sender_id="drone1",
        timestamp=time.time(),
        detections=[
            DetectionItem(
                id="drone1-g0001",
                label="rubbish",
                confidence=0.82,
                lat=22.3149000,
                lon=87.3105000,
                hits=1,
                status="confirmed",
                altitude_agl=15.0,
            )
        ],
        synthetic=False,
    )
    gcs.merge_message(msg1)
    assert len(gcs.garbage) == 1
    assert gcs.garbage["drone1-g0001"]["seenBy"] == ["drone1"]

    # Drone2 observes Target Alpha at ~0.78m offset
    msg2 = DetectionMessage(
        sender_id="drone2",
        timestamp=time.time(),
        detections=[
            DetectionItem(
                id="drone2-g0001",
                label="rubbish",
                confidence=0.89,
                lat=22.3149050,
                lon=87.3105050,
                hits=1,
                status="confirmed",
                altitude_agl=15.0,
            )
        ],
        synthetic=False,
    )
    gcs.merge_message(msg2)

    # Must still be exactly 1 marker in GCS!
    assert len(gcs.garbage) == 1
    marker = gcs.garbage["drone1-g0001"]
    assert "drone1" in marker["seenBy"]
    assert "drone2" in marker["seenBy"]
    assert marker["confidence"] == 0.89
    assert marker["hits"] == 2


@pytest.mark.asyncio
async def test_cross_drone_gcs_fusion_spatial_separation_over_2m():
    """Verify that sightings separated by > 2.0m remain distinct GCS markers."""
    gcs = GCSFusionEngine(merge_radius_m=2.0)

    # Drone1 observes Target Alpha
    msg1 = DetectionMessage(
        sender_id="drone1",
        timestamp=time.time(),
        detections=[
            DetectionItem(
                id="drone1-g0001",
                label="rubbish",
                confidence=0.85,
                lat=22.3149000,
                lon=87.3105000,
                hits=1,
                status="confirmed",
                altitude_agl=15.0,
            )
        ],
        synthetic=False,
    )
    gcs.merge_message(msg1)

    # Drone4 observes Target Beta at 2.8m separation (2.8m / 111132.92 = 0.0000252 deg lat)
    msg4 = DetectionMessage(
        sender_id="drone4",
        timestamp=time.time(),
        detections=[
            DetectionItem(
                id="drone4-g0001",
                label="rubbish",
                confidence=0.92,
                lat=22.3149252,
                lon=87.3105000,
                hits=1,
                status="confirmed",
                altitude_agl=15.0,
            )
        ],
        synthetic=False,
    )
    gcs.merge_message(msg4)

    # Must be 2 distinct markers
    assert len(gcs.garbage) == 2
    assert "drone1-g0001" in gcs.garbage
    assert "drone4-g0001" in gcs.garbage
    assert gcs.garbage["drone1-g0001"]["seenBy"] == ["drone1"]
    assert gcs.garbage["drone4-g0001"]["seenBy"] == ["drone4"]


@pytest.mark.asyncio
async def test_repeated_sightings_in_place_no_duplicate_markers():
    """Verify Rule A updates existing marker confidence and hits without creating duplicate markers."""
    gcs = GCSFusionEngine(merge_radius_m=2.0)

    for cycle in range(1, 10):
        msg = DetectionMessage(
            sender_id="drone1",
            timestamp=time.time(),
            detections=[
                DetectionItem(
                    id="drone1-g0001",
                    label="rubbish",
                    confidence=0.75 + (cycle * 0.02),
                    lat=22.3149000,
                    lon=87.3105000,
                    hits=cycle,
                    status="confirmed",
                    altitude_agl=15.0,
                )
            ],
            synthetic=False,
        )
        gcs.merge_message(msg)

    assert len(gcs.garbage) == 1
    marker = gcs.garbage["drone1-g0001"]
    assert marker["hits"] == 9
    assert marker["confidence"] == pytest.approx(0.93, abs=0.01)


@pytest.mark.asyncio
async def test_fault_injection_camera_disconnect_and_reconnect():
    """Verify pipeline handles camera disconnect safely and resumes upon reconnection."""
    src = MockCameraSource("Drone2")
    fc = MockFlightController("drone2", 22.3149, 87.3105)
    fc.source = src
    net = MockNetwork()
    det = MockDetector([Detection(bbox_xyxy=(630.0, 350.0, 650.0, 370.0), confidence=0.88, label="rubbish")])
    cfg = PerceptionConfig(
        enabled=True,
        min_hits=1,
        update_interval_s=0.0,
        camera=CameraConfig(mount_pitch_deg=90.0, hfov_deg=90.0),
    )
    pipe = PerceptionPipeline("drone2", src, det, fc, net, cfg)

    # 1. Normal step
    await pipe._step()
    assert len(net.sent_messages) == 1

    # 2. Camera drops offline
    src.connected = False
    await pipe._step()
    # No new message broadcast, no exception raised
    assert len(net.sent_messages) == 1

    # 3. Camera reconnects
    src.connected = True
    await pipe._step()
    # Detection resumes
    assert len(net.sent_messages) == 2


@pytest.mark.asyncio
async def test_fault_injection_gps_loss_suppresses_broadcast():
    """Verify safety gating suppresses detection broadcast when telemetry reports gps_valid=False."""
    src = MockCameraSource("Drone3")
    fc = MockFlightController("drone3", 22.3149, 87.3105)
    fc.source = src
    net = MockNetwork()
    det = MockDetector([Detection(bbox_xyxy=(630.0, 350.0, 650.0, 370.0), confidence=0.88, label="rubbish")])
    cfg = PerceptionConfig(
        enabled=True,
        min_hits=1,
        update_interval_s=0.0,
        camera=CameraConfig(mount_pitch_deg=90.0, hfov_deg=90.0),
    )
    pipe = PerceptionPipeline("drone3", src, det, fc, net, cfg)

    # 1. Normal step with valid GPS
    await pipe._step()
    assert len(net.sent_messages) == 1

    # 2. GPS fix lost
    fc.gps_valid = False
    await pipe._step()
    # Gating suppressed frame processing; no message broadcast
    assert len(net.sent_messages) == 1

    # 3. GPS restored
    fc.gps_valid = True
    await pipe._step()
    # Processing resumes
    assert len(net.sent_messages) == 2


def test_airsim_probe_reports_truthful_state():
    """Verify that scripts/verify_live_airsim.py truthfully records state in its report."""
    import json
    from pathlib import Path
    report_file = Path("ml/reports/live_airsim_probe.json")
    assert report_file.exists(), "live_airsim_probe.json report must exist"
    data = json.loads(report_file.read_text())
    assert data["status"] in ("CONNECTED", "BLOCKED")
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 41451
    assert data["connection_attempts"] >= 1
    if data["status"] == "CONNECTED":
        assert data["rpc_reachable"] is True
        assert "Drone1" in data["vehicles"]
        assert data["vehicles"]["Drone1"]["connected"] is True
    else:
        assert "WSAECONNREFUSED" in data["error_message"] or "refused" in data["error_message"].lower()
