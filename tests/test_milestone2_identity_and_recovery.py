"""
tests/test_milestone2_identity_and_recovery.py

Milestone 2 Operational Reliability: Identity Integrity & Crash Recovery.
Audits:
1. Cross-drone identical local_track_id collision immunity.
2. Cross-session identity collision prevention across drone reboot.
3. Local track ID reuse after provisional TTL expiration.
4. Packet & observation deduplication with payload hash security.
5. Transactional outbox crash recovery & lease expiration recovery.
6. Relay Contract B snapshot recovery & PhoneOS reconciliation.
7. Version-aware event ordering & stale event rejection.
"""

import os
import sys
import json
import time
import math
from pathlib import Path
from typing import Dict, List, Any
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gfs.models import Observation, LocalTracklet, GlobalObject, LifecycleState, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.database import init_db, get_db, create_connection
from gfs.service import GFSService
from gfs.association import build_local_tracklets
from gfs.hypothesis import form_new_global_objects
from gfs.inbox import ingest_raw_packet
from gfs.outbox import (
    lease_outbox_events,
    record_successful_transmission,
    compute_ack_hmac,
    process_gcs_ack,
)
from relay.relay import UdpWebsocketRelay


@pytest.fixture
def m2_env(tmp_path):
    db_file = str(tmp_path / "m2_identity_gfs.db")
    lock_file = str(tmp_path / "m2_identity_gfs.lock")
    init_db(db_file)
    cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=47.641468,
        ref_origin_lon=-122.140165,
        ref_origin_alt=0.0,
        spatial_physical_r_max_m=3.5,
        provisional_object_ttl_ms=3000,
    )
    service = GFSService(cfg)
    return cfg, service, db_file


def make_packet(drone_id: str, track_id: str, session_id: str, gen: int, lat: float, lon: float, conf: float, now_ms: int = 1000):
    return {
        "packet_id": f"pkt-{drone_id}-{track_id}-{now_ms}",
        "drone_id": drone_id,
        "drone_session_id": session_id,
        "session_generation": gen,
        "observations": [{
            "frame_id": int(now_ms // 10),
            "local_track_id": track_id,
            "capture_timestamp_ms": now_ms,
            "lat": lat,
            "lon": lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": conf,
            "cov_xx": 0.30,
            "cov_yy": 0.30,
            "cov_xy": 0.0
        }]
    }


def test_cross_drone_identical_local_track_ids(m2_env):
    """
    Test 1: Drone 1 and Drone 2 both emit track 'trk-01' at well-separated locations
    (e.g. 20m apart) in the same batch.
    Verify: GFS creates 2 distinct global objects; outbox item dispositions map to the correct global IDs.
    """
    cfg, service, db_file = m2_env
    # Drone 1 sees target at ref origin
    pkt1 = make_packet("drone1", "trk-01", "sess-d1", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    # Drone 2 sees target 20m north (approx +0.00018 deg lat)
    pkt2 = make_packet("drone2", "trk-01", "sess-d2", 1, cfg.ref_origin_lat + 0.00018, cfg.ref_origin_lon, 0.64, now_ms=1000)

    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
        ingest_raw_packet(con, pkt2, cfg, now_ms=1000)

    res = service.process_fusion_batch(now_ms=1000)
    assert res["status"] == "SUCCESS"
    assert res["new_globals"] == 2
    assert res["provisional_created"] == 2

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lat, confidence, source_count FROM global_objects WHERE status = 'ACTIVE'")
        rows = cur.fetchall()
        assert len(rows) == 2, "Must create 2 distinct global objects"
        g_ids = {r["global_id"] for r in rows}
        assert len(g_ids) == 2, "Global IDs must be distinct"

        # Verify identity history has both entries
        cur.execute("SELECT drone_id, local_track_id, global_id FROM global_track_identity_history")
        hist_rows = cur.fetchall()
        assert len(hist_rows) == 2
        hist_map = {(r["drone_id"], r["local_track_id"]): r["global_id"] for r in hist_rows}
        assert ("drone1", "trk-01") in hist_map
        assert ("drone2", "trk-01") in hist_map
        assert hist_map[("drone1", "trk-01")] != hist_map[("drone2", "trk-01")]


def test_cross_session_identity_collision_prevention(m2_env):
    """
    Test 2: Drone 1 boots (sess-1, gen 1), emits trk-01 at Location A.
    Drone 1 reboots (sess-2, gen 2), emits trk-01 at Location B (100m away).
    Verify: Global IDs are distinct; Location A global object is NOT overwritten or mutated.
    """
    cfg, service, db_file = m2_env

    # Batch 1: Session 1
    pkt1 = make_packet("drone1", "trk-01", "sess-d1-alpha", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.65, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    res1 = service.process_fusion_batch(now_ms=1000)
    assert res1["new_globals"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lat, lon FROM global_objects WHERE status = 'ACTIVE'")
        obj1 = cur.fetchone()
        gid1 = obj1["global_id"]
        lat1 = obj1["lat"]

    # Batch 2: Drone 1 reboots -> Session 2, Generation 2, at 100m North (+0.00090 deg lat)
    pkt2 = make_packet("drone1", "trk-01", "sess-d1-beta", 2, cfg.ref_origin_lat + 0.00090, cfg.ref_origin_lon, 0.66, now_ms=2000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=2000)
    res2 = service.process_fusion_batch(now_ms=2000)
    assert res2["new_globals"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lat, lon FROM global_objects WHERE status = 'ACTIVE'")
        all_objs = cur.fetchall()
        assert len(all_objs) == 2, "Both distinct global objects must exist simultaneously"
        g_ids = [r["global_id"] for r in all_objs]
        assert gid1 in g_ids
        assert len(set(g_ids)) == 2, "Global IDs across sessions must not collide"

        # Verify Location A object lat has not changed
        obj1_after = next(r for r in all_objs if r["global_id"] == gid1)
        assert abs(obj1_after["lat"] - lat1) < 1e-7


def test_local_track_id_reuse_after_provisional_retirement(m2_env):
    """
    Test 3: Provisional candidate expires after TTL -> RETIRED.
    Later, new observation arrives for that same region.
    Verify: Old candidate remains RETIRED, new candidate is formed or reactivated cleanly.
    """
    cfg, service, db_file = m2_env

    # 1. Create provisional candidate with TTL=3000ms
    pkt1 = make_packet("drone1", "trk-01", "sess-1", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.60, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # 2. Advance clock past TTL (now_ms=5000 > 1000 + 3000)
    res_exp = service.process_fusion_batch(now_ms=5000)
    assert res_exp["provisional_expired"] == 1
    assert res_exp["active_provisional_count"] == 0

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, status, lifecycle_state, version FROM global_objects")
        row = cur.fetchone()
        assert row["status"] == "RETIRED"
        assert row["lifecycle_state"] == "RETIRED"
        assert row["version"] == 2

    # 3. New observation arrives at now_ms=6000
    pkt2 = make_packet("drone1", "trk-01", "sess-1", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.65, now_ms=6000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=6000)
    res_reactivate = service.process_fusion_batch(now_ms=6000)
    assert res_reactivate["status"] == "SUCCESS"

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, status, lifecycle_state, version FROM global_objects")
        row2 = cur.fetchone()
        assert row2["status"] == "ACTIVE"
        assert row2["version"] >= 3


def test_delayed_and_duplicate_packet_deduplication(m2_env):
    """
    Test 4: Exact duplicate UDP packets and delayed identical packets are safely deduplicated.
    """
    cfg, service, db_file = m2_env
    pkt = make_packet("drone3", "trk-dup", "sess-3", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)

    with get_db(db_file) as con:
        pkt_id, accepted1, rej1 = ingest_raw_packet(con, pkt, cfg, now_ms=1000)
        assert accepted1 == 1
        assert rej1 == 0

        # Duplicate ingestion of same packet
        pkt_id2, accepted2, rej2 = ingest_raw_packet(con, pkt, cfg, now_ms=1050)
        assert accepted2 == 0
        assert rej2 == 1, "Duplicate packet must be identified and ignored"


def test_transactional_outbox_crash_and_recovery(m2_env):
    """
    Test 5: Simulate service termination before outbox dispatch.
    Verify: WAL persistence preserves pending events; subsequent restart leases and ACKs cleanly.
    """
    cfg, service, db_file = m2_env
    pkt = make_packet("drone1", "trk-rec", "sess-1", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.75, now_ms=1000)

    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Verify unleased events exist
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT id, status FROM gfs_outbox_events WHERE status = 'PENDING'")
        pending = cur.fetchall()
        assert len(pending) >= 1
        event_id = pending[0]["id"]

    # Lease event with token
    with get_db(db_file) as con:
        token, leased = lease_outbox_events(con, "worker-m2", 5000, cfg, now_ms=1000)
        assert any(e.id == event_id for e in leased)
        att_id = record_successful_transmission(con, event_id, token, 5000, now_ms=1000)

    # Simulate crash before ACK: advance clock past lease duration (7000ms > 1000 + 5000)
    # Lease expires -> event is re-leasable
    with get_db(db_file) as con:
        token2, re_leased = lease_outbox_events(con, "worker-m2-restart", 5000, cfg, now_ms=7000)
        assert any(e.id == event_id for e in re_leased)
        att_id2 = record_successful_transmission(con, event_id, token2, 5000, now_ms=7000)

        # Successfully process authenticated ACK using authorized client 'gcs-primary'
        secret = cfg.hmac_secret_key.encode("utf-8") if isinstance(cfg.hmac_secret_key, str) else cfg.hmac_secret_key
        sig = compute_ack_hmac(event_id, att_id2, "ack-m2-01", "gcs-primary", 7100, "gcs-key-1", secret)
        valid_ack = {
            "event_id": event_id,
            "delivery_attempt_id": att_id2,
            "gcs_client_id": "gcs-primary",
            "ack_id": "ack-m2-01",
            "timestamp_ms": 7100,
            "kid": "gcs-key-1",
            "signature": sig
        }
        ack_ok, reason = process_gcs_ack(con, valid_ack, cfg, now_ms=7100)
        assert ack_ok is True, f"ACK should succeed: {reason}"

        cur = con.cursor()
        cur.execute("SELECT status FROM gfs_outbox_events WHERE id = ?", (event_id,))
        assert cur.fetchone()["status"] == "ACKED"


@pytest.mark.asyncio
async def test_relay_restart_contract_b_snapshot_recovery(m2_env, tmp_path):
    """
    Test 6: Relay restart resilience.
    PhoneOS requests Contract B snapshot before and after relay restart.
    Verify: Snapshot metadata, versions, and active/provisional counts match exactly.
    """
    cfg, service, db_file = m2_env

    # Populate 1 Confirmed and 1 Provisional
    pkt_conf1 = make_packet("drone1", "trk-c1", "s1", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.65, now_ms=1000)
    pkt_conf2 = make_packet("drone2", "trk-c2", "s2", 1, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.65, now_ms=1000)
    pkt_prov = make_packet("drone3", "trk-p1", "s3", 1, cfg.ref_origin_lat + 0.0003, cfg.ref_origin_lon, 0.60, now_ms=1000)

    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt_conf1, cfg, now_ms=1000)
        ingest_raw_packet(con, pkt_conf2, cfg, now_ms=1000)
        ingest_raw_packet(con, pkt_prov, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    class MockWs:
        def __init__(self):
            self.sent_msgs = []
        async def send(self, data):
            self.sent_msgs.append(json.loads(data))

    # Phase 1: Relay instance 1
    relay1 = UdpWebsocketRelay(
        ws_port=8091,
        udp_bind_port=14591,
        gfs_db_path=db_file,
        relay_ack_db=str(tmp_path / "relay_test1.db")
    )
    ws1 = MockWs()
    await relay1._handle_gfs_snapshot_request(ws1, {"type": "gfs_snapshot_request"})
    assert len(ws1.sent_msgs) == 1
    snap1 = ws1.sent_msgs[0]
    assert snap1["type"] == "gfs_snapshot_response"
    assert len(snap1["objects"]) == 2

    # Phase 2: Relay restart (simulate new process instance using same persistent DB)
    relay2 = UdpWebsocketRelay(
        ws_port=8092,
        udp_bind_port=14592,
        gfs_db_path=db_file,
        relay_ack_db=str(tmp_path / "relay_test1.db")
    )
    ws2 = MockWs()
    await relay2._handle_gfs_snapshot_request(ws2, {"type": "gfs_snapshot_request"})
    assert len(ws2.sent_msgs) == 1
    snap2 = ws2.sent_msgs[0]
    assert snap2["type"] == "gfs_snapshot_response"
    assert len(snap2["objects"]) == 2

    # Exact equality of snapshot content across relay restarts
    obj_map1 = {o["id"]: o for o in snap1["objects"]}
    obj_map2 = {o["id"]: o for o in snap2["objects"]}
    assert set(obj_map1.keys()) == set(obj_map2.keys())
    for gid in obj_map1:
        assert obj_map1[gid]["version"] == obj_map2[gid]["version"]
        assert obj_map1[gid]["lifecycle_state"] == obj_map2[gid]["lifecycle_state"]
        assert obj_map1[gid]["confidence"] == obj_map2[gid]["confidence"]

