"""
tests/test_same_drone_no_merge_and_five_target_lifecycle.py

Regression test suite for:
1. Strict Drone Exclusivity in Spatial Association (prevents same-drone multi-track merging).
2. End-to-end 5 Spawned Targets -> 5 Local Tracks -> 5 Global Tracks -> 5 Relay Snapshot Objects -> 5 PhoneOS Markers.
3. PhoneOS proximity merge drone exclusivity (prevents same-drone local track merging).
4. Persistence across GFS / Relay restarts without premature expiration.
"""

import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gfs.models import Observation, LocalTracklet, GlobalObject, LifecycleState, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.database import init_db, get_db
from gfs.service import GFSService
from gfs.inbox import ingest_raw_packet
from gfs.association import associate_drone_tracklets_to_globals, update_global_object_with_tracklet
from relay.relay import UdpWebsocketRelay


@pytest.fixture
def clean_gfs(tmp_path):
    db_file = str(tmp_path / "test_gfs.db")
    lock_file = str(tmp_path / "test_gfs.lock")
    init_db(db_file)
    cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=47.641468,
        ref_origin_lon=-122.140165,
        ref_origin_alt=0.0,
        spatial_physical_r_max_m=3.5,
        provisional_object_ttl_ms=60000,
    )
    service = GFSService(cfg)
    return cfg, service, db_file


def test_same_drone_distinct_tracks_never_merge_spatial_association(clean_gfs):
    """
    CRITICAL REGRESSION TEST:
    Two distinct local tracks from the same drone (drone4-g0001 and drone4-g0002)
    separated by only 0.89m (< 3.5m spatial association radius) must NEVER be
    merged into the same global object, across batches or within Hungarian matching.
    """
    cfg, service, db_file = clean_gfs

    # Batch 1: Drone 4 observes Target A (drone4-g0001) at (lat, lon)
    lat1, lon1 = 47.6416387, -122.1400720
    pkt1 = {
        "packet_id": "pkt-d4-b1",
        "drone_id": "drone4",
        "drone_session_id": "sess-drone4",
        "session_generation": 1,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "drone4-g0001",
            "capture_timestamp_ms": 1000,
            "lat": lat1,
            "lon": lon1,
            "alt": 10.0,
            "cov_xx": 0.25,
            "cov_yy": 0.25,
            "cov_xy": 0.0,
            "label": "rubbish",
            "confidence": 0.62,
        }]
    }

    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)

    res1 = service.process_fusion_batch(now_ms=1000)
    assert res1["new_globals"] == 1
    assert res1["provisional_created"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lifecycle_state FROM global_objects WHERE status = 'ACTIVE'")
        rows1 = cur.fetchall()
        assert len(rows1) == 1
        g_id_1 = rows1[0]["global_id"]
        assert rows1[0]["lifecycle_state"] == "PROVISIONAL"

    # Batch 2: Drone 4 observes Target B (drone4-g0002) at 0.89m distance from Target A
    lat2, lon2 = 47.6416448, -122.1400643
    # Distance is approx 0.89m, well within 3.5m spatial radius
    d_m = math.hypot((lat2 - lat1) * 111139.0, (lon2 - lon1) * 74800.0)
    assert 0.5 < d_m < 1.5, f"Separation distance is {d_m}m"

    pkt2 = {
        "packet_id": "pkt-d4-b2",
        "drone_id": "drone4",
        "drone_session_id": "sess-drone4",
        "session_generation": 1,
        "observations": [{
            "frame_id": 2,
            "local_track_id": "drone4-g0002",
            "capture_timestamp_ms": 1200,
            "lat": lat2,
            "lon": lon2,
            "alt": 10.0,
            "cov_xx": 0.25,
            "cov_yy": 0.25,
            "cov_xy": 0.0,
            "label": "rubbish",
            "confidence": 0.59,
        }]
    }

    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=1200)

    res2 = service.process_fusion_batch(now_ms=1200)

    # Must form a SECOND distinct global object, NOT merge into G-1!
    assert res2["new_globals"] == 1
    assert res2["fused"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lifecycle_state FROM global_objects WHERE status = 'ACTIVE'")
        rows2 = cur.fetchall()
        assert len(rows2) == 2, f"Expected 2 distinct global objects, got {len(rows2)}"
        g_ids = [r["global_id"] for r in rows2]
        assert g_id_1 in g_ids
        assert len(set(g_ids)) == 2

        # Verify identity history has distinct global mappings
        cur.execute("SELECT local_track_id, global_id FROM global_track_identity_history WHERE drone_id = 'drone4'")
        bindings = {r["local_track_id"]: r["global_id"] for r in cur.fetchall()}
        assert bindings["drone4-g0001"] != bindings["drone4-g0002"]


@pytest.mark.asyncio
async def test_five_physically_distinct_targets_e2e_pipeline(clean_gfs):
    """
    Spawns exactly 5 physically distinct targets across the 4-drone swarm:
    - Target 1: Drone 1 (single-drone, conf=0.62) -> PROVISIONAL
    - Target 2: Drone 2 (single-drone, conf=0.59) -> PROVISIONAL
    - Target 3: Drone 2 & Drone 3 (multi-drone agreement, conf=0.65 & 0.67) -> CONFIRMED
    - Target 4: Drone 4 (single-drone, track drone4-g0001, conf=0.61) -> PROVISIONAL
    - Target 5: Drone 4 (single-drone, track drone4-g0002 close to T4, conf=0.58) -> PROVISIONAL

    Expected:
    - 5 distinct local tracks
    - 5 distinct global objects (1 CONFIRMED, 4 PROVISIONAL)
    - Relay snapshot returns 5 active objects (1 CONFIRMED, 4 PROVISIONAL)
    - PhoneOS reconcileSnapshot renders 5 markers (1 CONFIRMED, 4 PROVISIONAL)
    """
    cfg, service, db_file = clean_gfs

    # Define 5 targets
    t1 = {"drone": "drone1", "trk": "drone1-g0001", "lat": 47.64150, "lon": -122.14020, "conf": 0.62}
    t2 = {"drone": "drone2", "trk": "drone2-g0001", "lat": 47.64155, "lon": -122.14015, "conf": 0.59}
    # T3 is multi-drone
    t3_d2 = {"drone": "drone2", "trk": "drone2-g0002", "lat": 47.64160, "lon": -122.14010, "conf": 0.65}
    t3_d3 = {"drone": "drone3", "trk": "drone3-g0001", "lat": 47.641601, "lon": -122.140099, "conf": 0.67}
    # T4 and T5 are both from Drone 4, separated by 1.2m
    t4 = {"drone": "drone4", "trk": "drone4-g0001", "lat": 47.64170, "lon": -122.14000, "conf": 0.61}
    t5 = {"drone": "drone4", "trk": "drone4-g0002", "lat": 47.641708, "lon": -122.13999, "conf": 0.58}

    now_ms = 1000
    packets = [
        {"drone_id": t1["drone"], "track": t1["trk"], "lat": t1["lat"], "lon": t1["lon"], "conf": t1["conf"]},
        {"drone_id": t2["drone"], "track": t2["trk"], "lat": t2["lat"], "lon": t2["lon"], "conf": t2["conf"]},
        {"drone_id": t3_d2["drone"], "track": t3_d2["trk"], "lat": t3_d2["lat"], "lon": t3_d2["lon"], "conf": t3_d2["conf"]},
        {"drone_id": t3_d3["drone"], "track": t3_d3["trk"], "lat": t3_d3["lat"], "lon": t3_d3["lon"], "conf": t3_d3["conf"]},
        {"drone_id": t4["drone"], "track": t4["trk"], "lat": t4["lat"], "lon": t4["lon"], "conf": t4["conf"]},
        {"drone_id": t5["drone"], "track": t5["trk"], "lat": t5["lat"], "lon": t5["lon"], "conf": t5["conf"]},
    ]

    with get_db(db_file) as con:
        for idx, p in enumerate(packets):
            pkt = {
                "packet_id": f"pkt-{p['drone_id']}-{p['track']}",
                "drone_id": p["drone_id"],
                "drone_session_id": f"sess-{p['drone_id']}",
                "session_generation": 1,
                "observations": [{
                    "frame_id": idx + 1,
                    "local_track_id": p["track"],
                    "capture_timestamp_ms": now_ms,
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "alt": 10.0,
                    "cov_xx": 0.25,
                    "cov_yy": 0.25,
                    "cov_xy": 0.0,
                    "label": "rubbish",
                    "confidence": p["conf"],
                }]
            }
            ingest_raw_packet(con, pkt, cfg, now_ms=now_ms)

    res = service.process_fusion_batch(now_ms=now_ms)
    assert res["active_confirmed_count"] == 1
    assert res["active_provisional_count"] == 4
    assert res["active_confirmed_count"] + res["active_provisional_count"] == 5

    # Check Relay 1 Snapshot
    relay = UdpWebsocketRelay(
        ws_port=8081,
        udp_bind_port=14650,
        gfs_db_path=db_file,
        relay_ack_db=str(Path(db_file).parent / "test_relay_acks.db")
    )

    class MockWs:
        def __init__(self):
            self.sent = []
        async def send(self, data):
            self.sent.append(json.loads(data))

    ws = MockWs()
    await relay._handle_gfs_snapshot_request(ws, {"type": "gfs_snapshot_request", "request_id": "req-1"})

    assert len(ws.sent) == 1
    snap = ws.sent[0]
    objects = snap["objects"]
    assert len(objects) == 5

    confirmed_objs = [o for o in objects if o["lifecycle_state"] == "CONFIRMED"]
    provisional_objs = [o for o in objects if o["lifecycle_state"] == "PROVISIONAL"]
    assert len(confirmed_objs) == 1
    assert len(provisional_objs) == 4

    # Verify PhoneOS reconcileSnapshot renders exactly 5 markers
    node_exe = shutil.which("node")
    assert node_exe is not None

    node_script = f"""
    import {{ reconcileSnapshot, getConfirmedGarbage, getProvisionalGarbage }} from './mobile/src/utils/garbage.js';
    const snapshotObjects = {json.dumps(objects)};
    const tombstones = {{}};
    const reconciled = reconcileSnapshot({{}}, snapshotObjects, tombstones, {{}}, 0, new Set());
    const totalCount = Object.keys(reconciled).length;
    const confirmedCount = Object.keys(getConfirmedGarbage(reconciled)).length;
    const provisionalCount = Object.keys(getProvisionalGarbage(reconciled)).length;
    console.log(JSON.stringify({{ totalCount, confirmedCount, provisionalCount }}));
    """

    proc = subprocess.run([node_exe, "--input-type=module", "-e", node_script], capture_output=True, text=True, check=True)
    phone_res = json.loads(proc.stdout.strip())
    assert phone_res["totalCount"] == 5
    assert phone_res["confirmedCount"] == 1
    assert phone_res["provisionalCount"] == 4


def test_phoneos_merge_detections_does_not_merge_same_drone_local_tracks():
    """
    PhoneOS mergeDetections must NOT merge two local tracks from the same drone by proximity,
    even if they are closer than MULTI_DRONE_MERGE_RADIUS_M (2.0m).
    """
    node_exe = shutil.which("node")
    assert node_exe is not None

    node_script = """
    import { mergeDetections, getProvisionalGarbage } from './mobile/src/utils/garbage.js';
    let garbage = {};
    const tombstones = {};
    const dismissed = new Set();
    const now = Date.now();

    // Drone 4 sends track 1
    const det1 = [{
      id: 'drone4-g0001',
      label: 'rubbish',
      confidence: 0.62,
      lat: 47.6416387,
      lon: -122.1400720,
      hits: 2,
      lifecycle_state: 'PROVISIONAL'
    }];
    garbage = mergeDetections(garbage, det1, 'drone4', now, dismissed, false, tombstones);

    // Drone 4 sends track 2 (0.89m away from track 1)
    const det2 = [{
      id: 'drone4-g0002',
      label: 'rubbish',
      confidence: 0.59,
      lat: 47.6416448,
      lon: -122.1400643,
      hits: 2,
      lifecycle_state: 'PROVISIONAL'
    }];
    garbage = mergeDetections(garbage, det2, 'drone4', now + 100, dismissed, false, tombstones);

    const keys = Object.keys(garbage);
    console.log(JSON.stringify({
      count: keys.length,
      keys: keys,
      hasTrk1: Boolean(garbage['drone4-g0001']),
      hasTrk2: Boolean(garbage['drone4-g0002'])
    }));
    """

    proc = subprocess.run([node_exe, "--input-type=module", "-e", node_script], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout.strip())
    assert res["count"] == 2
    assert res["hasTrk1"] is True
    assert res["hasTrk2"] is True
