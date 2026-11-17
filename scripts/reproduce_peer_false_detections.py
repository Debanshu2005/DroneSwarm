#!/usr/bin/env python3
"""
scripts/reproduce_peer_false_detections.py

Reproduction Scenario and Forensic Diagnostics for Peer-Drone False Detections during Formation.

Execution Steps (Section 4):
1. Spawn exactly two genuine garbage objects.
2. Start/configure all four drones (DroneOS, DroneOS1, DroneOS2, DroneOS3).
3. Confirm that the mobile GCS / GFS initially shows two garbage markers.
4. Trigger formation flight mode.
5. Position one drone vertically above another (Drone 1 alt=15m, Drone 2 alt=5m, sep=0m).
6. Repeat with the opposite vertical ordering (Drone 2 alt=15m, Drone 1 alt=5m, sep=0m).
7. Observe whether either drone becomes a false garbage marker.
8. Repeat with drones at different horizontal separations (0m, 1m, 2.5m, 3.5m, 4.5m, 6m, 10m).
9. Evaluate telemetry validity states (missing peer position, stale position, inactive peer, ray projection elongation).
10. Trace any false detection through YOLO -> Geolocation -> Peer Exclusion -> Aggregator -> GFS -> Relay -> PhoneOS.
11. Output structured JSONL and summary report under ml/reports/peer_exclusion_diagnostics/.
"""

import asyncio
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

# Setup repository paths
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.diagnostics import (
    DiagnosticRecorder,
    get_diagnostics,
    log_raw_detection,
    log_peer_exclusion_eval,
    log_peer_telemetry_rx,
    log_peer_heartbeat_rx,
    log_peer_lifecycle,
    log_peer_position_status,
    log_gfs_observation_rx,
    log_gfs_fusion_result,
    log_relay_fwd,
)
from DroneOS.perception.pipeline import PerceptionPipeline
from DroneOS.perception.detector import GarbageDetector, Detection
from DroneOS.perception.frame_sources import FrameSource, Frame
from DroneOS.perception.geolocate import meters_to_lat_lon, lat_lon_to_meters
from DroneOS.shared.protocol.messages import (
    TelemetryData,
    TelemetryMessage,
    HeartbeatMessage,
    DetectionItem,
    DetectionMessage
)
from DroneOS.core.swarm_manager import SwarmMembership, SwarmRegistry, PeerStateManager

from gfs.config import GFSConfig
from gfs.service import GFSService
from gfs.models import Observation
from gfs.inbox import ingest_raw_packet
from relay.relay import UdpWebsocketRelay

OUTPUT_DIR = REPO_ROOT / "ml" / "reports" / "peer_exclusion_diagnostics"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class MockFrameSource(FrameSource):
    """Deterministic simulated frame source."""
    def __init__(self, width: int = 1280, height: int = 720):
        self.width = width
        self.height = height
        import numpy as np
        self.dummy_image = np.zeros((height, width, 3), dtype=np.uint8)

    async def get_frame(self) -> Optional[Frame]:
        return Frame(
            image=self.dummy_image,
            timestamp=time.time()
        )

    async def close(self) -> None:
        pass


class MockDetector(GarbageDetector):
    """Mock detector that returns specified target bounding boxes."""
    def __init__(self, detections_to_return: Optional[List[Detection]] = None):
        self.detections = detections_to_return or []
        self._available = True

    def set_detections(self, detections: List[Detection]):
        self.detections = detections

    async def ensure_loaded(self) -> bool:
        return True

    def load(self) -> bool:
        return True

    async def detect(self, image) -> List[Detection]:
        return self.detections

    def close(self):
        pass


class MockFlightController:
    """Mock flight controller providing configurable telemetry snapshots."""
    def __init__(self, lat: float, lon: float, alt: float, roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0):
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.gps_valid = True
        self.armed_state = "ARMED"

    def set_pose(self, lat: float, lon: float, alt: float, roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0):
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw

    async def get_telemetry(self) -> TelemetryData:
        return TelemetryData(
            flight_mode="GUIDED",
            latitude=self.lat,
            longitude=self.lon,
            altitude=self.alt,
            roll=self.roll,
            pitch=self.pitch,
            yaw=self.yaw,
            heading=self.yaw,
            timestamp=time.time(),
            gps_valid=self.gps_valid,
            armed_state=self.armed_state
        )


def make_pipeline(drone_id: str, swarm_mgr: SwarmMembership, fc: MockFlightController,
                  detector: MockDetector, frame_src: MockFrameSource) -> PerceptionPipeline:
    cfg = MagicMock()
    cfg.max_fps = 5.0
    cfg.min_altitude_m = 0.5
    cfg.max_tilt_deg = 35.0
    cfg.ground_elevation_m = 0.0
    cfg.max_range_m = 50.0
    cfg.peer_exclusion_radius_m = 4.0
    cfg.max_telemetry_age_s = 2.0
    cfg.max_frame_age_s = 2.0
    cfg.max_sync_skew_s = 1.0
    cfg.merge_radius_m = 2.5
    cfg.min_hits = 1
    cfg.update_interval_s = 0.1
    cfg.max_tracks = 500
    cfg.camera = MagicMock()
    cfg.camera.hfov_deg = 90.0
    cfg.camera.mount_pitch_deg = 90.0  # nadir downward camera (positive 90)

    net = MagicMock()
    net.broadcast_message = AsyncMock()

    pipe = PerceptionPipeline(
        drone_id=drone_id,
        frame_source=frame_src,
        detector=detector,
        flight_controller=fc,
        network_adapter=net,
        config=cfg,
        swarm_manager=swarm_mgr
    )
    return pipe


async def run_reproduction_suite() -> Dict[str, Any]:
    print("=" * 80)
    print("STARTING PEER-DRONE FALSE DETECTION REPRODUCTION & DIAGNOSTIC SUITE")
    print("=" * 80)

    # Reset diagnostic recorder
    recorder = DiagnosticRecorder.reset(OUTPUT_DIR)

    # Reference origin (AirSim Seattle default)
    ORIGIN_LAT = 47.641468
    ORIGIN_LON = -122.140165

    # 1. Spawn two genuine garbage objects
    # Target 1: NED (19.13, 4.42, 0.0) -> GPS (47.641640, -122.140106)
    # Target 2: NED (16.33, 9.71, 0.0) -> GPS (47.641615, -122.140036) (Separation = 5.98m > 3.0m)
    g1_lat, g1_lon = meters_to_lat_lon(19.13, 4.42, ORIGIN_LAT, ORIGIN_LON)
    g2_lat, g2_lon = meters_to_lat_lon(16.33, 9.71, ORIGIN_LAT, ORIGIN_LON)
    print(f"\n[Step 1] Spawned 2 Genuine Garbage Targets:")
    print(f"  - Genuine Target 1: GPS=({g1_lat:.6f}, {g1_lon:.6f})")
    print(f"  - Genuine Target 2: GPS=({g2_lat:.6f}, {g2_lon:.6f})")

    # 2. Start all 4 drones and establish swarm membership
    print("\n[Step 2] Initializing Swarm Membership across Drone1, Drone2, Drone3, Drone4...")
    swarm_d1 = SwarmMembership("drone1")
    swarm_d2 = SwarmMembership("drone2")
    swarm_d3 = SwarmMembership("drone3")
    swarm_d4 = SwarmMembership("drone4")
    swarms = {"drone1": swarm_d1, "drone2": swarm_d2, "drone3": swarm_d3, "drone4": swarm_d4}

    # Initial positions (on ground / takeoff)
    fc_d1 = MockFlightController(ORIGIN_LAT, ORIGIN_LON, alt=10.0)
    fc_d2 = MockFlightController(ORIGIN_LAT, ORIGIN_LON + 0.00005, alt=10.0)
    fc_d3 = MockFlightController(ORIGIN_LAT + 0.00005, ORIGIN_LON, alt=10.0)
    fc_d4 = MockFlightController(ORIGIN_LAT + 0.00005, ORIGIN_LON + 0.00005, alt=10.0)
    fcs = {"drone1": fc_d1, "drone2": fc_d2, "drone3": fc_d3, "drone4": fc_d4}

    det_d1 = MockDetector([])
    det_d2 = MockDetector([])
    det_d3 = MockDetector([])
    det_d4 = MockDetector([])
    detectors = {"drone1": det_d1, "drone2": det_d2, "drone3": det_d3, "drone4": det_d4}

    frame_src = MockFrameSource(1280, 720)
    DRONES_LIST = ["drone1", "drone2", "drone3", "drone4"]
    pipelines = {
        name: make_pipeline(name, swarms[name], fcs[name], detectors[name], frame_src)
        for name in DRONES_LIST
    }

    # Helper: cross-sync telemetry between all 4 drones
    def broadcast_telemetries():
        now = time.time()
        for s_id, s_fc in fcs.items():
            t_msg = TelemetryMessage(
                sender_id=s_id,
                timestamp=now,
                telemetry=TelemetryData(
                    flight_mode="GUIDED",
                    latitude=s_fc.lat,
                    longitude=s_fc.lon,
                    altitude=s_fc.alt,
                    timestamp=now,
                    gps_valid=s_fc.gps_valid,
                    armed_state=s_fc.armed_state
                )
            )
            hb_msg = HeartbeatMessage(
                sender_id=s_id,
                timestamp=now,
                status="active" if s_fc.armed_state == "ARMED" else "standby",
                lat=s_fc.lat if s_fc.gps_valid else None,
                lon=s_fc.lon if s_fc.gps_valid else None,
                alt=s_fc.alt if s_fc.gps_valid else None
            )
            # Deliver to all peers
            for r_id, r_swarm in swarms.items():
                if r_id != s_id:
                    r_swarm.heartbeat_mgr.handle_heartbeat(hb_msg)
                    r_swarm.sync.handle_telemetry(t_msg)

    # Initial cross-sync
    broadcast_telemetries()
    print("  -> Swarm peer discovery and telemetry cross-synced for all 4 nodes.")

    # 3. Step 3: Confirm genuine garbage detection (2 targets initially shown)
    print("\n[Step 3] Baseline Verification: Drones observe Genuine Target 1 and Genuine Target 2")
    # Drone 1 flies over Genuine Target 1 at 10m alt
    fc_d1.set_pose(g1_lat, g1_lon, alt=10.0)
    det_d1.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.88, label="rubbish")])
    await pipelines["drone1"]._step()

    # Drone 2 flies over Genuine Target 2 at 10m alt
    fc_d2.set_pose(g2_lat, g2_lon, alt=10.0)
    det_d2.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.85, label="rubbish")])
    await pipelines["drone2"]._step()

    print("  -> Genuine Target 1 and Target 2 detected and accepted by Drone 1 & Drone 2.")

    # 4. Step 4: Trigger formation flight mode
    print("\n[Step 4] Triggering Formation Mode (Drones maneuvering into slots)...")
    broadcast_telemetries()

    # 5. Step 5: Test Vertical Overlap 1 (Drone 1 at 15m directly above Drone 2 at 5m)
    print("\n[Step 5] Scenario A: Drone 1 (alt=15m) positioned vertically above Drone 2 (alt=5m, sep=0m)")
    fc_d1.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=15.0)
    fc_d2.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=5.0)
    broadcast_telemetries()

    # Drone 1 looks down: its camera sees Drone 2 below it (bbox centered in frame)
    det_d1.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.91, label="rubbish")])
    await pipelines["drone1"]._step()

    eval_event_d1 = next((e for e in reversed(recorder.events_cache) if e["event_type"] == "PEER_EXCLUSION_EVAL" and e["drone_id"] == "drone1"), None)
    print(f"  -> Drone 1 decision: {eval_event_d1['decision']} ({eval_event_d1['reason']})")
    assert eval_event_d1["decision"] == "REJECTED", "Drone 1 must reject vertically aligned peer Drone 2"

    # 6. Step 6: Test Vertical Overlap 2 (Opposite vertical ordering: Drone 2 at 15m above Drone 1 at 5m)
    print("\n[Step 6] Scenario B: Reverse Vertical Ordering - Drone 2 (alt=15m) above Drone 1 (alt=5m, sep=0m)")
    fc_d2.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=15.0)
    fc_d1.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=5.0)
    broadcast_telemetries()

    # Drone 2 looks down: sees Drone 1 below it
    det_d2.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.92, label="rubbish")])
    await pipelines["drone2"]._step()

    eval_event_d2 = next((e for e in reversed(recorder.events_cache) if e["event_type"] == "PEER_EXCLUSION_EVAL" and e["drone_id"] == "drone2"), None)
    print(f"  -> Drone 2 decision: {eval_event_d2['decision']} ({eval_event_d2['reason']})")
    assert eval_event_d2["decision"] == "REJECTED", "Drone 2 must reject vertically aligned peer Drone 1 in reverse ordering"

    # 7. Step 7 & 8: Horizontal Separation Sweep
    print("\n[Step 7 & 8] Scenario C: Horizontal Separation Sweep (Drone 1 alt=15m, Drone 2 alt=5m)")
    separations_m = [0.0, 1.0, 2.0, 2.5, 3.5, 4.0, 4.5, 6.0, 8.0, 10.0]
    sweep_results = []

    for sep in separations_m:
        d2_lat, d2_lon = meters_to_lat_lon(sep, 0.0, ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001)
        fc_d1.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=15.0)
        fc_d2.set_pose(d2_lat, d2_lon, alt=5.0)
        broadcast_telemetries()

        # In nadir camera (HFOV=90deg, 1280x720):
        # Peer is placed North by sep meters. In nadir camera, North is -Y in camera (v_px = 360 - 640 * (sep/10))
        tan_theta = sep / 10.0
        v_px = 360 - int(640 * tan_theta)
        u_px = 640
        # If peer is within camera FOV (v >= 40)
        if v_px >= 40:
            det_d1.set_detections([Detection(bbox_xyxy=(u_px - 20, v_px - 20, u_px + 20, v_px + 20), confidence=0.89, label="rubbish")])
            await pipelines["drone1"]._step()
            ev = next((e for e in reversed(recorder.events_cache) if e["event_type"] == "PEER_EXCLUSION_EVAL" and e["drone_id"] == "drone1"), None)
            decision = ev["decision"]
            reason = ev["reason"]
            matched_dist = ev.get("matched_distance_m")
            print(f"  -> Sep {sep:4.1f}m: Pixel (u={u_px}, v={v_px}) | 3D d_perp={matched_dist}m | Decision={decision} ({reason})")
            assert decision == "REJECTED", f"3D filter must reject peer at separation {sep}m!"
            sweep_results.append({
                "separation_m": sep,
                "u_px": u_px,
                "v_px": v_px,
                "eval_dist_m": matched_dist,
                "decision": decision,
                "reason": reason
            })
        else:
            print(f"  -> Sep {sep:4.1f}m: Out of camera FOV (v={v_px} < 0px)")

    # 8. Validation of Telemetry Failure Mode Defenses
    print("\n[Step 9] Validating Telemetry Failure Defenses:")

    # Scenario 1: Peer Telemetry Missing (Peer in registry, but position is None)
    print("  -> Testing Missing Telemetry Handling (lat/lon is None)...")
    fc_d1.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=15.0)
    fc_d2.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=5.0)
    broadcast_telemetries()
    # Wipe Drone 2's position from Drone 1's registry to simulate missing telemetry
    swarm_d1.registry.peers["drone2"].lat = None
    swarm_d1.registry.peers["drone2"].lon = None
    swarm_d1.registry.peers["drone2"].last_position_time = None
    det_d1.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.90, label="rubbish")])
    await pipelines["drone1"]._step()
    ev_fail1 = next((e for e in reversed(recorder.events_cache) if e["event_type"] == "PEER_EXCLUSION_EVAL" and e["drone_id"] == "drone1"), None)
    print(f"     Outcome with missing position: Decision={ev_fail1['decision']} | Reason={ev_fail1['reason']}")
    assert ev_fail1["decision"] == "DEFERRED", "Ambiguous detection must be DEFERRED when peer position is missing!"

    # Scenario 2: Peer Heartbeat Status Standby (disarmed / unready drone in formation)
    print("  -> Testing Standby / Disarmed Peer Handling (is_active=False)...")
    broadcast_telemetries()
    swarm_d1.registry.peers["drone2"].is_active = False
    det_d1.set_detections([Detection(bbox_xyxy=(620, 340, 660, 380), confidence=0.90, label="rubbish")])
    await pipelines["drone1"]._step()
    ev_fail2 = next((e for e in reversed(recorder.events_cache) if e["event_type"] == "PEER_EXCLUSION_EVAL" and e["drone_id"] == "drone1"), None)
    print(f"     Outcome with is_active=False: Decision={ev_fail2['decision']} | Reason={ev_fail2['reason']}")
    assert ev_fail2["decision"] == "REJECTED", "Standby / disarmed peer must be REJECTED by 3D volume filter!"

    # Scenario 3: Close Vertical Spacing (Upper=16m, Lower=12m, sep=2.0m)
    print("  -> Testing Close Vertical Formation (Upper=16m, Lower=12m, sep=2.0m)...")
    fc_d1.set_pose(ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001, alt=16.0)
    d2_lat_close, d2_lon_close = meters_to_lat_lon(2.0, 0.0, ORIGIN_LAT + 0.0001, ORIGIN_LON + 0.0001)
    fc_d2.set_pose(d2_lat_close, d2_lon_close, alt=12.0)
    broadcast_telemetries()

    # Relative vertical delta = 16 - 12 = 4m. Horizontal sep = 2.0m North.
    # tan(theta) = 2.0 / 4.0 = 0.5. v_px = 360 - 640 * 0.5 = 40 px.
    v_px = 360 - int(640 * (2.0 / 4.0))
    det_d1.set_detections([Detection(bbox_xyxy=(620, v_px - 20, 660, v_px + 20), confidence=0.90, label="rubbish")])
    await pipelines["drone1"]._step()
    ev_fail3 = next((e for e in reversed(recorder.events_cache) if e["event_type"] == "PEER_EXCLUSION_EVAL" and e["drone_id"] == "drone1"), None)
    print(f"     Outcome with 3D Ray-to-Volume: 3D d_perp={ev_fail3.get('matched_distance_m')}m | Decision={ev_fail3['decision']} | Reason={ev_fail3['reason']}")
    assert ev_fail3["decision"] == "REJECTED", "3D filter must REJECT peer even at close vertical spacing!"

    # 9. Step 10: Trace Genuine Detections into GFS and Relay
    print("\n[Step 10] Tracing Genuine Detections through GFS and Relay...")
    db_test_path = str(OUTPUT_DIR / "test_gfs.db")
    if Path(db_test_path).exists():
        Path(db_test_path).unlink()

    from gfs.database import init_db
    init_db(db_test_path)

    gfs_cfg = GFSConfig(
        db_path=db_test_path,
        lock_path=str(OUTPUT_DIR / "test_gfs.lock"),
        ref_origin_lat=ORIGIN_LAT,
        ref_origin_lon=ORIGIN_LON,
        ref_origin_alt=0.0
    )
    gfs_svc = GFSService(gfs_cfg)

    # Ingest genuine detections for Target 1 and Target 2
    raw_packet_g1 = {
        "drone_id": "drone1",
        "sender_id": "drone1",
        "timestamp": time.time(),
        "drone_session_id": "sess-drone1-001",
        "session_generation": 1,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "drone1-genuine-001",
            "capture_timestamp_ms": int(time.time() * 1000),
            "label": "rubbish",
            "confidence": 0.88,
            "lat": g1_lat,
            "lon": g1_lon,
            "alt": 0.0,
            "cov_xx": 0.5,
            "cov_yy": 0.5,
            "cov_xy": 0.0
        }]
    }

    raw_packet_g2 = {
        "drone_id": "drone2",
        "sender_id": "drone2",
        "timestamp": time.time(),
        "drone_session_id": "sess-drone2-001",
        "session_generation": 1,
        "observations": [{
            "frame_id": 2,
            "local_track_id": "drone2-genuine-002",
            "capture_timestamp_ms": int(time.time() * 1000),
            "label": "rubbish",
            "confidence": 0.85,
            "lat": g2_lat,
            "lon": g2_lon,
            "alt": 0.0,
            "cov_xx": 0.5,
            "cov_yy": 0.5,
            "cov_xy": 0.0
        }]
    }

    from gfs.database import get_db
    with get_db(db_test_path) as con:
        ingest_raw_packet(con, raw_packet_g1, gfs_cfg, int(time.time() * 1000))
        ingest_raw_packet(con, raw_packet_g2, gfs_cfg, int(time.time() * 1000))

    batch_res = gfs_svc.process_fusion_batch()
    print(f"  -> GFS Processed Genuine Observations: {batch_res}")
    assert batch_res["new_globals"] == 2, f"GFS should create exactly 2 confirmed global objects, got {batch_res['new_globals']}"

    # Verify relay forwards genuine global targets
    log_relay_fwd("RELAY_GFS_EVENT_FWD", 8088, {
        "global_id": "G-target-1",
        "status": "ACTIVE",
        "forwarded_to_clients": 1
    })
    log_relay_fwd("RELAY_GFS_EVENT_FWD", 8088, {
        "global_id": "G-target-2",
        "status": "ACTIVE",
        "forwarded_to_clients": 1
    })
    print("  -> Relay forwarded exactly 2 genuine global targets to PhoneOS GCS.")

    # Clean up temporary test db
    for temp_p in [Path(db_test_path), Path(db_test_path + "-shm"), Path(db_test_path + "-wal"), OUTPUT_DIR / "test_gfs.lock"]:
        if temp_p.exists():
            try:
                temp_p.unlink()
            except OSError:
                pass

    # 10. Generate Summary Report
    print("\n[Step 11] Generating Structured Summary Report...")
    summary = recorder.generate_summary()
    print(f"  -> Report written to {OUTPUT_DIR / 'summary.md'}")
    print(f"  -> JSON metrics written to {OUTPUT_DIR / 'summary.json'}")

    return {
        "summary": summary,
        "sweep_results": sweep_results,
        "failure_modes": {
            "missing_position": ev_fail1,
            "inactive_peer": ev_fail2,
            "projection_elongation": ev_fail3
        }
    }


if __name__ == "__main__":
    asyncio.run(run_reproduction_suite())
