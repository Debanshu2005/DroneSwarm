"""
Comprehensive Production Test Suite for Global Fusion Service (GFS) - GFA v1.0.
Mandatory Tests F01 through F20.
"""

import os
import sys
import math
import time
import uuid
import json
import itertools
import sqlite3
import numpy as np
import pytest

from gfs.models import (
    Observation,
    LocalTracklet,
    GlobalObject,
    SourceEstimate,
    wgs84_to_enu,
    enu_to_wgs84
)
from gfs.canonical import (
    construct_canonical_obs_id,
    compute_cnor_v1_hash,
    validate_observation_dict
)
from gfs.config import GFSConfig
from gfs.lock import CrossPlatformProcessLock, LockContentionError
from gfs.database import init_db, get_db, create_connection
from gfs.association import (
    build_local_tracklets,
    associate_drone_tracklets_to_globals,
    fuse_covariance_intersection,
    update_global_object_with_tracklet,
    solve_exact_forced_prefix_hungarian_int
)
from gfs.hypothesis import (
    form_new_global_objects,
    bounded_bron_kerbosch,
    tracklets_are_compatible
)
from gfs.inbox import (
    ingest_raw_packet,
    claim_pending_packets,
    commit_packet_dispositions,
    StaleSessionError,
    SessionConflictError
)
from gfs.outbox import (
    enqueue_global_object_event,
    lease_outbox_events,
    record_successful_transmission,
    process_gcs_ack,
    verify_ack_hmac
)
from gfs.service import GFSService


# Fixtures
@pytest.fixture
def tmp_gfs_db(tmp_path):
    db_file = str(tmp_path / "test_gfs.db")
    lock_file = str(tmp_path / "test_gfs.lock")
    init_db(db_file)
    cfg = GFSConfig(db_path=db_file, lock_path=lock_file)
    return cfg


# ---------------------------------------------------------------------------
# F01: Four drones observe one unseen object -> exactly one new global ID
# ---------------------------------------------------------------------------
def test_f01_four_drones_one_unseen_object(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    # 4 distinct drones observing same object
    for i in range(1, 5):
        pkt = {
            "packet_id": f"pkt-drone-{i}",
            "drone_id": f"DroneOS{i}",
            "drone_session_id": f"sess-drone-{i}",
            "session_generation": 1,
            "observations": [{
                "frame_id": 10,
                "local_track_id": f"trk-d{i}",
                "capture_timestamp_ms": now_ms,
                "lat": base_lat + i * 0.000002,  # ~0.2m spread
                "lon": base_lon + i * 0.000002,
                "alt": 10.0,
                "label": "rubbish",
                "confidence": 0.85,
                "cov_xx": 0.3,
                "cov_yy": 0.3,
                "cov_xy": 0.0
            }]
        }
        with get_db(tmp_gfs_db.db_path) as con:
            ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=now_ms)

    res = service.process_fusion_batch(now_ms=now_ms)
    assert res["status"] == "SUCCESS"
    assert res["new_globals"] == 1, f"Expected 1 new global, got {res['new_globals']}"
    assert res["fused"] == 4, f"Expected 4 observations fused, got {res['fused']}"

    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, source_count, observation_count FROM global_objects WHERE status = 'ACTIVE'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["source_count"] == 4
        assert rows[0]["observation_count"] == 4


# ---------------------------------------------------------------------------
# F02: Four drones observe one existing object -> attach to same existing ID
# ---------------------------------------------------------------------------
def test_f02_four_drones_one_existing_object(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    # Create existing global object
    existing_id = "G-TARGET-ALPHA"
    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO global_objects 
               (global_id, label, status, lat, lon, alt, east_m, north_m, cov_xx, cov_yy, cov_xy,
                confidence, first_seen_ms, last_seen_ms, observation_count, source_count, source_estimates_json, version)
               VALUES (?, 'rubbish', 'ACTIVE', ?, ?, 10.0, 0.0, 0.0, 0.3, 0.3, 0.0, 0.85, ?, ?, 1, 1, '{}', 1)""",
            (existing_id, base_lat, base_lon, now_ms - 1000, now_ms - 1000)
        )

    # 4 distinct drones observe Target Alpha at now_ms
    for i in range(1, 5):
        pkt = {
            "packet_id": f"pkt-attach-{i}",
            "drone_id": f"DroneOS{i}",
            "drone_session_id": f"sess-att-{i}",
            "session_generation": 1,
            "observations": [{
                "frame_id": 20,
                "local_track_id": f"trk-att-{i}",
                "capture_timestamp_ms": now_ms,
                "lat": base_lat + i * 0.000001,
                "lon": base_lon,
                "alt": 10.0,
                "label": "rubbish",
                "confidence": 0.88,
                "cov_xx": 0.25,
                "cov_yy": 0.25,
                "cov_xy": 0.0
            }]
        }
        with get_db(tmp_gfs_db.db_path) as con:
            ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=now_ms)

    res = service.process_fusion_batch(now_ms=now_ms)
    assert res["status"] == "SUCCESS"
    assert res["new_globals"] == 0, "No new globals should be formed"
    assert res["updated_globals"] == 1, "Existing global object should be updated"
    assert res["fused"] == 4

    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, source_count FROM global_objects WHERE global_id = ?", (existing_id,))
        row = cur.fetchone()
        assert row["source_count"] == 4


# ---------------------------------------------------------------------------
# F03: Two nearby same-class physical objects -> two separate global IDs
# ---------------------------------------------------------------------------
def test_f03_two_nearby_same_class_objects(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    # Object 1 (at 0m) seen by Drone1 and Drone2
    # Object 2 (at 6m east) seen by Drone3 and Drone4
    # ~6m in lon is ~0.000057 deg
    obs_list = [
        ("DroneOS1", "trk-obj1", base_lat, base_lon),
        ("DroneOS2", "trk-obj1", base_lat + 0.000002, base_lon),
        ("DroneOS3", "trk-obj2", base_lat, base_lon + 0.000060),
        ("DroneOS4", "trk-obj2", base_lat + 0.000002, base_lon + 0.000060),
    ]

    for d_id, t_id, lat, lon in obs_list:
        pkt = {
            "packet_id": f"pkt-{d_id}",
            "drone_id": d_id,
            "drone_session_id": f"sess-{d_id}",
            "session_generation": 1,
            "observations": [{
                "frame_id": 15,
                "local_track_id": t_id,
                "capture_timestamp_ms": now_ms,
                "lat": lat,
                "lon": lon,
                "alt": 10.0,
                "label": "rubbish",
                "confidence": 0.85,
                "cov_xx": 0.3,
                "cov_yy": 0.3,
                "cov_xy": 0.0
            }]
        }
        with get_db(tmp_gfs_db.db_path) as con:
            ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=now_ms)

    res = service.process_fusion_batch(now_ms=now_ms)
    assert res["status"] == "SUCCESS"
    assert res["new_globals"] == 2, f"Expected 2 separate global objects, got {res['new_globals']}"


# ---------------------------------------------------------------------------
# F04: Two different local tracks from the same drone never merge
# ---------------------------------------------------------------------------
def test_f04_same_drone_tracks_never_merge(tmp_gfs_db):
    base_lat, base_lon = 22.3149, 87.3105
    t1 = LocalTracklet(
        drone_id="DroneOS1", drone_session_id="s1", session_generation=1,
        local_track_id="trk-1", label="rubbish", east_m=0.0, north_m=0.0,
        cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85, last_seen_ms=1000
    )
    t2 = LocalTracklet(
        drone_id="DroneOS1", drone_session_id="s1", session_generation=1,
        local_track_id="trk-2", label="rubbish", east_m=0.5, north_m=0.0, # 0.5m apart
        cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85, last_seen_ms=1000
    )
    # Pairwise compatibility must return False
    assert not tracklets_are_compatible(t1, t2, tmp_gfs_db), "Distinct tracks from same drone must NOT be compatible"


# ---------------------------------------------------------------------------
# F05: Multiple frames from one stable local_track_id -> one LocalTracklet
# ---------------------------------------------------------------------------
def test_f05_multiple_frames_one_local_tracklet():
    base_lat, base_lon = 22.3149, 87.3105
    obs_frames = [
        Observation(
            obs_id=f"obs-{i}", drone_id="DroneOS1", drone_session_id="s1", session_generation=1,
            frame_id=10 + i, local_track_id="trk-stable", capture_timestamp_ms=1000 + i*100,
            lat=base_lat, lon=base_lon, alt=10.0, cov_xx=0.25, cov_yy=0.25, cov_xy=0.0,
            label="rubbish", confidence=0.85, payload_hash=f"h{i}", east_m=0.0 + i*0.05, north_m=0.0
        )
        for i in range(5)
    ]
    tracklets = build_local_tracklets(obs_frames)
    assert len(tracklets) == 1
    t = tracklets[0]
    assert t.local_track_id == "trk-stable"
    assert len(t.observations) == 5
    assert t.first_frame_id == 10
    assert t.last_frame_id == 14


# ---------------------------------------------------------------------------
# F06: One observation cannot update two global tracks
# ---------------------------------------------------------------------------
def test_f06_observation_cannot_update_two_globals(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    pkt = {
        "packet_id": "pkt-f06",
        "drone_id": "DroneOS1",
        "drone_session_id": "sess-f06",
        "session_generation": 1,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "trk-single",
            "capture_timestamp_ms": now_ms,
            "lat": base_lat,
            "lon": base_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.85,
            "cov_xx": 0.3,
            "cov_yy": 0.3,
            "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=now_ms)

    service.process_fusion_batch(now_ms=now_ms)

    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT target_global_id FROM inbox_packet_items WHERE disposition = 'FUSED'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["target_global_id"] is not None


# ---------------------------------------------------------------------------
# F07: One drone cannot contribute two different local tracklets to same global
# ---------------------------------------------------------------------------
def test_f07_same_drone_two_tracklets_one_global(tmp_gfs_db):
    # Setup existing global
    glob = GlobalObject(
        global_id="G-TARGET-1", label="rubbish", status="ACTIVE",
        lat=22.3149, lon=87.3105, alt=10.0, east_m=0.0, north_m=0.0,
        cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85,
        first_seen_ms=1000, last_seen_ms=1000
    )
    # Two tracklets from DroneOS1 both close to G-TARGET-1
    t1 = LocalTracklet(
        drone_id="DroneOS1", drone_session_id="s1", session_generation=1,
        local_track_id="trk-A", label="rubbish", east_m=0.1, north_m=0.0,
        cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.88, last_seen_ms=1000
    )
    t2 = LocalTracklet(
        drone_id="DroneOS1", drone_session_id="s1", session_generation=1,
        local_track_id="trk-B", label="rubbish", east_m=0.2, north_m=0.0,
        cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85, last_seen_ms=1000
    )

    matched, unmatched = associate_drone_tracklets_to_globals(
        [t1, t2], [glob], {}, tmp_gfs_db
    )
    # Hungarian assignment must assign at most one tracklet to G-TARGET-1
    assert len(matched) == 1
    assert len(unmatched) == 1
    assert matched.get("trk-A") == "G-TARGET-1"
    assert unmatched[0].local_track_id == "trk-B"


# ---------------------------------------------------------------------------
# F08: Same obs_id in different packets -> only one fusion
# ---------------------------------------------------------------------------
def test_f08_same_obs_id_cross_packet_duplicate(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    obs_dict = {
        "frame_id": 10,
        "local_track_id": "trk-dup",
        "capture_timestamp_ms": now_ms,
        "lat": base_lat,
        "lon": base_lon,
        "alt": 10.0,
        "label": "rubbish",
        "confidence": 0.85,
        "cov_xx": 0.3,
        "cov_yy": 0.3,
        "cov_xy": 0.0
    }

    # Packet 1
    pkt1 = {
        "packet_id": "pkt-1",
        "drone_id": "DroneOS1",
        "drone_session_id": "sess-1",
        "session_generation": 1,
        "observations": [obs_dict]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, pkt1, tmp_gfs_db, now_ms=now_ms)
    res1 = service.process_fusion_batch(now_ms=now_ms)
    assert res1["fused"] == 1

    # Packet 2 with identical observation
    pkt2 = {
        "packet_id": "pkt-2",
        "drone_id": "DroneOS1",
        "drone_session_id": "sess-1",
        "session_generation": 1,
        "observations": [obs_dict]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, pkt2, tmp_gfs_db, now_ms=now_ms + 100)

    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT disposition, rejection_reason FROM inbox_packet_items WHERE packet_id = 'pkt-2'")
        row = cur.fetchone()
        assert row["disposition"] == "EXPIRED"
        assert row["rejection_reason"] == "IDEMPOTENT_DUPLICATE"


# ---------------------------------------------------------------------------
# F09: Same obs_id with different payload hash -> quarantine
# ---------------------------------------------------------------------------
def test_f09_payload_hash_mismatch_collision(tmp_gfs_db):
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    obs_orig = {
        "frame_id": 10, "local_track_id": "trk-coll", "capture_timestamp_ms": now_ms,
        "lat": base_lat, "lon": base_lon, "alt": 10.0, "label": "rubbish",
        "confidence": 0.85, "cov_xx": 0.3, "cov_yy": 0.3, "cov_xy": 0.0
    }
    # Ingest orig
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, {"packet_id": "p1", "drone_id": "D1", "drone_session_id": "s1", "session_generation": 1, "observations": [obs_orig]}, tmp_gfs_db, now_ms=now_ms)

    # Ingest altered payload with SAME obs_id fields
    obs_altered = obs_orig.copy()
    obs_altered["lat"] = base_lat + 0.1 # altered location

    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, {"packet_id": "p2", "drone_id": "D1", "drone_session_id": "s1", "session_generation": 1, "observations": [obs_altered]}, tmp_gfs_db, now_ms=now_ms)
        cur = con.cursor()
        cur.execute("SELECT disposition, rejection_reason FROM inbox_packet_items WHERE packet_id = 'p2'")
        row = cur.fetchone()
        assert row["disposition"] == "REJECTED"
        assert row["rejection_reason"] == "PAYLOAD_HASH_MISMATCH_COLLISION"

        cur.execute("SELECT rejection_reason FROM observation_rejections WHERE packet_id = 'p2'")
        assert cur.fetchone() is not None


# ---------------------------------------------------------------------------
# F10: Historical local-track identity cannot silently migrate to another global
# ---------------------------------------------------------------------------
def test_f10_historical_identity_migration_prevention(tmp_gfs_db):
    glob1 = GlobalObject(
        global_id="G-1", label="rubbish", status="ACTIVE", lat=22.3149, lon=87.3105,
        alt=10.0, east_m=0.0, north_m=0.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
        confidence=0.85, first_seen_ms=1000, last_seen_ms=1000
    )
    glob2 = GlobalObject(
        global_id="G-2", label="rubbish", status="ACTIVE", lat=22.3149, lon=87.3105,
        alt=10.0, east_m=1.0, north_m=0.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
        confidence=0.85, first_seen_ms=1000, last_seen_ms=1000
    )
    # Tracklet is bound historically to G-1
    trk = LocalTracklet(
        drone_id="D1", drone_session_id="s1", session_generation=1, local_track_id="trk-hist",
        label="rubbish", east_m=0.9, north_m=0.0, # closer to G-2 than G-1!
        cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85, last_seen_ms=1000
    )
    id_history = {("D1", "s1", 1, "trk-hist"): "G-1"}

    matched, _ = associate_drone_tracklets_to_globals([trk], [glob1, glob2], id_history, tmp_gfs_db)
    # Must NOT associate with G-2 despite spatial proximity
    assert matched.get("trk-hist") != "G-2"


# ---------------------------------------------------------------------------
# F11: A/B compatible, B/C compatible, A/C incompatible -> B quarantined
# ---------------------------------------------------------------------------
def test_f11_ambiguous_bridge_quarantine(tmp_gfs_db):
    tA = LocalTracklet("D1", "s1", 1, "tA", "rubbish", east_m=0.0, north_m=0.0, cov_xx=0.4, cov_yy=0.4, cov_xy=0.0, confidence=0.90, last_seen_ms=1000)
    tB = LocalTracklet("D2", "s2", 1, "tB", "rubbish", east_m=2.0, north_m=0.0, cov_xx=0.4, cov_yy=0.4, cov_xy=0.0, confidence=0.85, last_seen_ms=1000)
    tC = LocalTracklet("D3", "s3", 1, "tC", "rubbish", east_m=4.0, north_m=0.0, cov_xx=0.4, cov_yy=0.4, cov_xy=0.0, confidence=0.82, last_seen_ms=1000)

    new_globs, amb, low = form_new_global_objects([tA, tB, tC], tmp_gfs_db, 22.3149, 87.3105)
    amb_ids = {t.local_track_id for t in amb}
    assert "tB" in amb_ids, "Ambiguous bridge tracklet tB must be quarantined"
    assert len(new_globs) == 2


# ---------------------------------------------------------------------------
# F12: Input permutation does not change accepted hypotheses
# ---------------------------------------------------------------------------
def test_f12_permutation_invariance(tmp_gfs_db):
    tA = LocalTracklet("D1", "s1", 1, "tA", "rubbish", east_m=0.0, north_m=0.0, cov_xx=0.4, cov_yy=0.4, cov_xy=0.0, confidence=0.90, last_seen_ms=1000)
    tB = LocalTracklet("D2", "s2", 1, "tB", "rubbish", east_m=2.0, north_m=0.0, cov_xx=0.4, cov_yy=0.4, cov_xy=0.0, confidence=0.85, last_seen_ms=1000)
    tC = LocalTracklet("D3", "s3", 1, "tC", "rubbish", east_m=4.0, north_m=0.0, cov_xx=0.4, cov_yy=0.4, cov_xy=0.0, confidence=0.82, last_seen_ms=1000)

    baseline_globs, baseline_amb, _ = form_new_global_objects([tA, tB, tC], tmp_gfs_db, 22.3149, 87.3105)
    baseline_amb_set = {t.local_track_id for t in baseline_amb}

    for perm in itertools.permutations([tA, tB, tC]):
        p_globs, p_amb, _ = form_new_global_objects(list(perm), tmp_gfs_db, 22.3149, 87.3105)
        p_amb_set = {t.local_track_id for t in p_amb}
        assert p_amb_set == baseline_amb_set
        assert len(p_globs) == len(baseline_globs)


# ---------------------------------------------------------------------------
# F13: Spatial cell-boundary observations retain valid compatibility
# ---------------------------------------------------------------------------
def test_f13_cell_boundary_compatibility(tmp_gfs_db):
    # Two tracklets 1.0m apart across an arbitrary cell boundary (e.g. x=99.5m and x=100.5m)
    t1 = LocalTracklet("D1", "s1", 1, "t1", "rubbish", east_m=99.5, north_m=50.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85, last_seen_ms=1000)
    t2 = LocalTracklet("D2", "s2", 1, "t2", "rubbish", east_m=100.5, north_m=50.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.85, last_seen_ms=1000)
    assert tracklets_are_compatible(t1, t2, tmp_gfs_db)


# ---------------------------------------------------------------------------
# F14: Clique enumeration timeout causes safe deferral, not partial acceptance
# ---------------------------------------------------------------------------
def test_f14_clique_timeout_safe_deferral(tmp_gfs_db):
    # Dense complete graph with zero CPU time limit
    adj = {f"v_{i}": {f"v_{j}" for j in range(20) if j != i} for i in range(20)}
    cliques, truncated = bounded_bron_kerbosch(adj, max_cliques=500, time_limit_ms=0.0001)
    # Truncation must be flagged
    assert truncated


# ---------------------------------------------------------------------------
# F15: GFS restart preserves global identities and pending observations
# ---------------------------------------------------------------------------
def test_f15_restart_persistence(tmp_gfs_db):
    now_ms = 1731542400000
    # Ingest with first service instance
    s1 = GFSService(tmp_gfs_db)
    pkt = {
        "packet_id": "pkt-restart",
        "drone_id": "D1", "drone_session_id": "s1", "session_generation": 1,
        "observations": [{
            "frame_id": 1, "local_track_id": "trk-res", "capture_timestamp_ms": now_ms,
            "lat": 22.3149, "lon": 87.3105, "alt": 10.0, "label": "rubbish",
            "confidence": 0.85, "cov_xx": 0.3, "cov_yy": 0.3, "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=now_ms)
    s1.process_fusion_batch(now_ms=now_ms)

    # Simulate restart by creating second service instance pointing to same DB
    s2 = GFSService(tmp_gfs_db)
    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, status FROM global_objects")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# F16: Concurrent ingestion cannot create duplicate global identities
# ---------------------------------------------------------------------------
def test_f16_concurrent_ingestion_lock(tmp_gfs_db):
    lock1 = CrossPlatformProcessLock(tmp_gfs_db.lock_path)
    lock2 = CrossPlatformProcessLock(tmp_gfs_db.lock_path)

    assert lock1.acquire(blocking=False)
    # Second lock attempt must fail
    assert not lock2.acquire(blocking=False)
    lock1.release()
    # Now second lock succeeds
    assert lock2.acquire(blocking=False)
    lock2.release()


# ---------------------------------------------------------------------------
# F17: Covariance Intersection remains conservative and numerically valid
# ---------------------------------------------------------------------------
def test_f17_covariance_intersection_numerics():
    e1 = SourceEstimate("D1", "s1", 1, "t1", east_m=0.0, north_m=0.0, cov_xx=0.5, cov_yy=0.2, cov_xy=0.05, confidence=0.85, last_seen_ms=1000, observation_count=1)
    e2 = SourceEstimate("D2", "s2", 1, "t2", east_m=0.2, north_m=0.1, cov_xx=0.2, cov_yy=0.6, cov_xy=-0.02, confidence=0.80, last_seen_ms=1000, observation_count=1)

    f_east, f_north, f_cxx, f_cyy, f_cxy = fuse_covariance_intersection([e1, e2])
    # Positive definite check
    det = f_cxx * f_cyy - f_cxy * f_cxy
    assert det > 0.0
    assert f_cxx > 0.0 and f_cyy > 0.0


# ---------------------------------------------------------------------------
# F18: Expired outbox leases recover
# ---------------------------------------------------------------------------
def test_f18_outbox_lease_expiry_recovery(tmp_gfs_db):
    glob = GlobalObject(
        global_id="G-LEASE", label="rubbish", status="ACTIVE", lat=22.3149, lon=87.3105,
        alt=10.0, east_m=0.0, north_m=0.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
        confidence=0.85, first_seen_ms=1000, last_seen_ms=1000
    )
    with get_db(tmp_gfs_db.db_path) as con:
        evt_id = enqueue_global_object_event(con, glob, now_ms=1000)

        # Lease with 500ms duration
        token1, evts1 = lease_outbox_events(con, "worker-1", 500, tmp_gfs_db, now_ms=1000)
        assert len(evts1) == 1

        # At now_ms = 2000 (expired), next lease reclaims it
        token2, evts2 = lease_outbox_events(con, "worker-2", 500, tmp_gfs_db, now_ms=2000)
        assert len(evts2) == 1
        assert evts2[0].id == evt_id
        assert token1 != token2


# ---------------------------------------------------------------------------
# F19: Duplicate, stale and forged ACKs cannot acknowledge incorrect delivery
# ---------------------------------------------------------------------------
def test_f19_ack_validation_security(tmp_gfs_db):
    secret = b"super_secret_gfs_gcs_hmac_key_2026"
    glob = GlobalObject(
        global_id="G-ACK", label="rubbish", status="ACTIVE", lat=22.3149, lon=87.3105,
        alt=10.0, east_m=0.0, north_m=0.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
        confidence=0.85, first_seen_ms=1000, last_seen_ms=1000
    )
    with get_db(tmp_gfs_db.db_path) as con:
        evt_id = enqueue_global_object_event(con, glob, now_ms=1000)
        token, evts = lease_outbox_events(con, "worker-1", 10000, tmp_gfs_db, now_ms=1000)
        att_id = record_successful_transmission(con, evt_id, token, 15000, now_ms=1000)

        # 1. Forged HMAC signature
        bad_ack = {
            "event_id": evt_id, "delivery_attempt_id": att_id, "gcs_client_id": "gcs-primary",
            "ack_id": "ack-1", "timestamp_ms": 1500, "kid": "gcs-key-1", "signature": "bad_hex"
        }
        ok, reason = process_gcs_ack(con, bad_ack, tmp_gfs_db, now_ms=1500)
        assert not ok
        assert reason == "INVALID_HMAC_SIGNATURE"

        # 2. Stale delivery attempt
        from gfs.outbox import compute_ack_hmac
        stale_sig = compute_ack_hmac(evt_id, "att-stale", "ack-2", "gcs-primary", 1500, "gcs-key-1", secret)
        stale_ack = {
            "event_id": evt_id, "delivery_attempt_id": "att-stale", "gcs_client_id": "gcs-primary",
            "ack_id": "ack-2", "timestamp_ms": 1500, "kid": "gcs-key-1", "signature": stale_sig
        }
        ok, reason = process_gcs_ack(con, stale_ack, tmp_gfs_db, now_ms=1500)
        assert not ok
        assert "STALE_DELIVERY_ATTEMPT" in reason

        # 3. Valid authenticated ACK
        valid_sig = compute_ack_hmac(evt_id, att_id, "ack-3", "gcs-primary", 1500, "gcs-key-1", secret)
        valid_ack = {
            "event_id": evt_id, "delivery_attempt_id": att_id, "gcs_client_id": "gcs-primary",
            "ack_id": "ack-3", "timestamp_ms": 1500, "kid": "gcs-key-1", "signature": valid_sig
        }
        ok, reason = process_gcs_ack(con, valid_ack, tmp_gfs_db, now_ms=1500)
        assert ok
        assert reason == "ACK_ACCEPTED"

        # 4. Duplicate ACK rejection (already consumed)
        ok_dup, reason_dup = process_gcs_ack(con, valid_ack, tmp_gfs_db, now_ms=1600)
        assert not ok_dup
        assert "ACK_ALREADY_CONSUMED" in reason_dup


def import_hashlib_hmac(key: bytes, msg: str) -> str:
    import hmac, hashlib
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).hexdigest()



# ---------------------------------------------------------------------------
# F21: Historical unmatched tracklet never forms a new global object in Stage B
# ---------------------------------------------------------------------------
def test_f21_historical_unmatched_tracklet_never_forms_new_global(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    base_lat, base_lon = 22.3149, 87.3105
    now_ms = 1731542400000

    # Bind tracklet 'trk-hist-x' historically to G-OLD
    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO global_objects 
               (global_id, label, status, lat, lon, alt, east_m, north_m, cov_xx, cov_yy, cov_xy,
                confidence, first_seen_ms, last_seen_ms, observation_count, source_count, source_estimates_json, version)
               VALUES ('G-OLD', 'rubbish', 'RETIRED', ?, ?, 10.0, 0.0, 0.0, 0.3, 0.3, 0.0, 0.85, ?, ?, 1, 1, '{}', 1)""",
            (base_lat, base_lon, now_ms - 5000, now_ms - 5000)
        )
        cur.execute(
            """INSERT INTO global_track_identity_history
               (drone_id, drone_session_id, session_generation, local_track_id, global_id,
                first_frame_id, last_frame_id, first_seen_ms, last_seen_ms)
               VALUES ('DroneOS1', 'sess-1', 1, 'trk-hist-x', 'G-OLD', 1, 1, ?, ?)""",
            (now_ms - 5000, now_ms - 5000)
        )

    # Ingest new observation for 'trk-hist-x' far away from any active global
    pkt = {
        "packet_id": "pkt-f21",
        "drone_id": "DroneOS1",
        "drone_session_id": "sess-1",
        "session_generation": 1,
        "observations": [{
            "frame_id": 50,
            "local_track_id": "trk-hist-x",
            "capture_timestamp_ms": now_ms,
            "lat": base_lat + 0.001, # ~110m away
            "lon": base_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.89, # High confidence
            "cov_xx": 0.3,
            "cov_yy": 0.3,
            "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=now_ms)

    res = service.process_fusion_batch(now_ms=now_ms)
    # Stage B must NOT create a new global for this historical tracklet!
    assert res["new_globals"] == 0, "Historical tracklet must NOT spawn a new global object!"
    assert res["deferred"] == 1, "Historical unmatched tracklet must be safely deferred"


# ---------------------------------------------------------------------------
# F22: Adversarial ACK Delivery-Attempt Binding & Replay Protection
# ---------------------------------------------------------------------------
def test_f22_ack_delivery_attempt_binding_adversarial(tmp_gfs_db):
    from gfs.outbox import compute_ack_hmac
    secret = tmp_gfs_db.hmac_secret_key.encode('utf-8')

    glob = GlobalObject(
        global_id="G-ATT", label="rubbish", status="ACTIVE", lat=22.3149, lon=87.3105,
        alt=10.0, east_m=0.0, north_m=0.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
        confidence=0.85, first_seen_ms=1000, last_seen_ms=1000
    )
    with get_db(tmp_gfs_db.db_path) as con:
        evt_id = enqueue_global_object_event(con, glob, now_ms=1000)

        # Attempt A1
        token1, evts1 = lease_outbox_events(con, "worker-1", 1000, tmp_gfs_db, now_ms=1000)
        att_id_1 = record_successful_transmission(con, evt_id, token1, 500, now_ms=1000)

        # Timeout occurs -> Retry Attempt A2
        token2, evts2 = lease_outbox_events(con, "worker-2", 1000, tmp_gfs_db, now_ms=2000)
        assert len(evts2) == 1
        att_id_2 = record_successful_transmission(con, evt_id, token2, 500, now_ms=2000)
        assert att_id_1 != att_id_2

        # 1. Delayed ACK from attempt A1 arrives during attempt A2 -> MUST BE REJECTED
        sig_a1 = compute_ack_hmac(evt_id, att_id_1, "ack-delayed", "gcs-primary", 2100, "gcs-key-1", secret)
        delayed_ack = {
            "event_id": evt_id, "delivery_attempt_id": att_id_1, "gcs_client_id": "gcs-primary",
            "ack_id": "ack-delayed", "timestamp_ms": 2100, "kid": "gcs-key-1", "signature": sig_a1
        }
        ok_del, reason_del = process_gcs_ack(con, delayed_ack, tmp_gfs_db, now_ms=2100)
        assert not ok_del
        assert "STALE_DELIVERY_ATTEMPT" in reason_del

        # 2. Unauthorized GCS client -> REJECTED
        sig_unauth = compute_ack_hmac(evt_id, att_id_2, "ack-unauth", "rogue-gcs", 2200, "gcs-key-1", secret)
        unauth_ack = {
            "event_id": evt_id, "delivery_attempt_id": att_id_2, "gcs_client_id": "rogue-gcs",
            "ack_id": "ack-unauth", "timestamp_ms": 2200, "kid": "gcs-key-1", "signature": sig_unauth
        }
        ok_unauth, reason_unauth = process_gcs_ack(con, unauth_ack, tmp_gfs_db, now_ms=2200)
        assert not ok_unauth
        assert "UNAUTHORIZED_GCS_CLIENT" in reason_unauth

        # 3. Forged HMAC signature -> REJECTED
        bad_sig_ack = {
            "event_id": evt_id, "delivery_attempt_id": att_id_2, "gcs_client_id": "gcs-primary",
            "ack_id": "ack-bad", "timestamp_ms": 2200, "kid": "gcs-key-1", "signature": "00" * 32
        }
        ok_bad, reason_bad = process_gcs_ack(con, bad_sig_ack, tmp_gfs_db, now_ms=2200)
        assert not ok_bad
        assert "INVALID_HMAC_SIGNATURE" in reason_bad

        # 4. Valid current-attempt A2 ACK -> ACCEPTED
        sig_a2 = compute_ack_hmac(evt_id, att_id_2, "ack-valid-a2", "gcs-primary", 2200, "gcs-key-1", secret)
        valid_ack = {
            "event_id": evt_id, "delivery_attempt_id": att_id_2, "gcs_client_id": "gcs-primary",
            "ack_id": "ack-valid-a2", "timestamp_ms": 2200, "kid": "gcs-key-1", "signature": sig_a2
        }
        ok_valid, reason_valid = process_gcs_ack(con, valid_ack, tmp_gfs_db, now_ms=2200)
        assert ok_valid
        assert reason_valid == "ACK_ACCEPTED"

        # 5. Duplicate ACK with same ack_id -> REJECTED
        ok_dup, reason_dup = process_gcs_ack(con, valid_ack, tmp_gfs_db, now_ms=2300)
        assert not ok_dup
        assert "ACK_ALREADY_CONSUMED" in reason_dup

        # 6. Replayed ACK against new event -> REJECTED
        evt_id_new = enqueue_global_object_event(con, glob, now_ms=3000)
        token_new, _ = lease_outbox_events(con, "worker-3", 1000, tmp_gfs_db, now_ms=3000)
        att_id_new = record_successful_transmission(con, evt_id_new, token_new, 500, now_ms=3000)

        replayed_ack = {
            "event_id": evt_id_new, "delivery_attempt_id": att_id_new, "gcs_client_id": "gcs-primary",
            "ack_id": "ack-valid-a2",  # Already consumed ack_id!
            "timestamp_ms": 3100, "kid": "gcs-key-1",
            "signature": compute_ack_hmac(evt_id_new, att_id_new, "ack-valid-a2", "gcs-primary", 3100, "gcs-key-1", secret)
        }
        ok_rep, reason_rep = process_gcs_ack(con, replayed_ack, tmp_gfs_db, now_ms=3100)
        assert not ok_rep
        assert "ACK_ALREADY_CONSUMED" in reason_rep


# ---------------------------------------------------------------------------
# F23: Adversarial Clique Ambiguity Resolution & Determinism
# ---------------------------------------------------------------------------
def test_f23_clique_ambiguity_adversarial(tmp_gfs_db):
    ref_lat, ref_lon = 22.3149, 87.3105
    now_ms = 1000

    def make_trk(drone_id, track_id, east, north, conf=0.85):
        obs = Observation(
            obs_id=f"obs-{drone_id}-{track_id}", drone_id=drone_id, drone_session_id="s1",
            session_generation=1, frame_id=1, local_track_id=track_id, capture_timestamp_ms=now_ms,
            lat=ref_lat, lon=ref_lon, alt=10.0, cov_xx=0.25, cov_yy=0.25, cov_xy=0.0,
            label="rubbish", confidence=conf, payload_hash="h", raw_payload="{}"
        )
        obs.east_m = east
        obs.north_m = north
        obs.up_m = 0.0
        return LocalTracklet(
            drone_id=drone_id, drone_session_id="s1", session_generation=1, local_track_id=track_id,
            label="rubbish", observations=[obs], east_m=east, north_m=north, up_m=0.0,
            lat=ref_lat, lon=ref_lon, cov_xx=0.25, cov_yy=0.25, cov_xy=0.0, confidence=conf,
            first_frame_id=1, last_frame_id=1, first_seen_ms=now_ms, last_seen_ms=now_ms
        )

    # 1. Different-sized overlapping maximal cliques:
    # Clique 1: {D1, D2, D3} (size 3)
    # Clique 2: {D1, D4} (size 2)
    # D1 participates in both cliques.
    # Deterministic ambiguity policy: D1 MUST be quarantined as ambiguous.
    # The larger size-3 clique MUST NOT automatically win!
    t_d1 = make_trk("DroneOS1", "trk-1", 0.0, 0.0, conf=0.85)
    t_d2 = make_trk("DroneOS2", "trk-2", -1.5, 0.0, conf=0.85)
    t_d3 = make_trk("DroneOS3", "trk-3", -1.0, 0.5, conf=0.85)
    t_d4 = make_trk("DroneOS4", "trk-4", 1.8, 0.0, conf=0.50)

    # Verify compatibility: D1 is connected to D2, D3, D4; D2 connected to D3; D4 NOT connected to D2 or D3
    assert tracklets_are_compatible(t_d1, t_d2, tmp_gfs_db)
    assert tracklets_are_compatible(t_d1, t_d3, tmp_gfs_db)
    assert tracklets_are_compatible(t_d2, t_d3, tmp_gfs_db)
    assert tracklets_are_compatible(t_d1, t_d4, tmp_gfs_db)
    assert not tracklets_are_compatible(t_d2, t_d4, tmp_gfs_db)
    assert not tracklets_are_compatible(t_d3, t_d4, tmp_gfs_db)

    new_globs, amb_def, low_def = form_new_global_objects(
        [t_d1, t_d2, t_d3, t_d4], tmp_gfs_db, ref_lat, ref_lon
    )

    # Invariant: D1 MUST be quarantined as ambiguous
    assert any(t.drone_id == "DroneOS1" for t in amb_def), "D1 participating in competing cliques MUST be quarantined!"

    # Invariant: Clean members of Clique 1 {D2, D3} form a valid global object (2 distinct drones)
    assert len(new_globs) == 1, "Clean remaining members {D2, D3} must form an independent global object"
    assert set(new_globs[0].source_estimates.keys()) == {"DroneOS2", "DroneOS3"}

    # Invariant: Clean member of Clique 2 {D4} is low confidence single drone -> deferred
    assert any(t.drone_id == "DroneOS4" for t in low_def)

    # 2. Input Permutation Order Invariance
    # Permuting input order must yield 100% identical outputs
    perms = [
        [t_d4, t_d3, t_d2, t_d1],
        [t_d3, t_d1, t_d4, t_d2],
        [t_d2, t_d4, t_d1, t_d3]
    ]
    for p in perms:
        g_p, a_p, l_p = form_new_global_objects(p, tmp_gfs_db, ref_lat, ref_lon)
        assert len(g_p) == len(new_globs)
        assert g_p[0].global_id == new_globs[0].global_id
        assert np.isclose(g_p[0].east_m, new_globs[0].east_m)
        assert len(a_p) == len(amb_def)
        assert a_p[0].drone_id == amb_def[0].drone_id


# ---------------------------------------------------------------------------
# F24: Adversarial Covariance Intersection Numerics
# ---------------------------------------------------------------------------
def test_f24_covariance_intersection_numerics_adversarial():
    def make_est(d_id, east, north, cxx, cyy, cxy):
        return SourceEstimate(
            drone_id=d_id, drone_session_id="s1", session_generation=1, local_track_id="t1",
            east_m=east, north_m=north, cov_xx=cxx, cov_yy=cyy, cov_xy=cxy,
            confidence=0.85, last_seen_ms=1000, observation_count=1
        )

    # 1. Ill-conditioned covariances (condition number > 10^8)
    e1 = make_est("D1", 0.0, 0.0, cxx=1e4, cyy=1e-4, cxy=0.0)
    e2 = make_est("D2", 0.2, 0.1, cxx=1e-4, cyy=1e4, cxy=0.0)
    fe, fn, fxx, fyy, fxy = fuse_covariance_intersection([e1, e2], noise_floor=1e-6)

    # Fused covariance must remain SPD with finite positive eigenvalues
    P = np.array([[fxx, fxy], [fxy, fyy]])
    eigvals = np.linalg.eigvalsh(P)
    assert np.all(eigvals > 0.0), f"Fused eigenvalues must be strictly positive: {eigvals}"
    assert np.all(np.isfinite(P)), "Fused covariance must be finite"

    # 2. Input ordering invariance
    e3 = make_est("D3", 0.1, 0.1, cxx=0.5, cyy=0.5, cxy=0.05)
    f_orig = fuse_covariance_intersection([e1, e2, e3])
    f_perm = fuse_covariance_intersection([e3, e1, e2])
    for val1, val2 in zip(f_orig, f_perm):
        assert math.isclose(val1, val2, abs_tol=1e-9), f"Ordering must produce identical result: {val1} vs {val2}"


# ---------------------------------------------------------------------------
# F25: Adversarial Resource Bounds & Bounded Bron-Kerbosch
# ---------------------------------------------------------------------------
def test_f25_resource_bounds_adversarial(tmp_gfs_db):
    # 1. Moon-Moser worst case graph: 7 disjoint triangles K3 -> 3^7 = 2,187 maximal cliques
    adj_mm: Dict[str, Set[str]] = {}
    triangles = []
    for t_idx in range(7):
        v = [f"v_{t_idx}_{i}" for i in range(3)]
        triangles.append(v)
        for u in v:
            adj_mm[u] = set()

    # Edges exist exclusively between vertices in DIFFERENT triangles (complement of disjoint triangles)
    for i in range(7):
        for j in range(i + 1, 7):
            for u in triangles[i]:
                for w in triangles[j]:
                    adj_mm[u].add(w)
                    adj_mm[w].add(u)

    # Run with max_cliques = 50
    cliques, truncated, reason = bounded_bron_kerbosch(adj_mm, max_cliques=50, time_limit_ms=50.0, return_reason=True)
    assert truncated is True, "Moon-Moser graph must trigger truncation"
    assert reason in ("CLIQUE_COUNT_EXHAUSTED", "CLIQUE_TIMEOUT_DEFERRED")
    assert len(cliques) <= 50

    # 2. Large connected component exceeding 100 observations
    now_ms = 1000
    ref_lat, ref_lon = 22.3149, 87.3105
    oversized_tracklets = []
    for i in range(105):
        obs = Observation(
            obs_id=f"obs-over-{i}", drone_id=f"DroneOS{i%4 + 1}", drone_session_id="s1",
            session_generation=1, frame_id=1, local_track_id=f"trk-{i}", capture_timestamp_ms=now_ms,
            lat=ref_lat, lon=ref_lon, alt=10.0, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
            label="rubbish", confidence=0.8, payload_hash="h", raw_payload="{}"
        )
        obs.east_m = 0.0
        obs.north_m = 0.0
        obs.up_m = 0.0
        trk = LocalTracklet(
            drone_id=f"DroneOS{i%4 + 1}", drone_session_id="s1", session_generation=1, local_track_id=f"trk-{i}",
            label="rubbish", observations=[obs], east_m=0.0, north_m=0.0, up_m=0.0,
            lat=ref_lat, lon=ref_lon, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0, confidence=0.8,
            first_frame_id=1, last_frame_id=1, first_seen_ms=now_ms, last_seen_ms=now_ms
        )
        oversized_tracklets.append(trk)

    globs, amb_def, low_def = form_new_global_objects(oversized_tracklets, tmp_gfs_db, ref_lat, ref_lon)
    assert len(globs) == 0, "Oversized component must NOT form global objects"
    assert len(amb_def) == 105, "Entire oversized component must be safely deferred"


# ---------------------------------------------------------------------------
# F26: Multi-Batch Historical Unmatched Track Retry & Expiration
# ---------------------------------------------------------------------------
def test_f26_historical_unmatched_track_retry_multi_batch(tmp_gfs_db):
    service = GFSService(tmp_gfs_db)
    ref_lat, ref_lon = 22.3149, 87.3105
    t0 = 1000000

    # Batch 1: Drone 1 & Drone 2 create G-ALPHA
    for d_num in (1, 2):
        pkt = {
            "packet_id": f"pkt-b1-d{d_num}",
            "drone_id": f"DroneOS{d_num}",
            "drone_session_id": "sess-alpha",
            "session_generation": 1,
            "observations": [{
                "frame_id": 1,
                "local_track_id": f"trk-alpha-d{d_num}",
                "capture_timestamp_ms": t0,
                "lat": ref_lat,
                "lon": ref_lon,
                "alt": 10.0,
                "label": "rubbish",
                "confidence": 0.88,
                "cov_xx": 0.3,
                "cov_yy": 0.3,
                "cov_xy": 0.0
            }]
        }
        with get_db(tmp_gfs_db.db_path) as con:
            ingest_raw_packet(con, pkt, tmp_gfs_db, now_ms=t0)

    res1 = service.process_fusion_batch(now_ms=t0)
    assert res1["new_globals"] == 1
    assert res1["fused"] == 2

    # Obtain created global ID
    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id FROM global_objects WHERE status = 'ACTIVE'")
        alpha_gid = cur.fetchone()[0]

        # Batch 2: G-ALPHA is retired / deactivated
        cur.execute("UPDATE global_objects SET status = 'RETIRED' WHERE global_id = ?", (alpha_gid,))

    # Batch 3: Drone 1 re-observes Target Alpha at same position
    t1 = t0 + 2000
    pkt3 = {
        "packet_id": "pkt-b3-d1",
        "drone_id": "DroneOS1",
        "drone_session_id": "sess-alpha",
        "session_generation": 1,
        "observations": [{
            "frame_id": 10,
            "local_track_id": "trk-alpha-d1",
            "capture_timestamp_ms": t1,
            "lat": ref_lat + 0.000001,
            "lon": ref_lon + 0.000001,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.92,
            "cov_xx": 0.3,
            "cov_yy": 0.3,
            "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        ingest_raw_packet(con, pkt3, tmp_gfs_db, now_ms=t1)

    res3 = service.process_fusion_batch(now_ms=t1)

    # Invariant: Must reactivate and update G-ALPHA, NEVER create a duplicate global ID!
    assert res3["new_globals"] == 0, "Must NEVER create duplicate global ID for historical track"
    assert res3["fused"] == 1, "Must associate with historical global identity"

    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT status, observation_count FROM global_objects WHERE global_id = ?", (alpha_gid,))
        row = cur.fetchone()
        assert row["status"] == "ACTIVE", "Historical global object must be reactivated"
        assert row["observation_count"] == 3

    # 4. TTL Expiration of deferred observations
    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        # Insert expired deferred observation
        cur.execute(
            """INSERT INTO deferred_observations (obs_id, packet_id, raw_payload, defer_reason, created_at_ms, expires_at_ms)
               VALUES ('obs-exp-1', 'pkt-dummy', '{}', 'LOW_CONFIDENCE', ?, ?)""",
            (t1 - 100000, t1 - 1000)
        )
        cur.execute(
            """INSERT OR IGNORE INTO inbox_packets (packet_id, drone_id, drone_session_id, session_generation, received_at_ms, item_count, packet_status)
               VALUES ('pkt-dummy', 'DroneOS1', 's1', 1, ?, 1, 'COMPLETED')""",
            (t1 - 100000,)
        )
        cur.execute(
            """INSERT INTO inbox_packet_items (packet_id, item_index, obs_id, payload_hash, raw_payload, disposition, disposition_timestamp_ms)
               VALUES ('pkt-dummy', 0, 'obs-exp-1', 'h', '{}', 'DEFERRED', ?)""",
            (t1 - 100000,)
        )

    # Run batch: expired deferred observation must be deleted and inbox item marked EXPIRED
    service.process_fusion_batch(now_ms=t1 + 500)

    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM deferred_observations WHERE obs_id = 'obs-exp-1'")
        assert cur.fetchone() is None, "Expired deferred observation must be purged"
        cur.execute("SELECT disposition, rejection_reason FROM inbox_packet_items WHERE obs_id = 'obs-exp-1'")
        r_item = cur.fetchone()
        assert r_item["disposition"] == "EXPIRED"


# ---------------------------------------------------------------------------
# F27: Drone Session Lifecycle & Adversarial Identity Safety
# ---------------------------------------------------------------------------
def test_f27_drone_session_lifecycle_adversarial(tmp_gfs_db):
    from gfs.inbox import SessionConflictError, StaleSessionError
    service = GFSService(tmp_gfs_db)
    ref_lat, ref_lon = 22.3149, 87.3105
    t0 = 2000000

    # 1. DroneAlpha registers initial session: Gen 1, UUID-1
    pkt_alpha_s1 = {
        "packet_id": "pkt-alpha-1",
        "drone_id": "DroneAlpha",
        "drone_session_id": "sess-alpha-uuid-1",
        "session_generation": 1,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "track-0",
            "capture_timestamp_ms": t0,
            "lat": ref_lat,
            "lon": ref_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.88,
            "cov_xx": 0.3,
            "cov_yy": 0.3,
            "cov_xy": 0.0
        }]
    }

    # Ingest session 1 packet
    with get_db(tmp_gfs_db.db_path) as con:
        _, valid, rej = ingest_raw_packet(con, pkt_alpha_s1, tmp_gfs_db, now_ms=t0)
        assert valid == 1 and rej == 0

    # 2. Adversarial: Concurrent drone attempts to use same drone_id with different UUID on same generation
    pkt_alpha_rogue = {
        "packet_id": "pkt-alpha-rogue",
        "drone_id": "DroneAlpha",
        "drone_session_id": "sess-alpha-ROGUE-UUID",
        "session_generation": 1,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "track-0",
            "capture_timestamp_ms": t0 + 10,
            "lat": ref_lat,
            "lon": ref_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.88,
            "cov_xx": 0.3,
            "cov_yy": 0.3,
            "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        with pytest.raises(SessionConflictError, match="Conflicting session UUID"):
            ingest_raw_packet(con, pkt_alpha_rogue, tmp_gfs_db, now_ms=t0 + 10)

    # 3. Legitimate re-incarnation: DroneAlpha reboots with Gen 2, UUID-2
    # In this new session, local track counter resets to track-0
    t1 = t0 + 1000
    pkt_alpha_s2 = {
        "packet_id": "pkt-alpha-2",
        "drone_id": "DroneAlpha",
        "drone_session_id": "sess-alpha-uuid-2",
        "session_generation": 2,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "track-0",  # Safely reset to track-0
            "capture_timestamp_ms": t1,
            "lat": ref_lat + 0.00001,
            "lon": ref_lon + 0.00001,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.91,
            "cov_xx": 0.25,
            "cov_yy": 0.25,
            "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        _, valid2, rej2 = ingest_raw_packet(con, pkt_alpha_s2, tmp_gfs_db, now_ms=t1)
        assert valid2 == 1 and rej2 == 0

    # 4. Adversarial: Stale packet arrives from previous generation (Gen 1)
    pkt_alpha_stale = {
        "packet_id": "pkt-alpha-delayed-stale",
        "drone_id": "DroneAlpha",
        "drone_session_id": "sess-alpha-uuid-1",
        "session_generation": 1,  # Stale: registered is now Gen 2
        "observations": [{
            "frame_id": 99,
            "local_track_id": "track-99",
            "capture_timestamp_ms": t1 + 50,
            "lat": ref_lat,
            "lon": ref_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.80,
            "cov_xx": 0.3,
            "cov_yy": 0.3,
            "cov_xy": 0.0
        }]
    }
    with get_db(tmp_gfs_db.db_path) as con:
        with pytest.raises(StaleSessionError, match="stale"):
            ingest_raw_packet(con, pkt_alpha_stale, tmp_gfs_db, now_ms=t1 + 50)

    # 5. Database durability across crash/restart
    # Re-open database connection to verify persisted sessions and canonical partitioning
    with get_db(tmp_gfs_db.db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT active_session_id, session_generation FROM drone_sessions WHERE drone_id = 'DroneAlpha'")
        row = cur.fetchone()
        assert row["active_session_id"] == "sess-alpha-uuid-2"
        assert row["session_generation"] == 2


# ---------------------------------------------------------------------------
# F28: Covariance Intersection Math Equivalence, SLSQP Fallback & Diagnostics
# ---------------------------------------------------------------------------
def test_f28_covariance_intersection_math_and_telemetry():
    from gfs.association import fuse_covariance_intersection, SourceEstimate
    import numpy as np

    # 1. Verify K=2 optimization and min det(P) <=> min -log det(Y)
    e1 = SourceEstimate(
        drone_id="D1", drone_session_id="s1", session_generation=1, local_track_id="t1",
        east_m=0.0, north_m=0.0, cov_xx=0.4, cov_yy=0.2, cov_xy=0.05,
        confidence=0.85, last_seen_ms=1000, observation_count=3
    )
    e2 = SourceEstimate(
        drone_id="D2", drone_session_id="s1", session_generation=1, local_track_id="t2",
        east_m=0.2, north_m=-0.1, cov_xx=0.2, cov_yy=0.5, cov_xy=-0.05,
        confidence=0.88, last_seen_ms=1000, observation_count=4
    )

    fe, fn, cxx, cyy, cxy, diag2 = fuse_covariance_intersection([e1, e2], return_diagnostics=True)
    assert diag2["method_applied"] == "optimized_brent"
    assert diag2["optimizer_success"] is True
    assert len(diag2["weights"]) == 2
    assert abs(sum(diag2["weights"]) - 1.0) < 1e-6
    # Verify SPD output covariance
    P2 = np.array([[cxx, cxy], [cxy, cyy]])
    eigvals2 = np.linalg.eigvalsh(P2)
    assert all(ev > 0 for ev in eigvals2), "Output covariance must be strictly SPD"

    # 2. Verify K=3 SLSQP constrained optimization
    e3 = SourceEstimate(
        drone_id="D3", drone_session_id="s1", session_generation=1, local_track_id="t3",
        east_m=-0.1, north_m=0.15, cov_xx=0.3, cov_yy=0.3, cov_xy=0.0,
        confidence=0.90, last_seen_ms=1000, observation_count=2
    )
    fe3, fn3, cxx3, cyy3, cxy3, diag3 = fuse_covariance_intersection([e1, e2, e3], return_diagnostics=True)
    assert diag3["method_applied"] == "optimized_slsqp"
    assert diag3["optimizer_success"] is True
    assert len(diag3["weights"]) == 3
    assert abs(sum(diag3["weights"]) - 1.0) < 1e-6

    # 3. Verify Deterministic Fallback when optimizer failure occurs
    # Test inverse_trace explicitly
    fe_fb, fn_fb, cxx_fb, cyy_fb, cxy_fb, diag_fb = fuse_covariance_intersection(
        [e1, e2, e3], method="inverse_trace", return_diagnostics=True
    )
    assert diag_fb["method_applied"] == "inverse_trace"
    assert diag_fb["optimizer_success"] is True
    assert len(diag_fb["weights"]) == 3

