"""
tests/test_provisional_lifecycle.py

Comprehensive test suite for GFS Provisional Detection Lifecycle:
1. One drone, confidence 0.62 -> PROVISIONAL.
2. One drone, confidence 0.69 -> PROVISIONAL.
3. One drone, confidence 0.70 -> CONFIRMED (existing confirmation behavior).
4. Two independent compatible drones -> CONFIRMED.
5. Two observations from the same drone -> not multi-drone confirmation.
6. Invalid coordinates -> rejected / not provisional.
7. Stale observation -> rejected / deferred according to contract.
8. Ambiguous clique -> deferred as AMBIGUOUS_CLIQUE (not silently confirmed).
9. Provisional persistence across service restart.
10. Provisional expiry (TTL exceeded -> RETIRED with tombstone & outbox event).
11. Promotion without duplicate global objects (preserves ID, emits confirmation).
12. Version-aware retirement and reactivation.
13. Relay snapshot serialization (includes provisional & confirmed with metadata).
14. Experimental temporal parallax confirmation (gated behind configuration flag).
"""

import os
import sys
import json
import time
import math
from typing import Optional, List, Dict, Set, Tuple
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gfs.models import Observation, LocalTracklet, GlobalObject, LifecycleState, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.database import init_db, get_db, create_connection
from gfs.service import GFSService
from gfs.association import build_local_tracklets, update_global_object_with_tracklet
from gfs.hypothesis import form_new_global_objects
from gfs.inbox import ingest_raw_packet
from gfs.outbox import lease_outbox_events
from relay.relay import UdpWebsocketRelay


@pytest.fixture
def test_env(tmp_path):
    db_file = str(tmp_path / "test_lifecycle_gfs.db")
    lock_file = str(tmp_path / "test_lifecycle_gfs.lock")
    init_db(db_file)
    cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=47.641468,
        ref_origin_lon=-122.140165,
        ref_origin_alt=0.0,
        spatial_physical_r_max_m=3.5,
        provisional_object_ttl_ms=5000,
    )
    service = GFSService(cfg)
    return cfg, service, db_file


def create_sample_packet(drone_id: str, track_id: str, lat: float, lon: float, conf: float, label: str = "rubbish", now_ms: int = 1000, frame_id: Optional[int] = None):
    fid = frame_id if frame_id is not None else int(now_ms // 10)
    return {
        "packet_id": f"pkt-{drone_id}-{track_id}-{now_ms}-{fid}",
        "drone_id": drone_id,
        "drone_session_id": f"sess-{drone_id}",
        "session_generation": 1,
        "observations": [{
            "frame_id": fid,
            "local_track_id": track_id,
            "capture_timestamp_ms": now_ms,
            "lat": lat,
            "lon": lon,
            "alt": 10.0,
            "label": label,
            "confidence": conf,
            "cov_xx": 0.35,
            "cov_yy": 0.35,
            "cov_xy": 0.01
        }]
    }


def test_single_drone_conf_062_becomes_provisional(test_env):
    """Test 1: One drone, confidence 0.62 -> PROVISIONAL."""
    cfg, service, db_file = test_env
    pkt = create_sample_packet("drone4", "trk-01", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt, cfg, now_ms=1000)

    res = service.process_fusion_batch(now_ms=1000)
    assert res["status"] == "SUCCESS"
    assert res["new_globals"] == 1
    assert res["provisional_created"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lifecycle_state, status, confidence, provisional_reason FROM global_objects")
        row = cur.fetchone()
        assert row is not None
        assert row["lifecycle_state"] == LifecycleState.PROVISIONAL.value
        assert row["status"] == "ACTIVE"
        assert row["confidence"] == 0.62
        assert row["provisional_reason"] == "SINGLE_DRONE_LOW_CONFIDENCE"


def test_single_drone_conf_069_becomes_provisional(test_env):
    """Test 2: One drone, confidence 0.69 -> PROVISIONAL."""
    cfg, service, db_file = test_env
    pkt = create_sample_packet("drone1", "trk-02", cfg.ref_origin_lat + 0.0001, cfg.ref_origin_lon, 0.69, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt, cfg, now_ms=1000)

    res = service.process_fusion_batch(now_ms=1000)
    assert res["status"] == "SUCCESS"
    assert res["provisional_created"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, status, confidence FROM global_objects")
        row = cur.fetchone()
        assert row["lifecycle_state"] == LifecycleState.PROVISIONAL.value
        assert row["confidence"] == 0.69


def test_single_drone_conf_070_becomes_confirmed(test_env):
    """Test 3: One drone, confidence 0.70 -> existing CONFIRMED behavior."""
    cfg, service, db_file = test_env
    pkt = create_sample_packet("drone2", "trk-03", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.70, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt, cfg, now_ms=1000)

    res = service.process_fusion_batch(now_ms=1000)
    assert res["status"] == "SUCCESS"
    assert res["confirmed_created"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, status, confirmation_method FROM global_objects")
        row = cur.fetchone()
        assert row["lifecycle_state"] == LifecycleState.CONFIRMED.value
        assert row["confirmation_method"] == "single_drone_high_confidence"


def test_two_independent_compatible_drones_confirmed(test_env):
    """Test 4: Two independent compatible drones -> CONFIRMED via Covariance Intersection."""
    cfg, service, db_file = test_env
    pkt1 = create_sample_packet("drone2", "trk-alpha", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.60, now_ms=1000)
    pkt2 = create_sample_packet("drone3", "trk-beta", cfg.ref_origin_lat + 0.000002, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
        ingest_raw_packet(con, pkt2, cfg, now_ms=1000)

    res = service.process_fusion_batch(now_ms=1000)
    assert res["status"] == "SUCCESS"
    assert res["new_globals"] == 1
    assert res["confirmed_created"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, source_count, confirmation_method, confidence FROM global_objects")
        row = cur.fetchone()
        assert row["lifecycle_state"] == LifecycleState.CONFIRMED.value
        assert row["source_count"] == 2
        assert row["confirmation_method"] == "multi_drone_hypothesis"
        assert row["confidence"] > 0.80  # combined probabilistic union


def test_two_observations_same_drone_not_multi_drone_confirmation(test_env):
    """Test 5: Two observations from the SAME drone must NEVER be counted as multi-drone confirmation."""
    cfg, service, db_file = test_env
    # First batch: Drone 1 sees target with conf 0.62
    pkt1 = create_sample_packet("drone1", "trk-01", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Second batch: SAME Drone 1 sends another observation for trk-01 with conf 0.64
    pkt2 = create_sample_packet("drone1", "trk-01", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.64, now_ms=2000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=2000)
    service.process_fusion_batch(now_ms=2000)

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, source_count, observation_count FROM global_objects")
        row = cur.fetchone()
        assert row["lifecycle_state"] == LifecycleState.PROVISIONAL.value
        assert row["source_count"] == 1, "Must only have 1 source drone"
        assert row["observation_count"] == 2, "Must aggregate 2 observations"


def test_invalid_coordinates_rejected_or_deferred(test_env):
    """Test 6: Candidate with invalid / (0,0) coordinates must NOT become provisional or confirmed."""
    cfg, service, db_file = test_env
    obs = Observation(
        obs_id="obs-inv-01",
        drone_id="drone1",
        drone_session_id="sess-1",
        session_generation=1,
        frame_id=1,
        local_track_id="trk-inv",
        capture_timestamp_ms=1000,
        lat=0.0,
        lon=0.0,
        alt=0.0,
        cov_xx=0.35,
        cov_yy=0.35,
        cov_xy=0.0,
        label="rubbish",
        confidence=0.62,
        payload_hash="h-inv",
    )
    obs.east_m, obs.north_m, obs.up_m = 0.0, 0.0, 0.0
    tracklets = build_local_tracklets([obs])

    new_globals, ambiguous, low_conf = form_new_global_objects(
        unmatched_tracklets=tracklets,
        config=cfg,
        ref_lat=cfg.ref_origin_lat,
        ref_lon=cfg.ref_origin_lon,
        ref_alt=cfg.ref_origin_alt,
    )
    assert len(new_globals) == 0, "Invalid coordinates must not form any global object"
    assert len(low_conf) == 1, "Must be deferred"


def test_stale_session_observation_rejected(test_env):
    """Test 7: Stale drone session observation is rejected at ingestion."""
    cfg, service, db_file = test_env
    # Register session generation 2
    pkt_gen2 = {
        "packet_id": "pkt-gen2-01",
        "drone_id": "drone3",
        "drone_session_id": "sess-drone3",
        "session_generation": 2,
        "observations": [{
            "frame_id": 1,
            "local_track_id": "trk-gen2",
            "capture_timestamp_ms": 1000,
            "lat": cfg.ref_origin_lat,
            "lon": cfg.ref_origin_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.65,
            "cov_xx": 0.35,
            "cov_yy": 0.35,
            "cov_xy": 0.0
        }]
    }
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt_gen2, cfg, now_ms=1000)

    # Now attempt older generation 1 for same drone -> StaleSessionError
    pkt_gen1 = {
        "packet_id": "pkt-gen1-02",
        "drone_id": "drone3",
        "drone_session_id": "sess-drone3",
        "session_generation": 1,
        "observations": [{
            "frame_id": 2,
            "local_track_id": "trk-gen1",
            "capture_timestamp_ms": 1100,
            "lat": cfg.ref_origin_lat,
            "lon": cfg.ref_origin_lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": 0.65,
            "cov_xx": 0.35,
            "cov_yy": 0.35,
            "cov_xy": 0.0
        }]
    }
    from gfs.inbox import StaleSessionError
    with pytest.raises(StaleSessionError):
        with get_db(db_file) as con:
            ingest_raw_packet(con, pkt_gen1, cfg, now_ms=1100)


def test_ambiguous_clique_quarantine_not_silently_confirmed(test_env):
    """Test 8: Ambiguous cliques are quarantined as AMBIGUOUS_CLIQUE, not confirmed."""
    cfg, service, db_file = test_env
    # Create two overlapping tracks from same drone or bridge drone that form ambiguous clique
    # Drone1 and Drone2 see target at (0, 0), Drone3 is in the middle ambiguous bridge
    obs1 = Observation("obs-c1", "drone1", "s1", 1, 1, "t1", 1000, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.0, 0.25, 0.25, 0.0, "rubbish", 0.65, "h1")
    obs2 = Observation("obs-c2", "drone2", "s2", 1, 1, "t2", 1000, cfg.ref_origin_lat + 0.00002, cfg.ref_origin_lon, 0.0, 0.25, 0.25, 0.0, "rubbish", 0.65, "h2")
    # obs3 lies midway between two incompatible observations, bridging both
    obs3 = Observation("obs-c3", "drone3", "s3", 1, 1, "t3", 1000, cfg.ref_origin_lat + 0.00001, cfg.ref_origin_lon, 0.0, 0.25, 0.25, 0.0, "rubbish", 0.65, "h3")
    
    for o in [obs1, obs2, obs3]:
        e, n, u = wgs84_to_enu(o.lat, o.lon, o.alt, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.0)
        o.east_m, o.north_m, o.up_m = e, n, u

    tracklets = build_local_tracklets([obs1, obs2, obs3])
    # Set physical distance threshold so (obs1, obs2) is incompatible but obs3 is compatible with both
    cfg_clique = GFSConfig(spatial_physical_r_max_m=1.5, spatial_mahalanobis_thresh=3.0)
    new_globals, ambiguous, low_conf = form_new_global_objects(tracklets, cfg_clique, cfg.ref_origin_lat, cfg.ref_origin_lon, 0.0)
    # Ambiguous tracklets must be quarantined/deferred, not confirmed
    assert len(ambiguous) > 0, "Conflicting ambiguous clique candidates must be deferred"


def test_provisional_persistence_across_restart(test_env):
    """Test 9: Provisional candidates survive GFS process restart."""
    cfg, service, db_file = test_env
    pkt = create_sample_packet("drone4", "trk-persist", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Simulate restart by creating a completely new GFSService instance pointing to same DB
    new_service = GFSService(cfg)
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lifecycle_state, status FROM global_objects WHERE status = 'ACTIVE'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["lifecycle_state"] == LifecycleState.PROVISIONAL.value

    # Run batch on new instance - should safely maintain active provisional state
    res = new_service.process_fusion_batch(now_ms=1500)
    assert res["active_provisional_count"] == 1


def test_provisional_expiry_ttl_enforcement(test_env):
    """Test 10: Expired provisional objects transition to RETIRED and emit outbox event."""
    cfg, service, db_file = test_env
    # Provisional object created at t=1000 with TTL=5000ms -> expires at t=6000ms
    pkt = create_sample_packet("drone4", "trk-exp", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Batch at t=3000ms (within TTL) -> remains ACTIVE
    service.process_fusion_batch(now_ms=3000)
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT status, lifecycle_state FROM global_objects")
        row = cur.fetchone()
        assert row["status"] == "ACTIVE"
        assert row["lifecycle_state"] == LifecycleState.PROVISIONAL.value

    # Batch at t=7000ms (past TTL 6000ms) -> must EXPIRE to RETIRED
    res = service.process_fusion_batch(now_ms=7000)
    assert res["provisional_expired"] == 1

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT status, lifecycle_state, version FROM global_objects")
        row = cur.fetchone()
        assert row["status"] == "RETIRED"
        assert row["lifecycle_state"] == LifecycleState.RETIRED.value
        assert row["version"] >= 2

        # Verify retirement outbox event was enqueued
        cur.execute("SELECT payload FROM gfs_outbox_events WHERE status = 'PENDING'")
        events = [json.loads(r["payload"]) for r in cur.fetchall()]
        retired_events = [e for e in events if e.get("event_type") == "GLOBAL_OBJECT_RETIRED"]
        assert len(retired_events) == 1
        assert retired_events[0]["lifecycle_state"] == "RETIRED"


def test_promotion_preserves_identity_without_duplicates(test_env):
    """Test 11: Promotion of provisional object to confirmed preserves global_id without duplicate markers."""
    cfg, service, db_file = test_env
    # Step 1: Drone 4 discovers provisional object
    pkt1 = create_sample_packet("drone4", "trk-promo", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lifecycle_state, version FROM global_objects")
        init_row = cur.fetchone()
        assert init_row["lifecycle_state"] == LifecycleState.PROVISIONAL.value
        stable_gid = init_row["global_id"]
        init_ver = init_row["version"]

    # Step 2: Drone 2 independently observes the exact same physical location
    pkt2 = create_sample_packet("drone2", "trk-promo-mate", cfg.ref_origin_lat + 0.000001, cfg.ref_origin_lon, 0.65, now_ms=2000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=2000)
    batch_res = service.process_fusion_batch(now_ms=2000)

    assert batch_res["provisional_promoted"] == 1

    # Verify database: exactly 1 global object exists with same stable global_id, promoted to CONFIRMED
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, lifecycle_state, status, source_count, version, confirmation_method FROM global_objects")
        rows = cur.fetchall()
        assert len(rows) == 1, "Must NOT create a duplicate object upon promotion!"
        promoted_row = rows[0]
        assert promoted_row["global_id"] == stable_gid, "Promotion must preserve the exact logical object identity!"
        assert promoted_row["lifecycle_state"] == LifecycleState.CONFIRMED.value
        assert promoted_row["source_count"] == 2
        assert promoted_row["confirmation_method"] == "multi_drone_association"
        assert promoted_row["version"] > init_ver

        # Verify outbox event emitted GLOBAL_OBJECT_CONFIRMED
        cur.execute("SELECT payload FROM gfs_outbox_events WHERE status = 'PENDING'")
        events = [json.loads(r["payload"]) for r in cur.fetchall()]
        confirmed_evts = [e for e in events if e.get("event_type") == "GLOBAL_OBJECT_CONFIRMED"]
        assert len(confirmed_evts) == 1
        assert confirmed_evts[0]["global_id"] == stable_gid
        assert confirmed_evts[0]["lifecycle_state"] == "CONFIRMED"


def test_version_aware_retirement_and_reactivation(test_env):
    """Test 12: Version increments on retirement and reactivates properly if new observation matches."""
    cfg, service, db_file = test_env
    pkt1 = create_sample_packet("drone4", "trk-react", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Fast forward to expire provisional object
    service.process_fusion_batch(now_ms=7000)
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT global_id, status, version FROM global_objects")
        row = cur.fetchone()
        assert row["status"] == "RETIRED"
        v_retired = row["version"]
        gid = row["global_id"]

    # Now a new observation arrives for this exact track
    pkt2 = create_sample_packet("drone4", "trk-react", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.64, now_ms=8000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=8000)
    service.process_fusion_batch(now_ms=8000)

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT status, version FROM global_objects WHERE global_id = ?", (gid,))
        react_row = cur.fetchone()
        assert react_row["status"] == "ACTIVE"
        assert react_row["version"] > v_retired


@pytest.mark.asyncio
async def test_relay_snapshot_includes_provisional_and_confirmed(test_env, tmp_path):
    """Test 13: Relay snapshot accurately serializes both provisional and confirmed objects with full metadata."""
    cfg, service, db_file = test_env
    # Target A: Confirmed (2 drones)
    pkt_a1 = create_sample_packet("drone2", "trk-a1", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.65, now_ms=1000)
    pkt_a2 = create_sample_packet("drone3", "trk-a2", cfg.ref_origin_lat + 0.000002, cfg.ref_origin_lon, 0.65, now_ms=1000)
    # Target B: Provisional (1 drone, conf 0.61)
    pkt_b1 = create_sample_packet("drone4", "trk-b1", cfg.ref_origin_lat + 0.0002, cfg.ref_origin_lon, 0.61, now_ms=1000)

    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt_a1, cfg, now_ms=1000)
        ingest_raw_packet(con, pkt_a2, cfg, now_ms=1000)
        ingest_raw_packet(con, pkt_b1, cfg, now_ms=1000)

    service.process_fusion_batch(now_ms=1000)

    # Initialize Relay pointing to GFS DB
    relay = UdpWebsocketRelay(
        ws_port=8099,
        udp_bind_port=14598,
        gfs_db_path=db_file,
        relay_ack_db=str(tmp_path / "relay_test.db")
    )

    class MockWs:
        def __init__(self):
            self.sent_msgs = []
        async def send(self, data):
            self.sent_msgs.append(json.loads(data))

    ws = MockWs()
    await relay._handle_gfs_snapshot_request(ws, {"type": "gfs_snapshot_request"})

    assert len(ws.sent_msgs) == 1
    resp = ws.sent_msgs[0]
    assert resp["type"] == "gfs_snapshot_response"
    objects = resp["objects"]
    assert len(objects) == 2, "Snapshot must include both confirmed and provisional active objects!"

    by_state = {o["lifecycle_state"]: o for o in objects}
    assert "CONFIRMED" in by_state
    assert "PROVISIONAL" in by_state

    confirmed_obj = by_state["CONFIRMED"]
    provisional_obj = by_state["PROVISIONAL"]

    assert confirmed_obj["source_count"] == 2
    assert provisional_obj["source_count"] == 1
    assert provisional_obj["provisional_reason"] == "SINGLE_DRONE_LOW_CONFIDENCE"
    assert provisional_obj["confidence"] == 0.61


def test_experimental_temporal_parallax_confirmation(tmp_path):
    """Test 14: Experimental temporal parallax promotes candidate when enabled and criteria met."""
    db_file = str(tmp_path / "test_parallax.db")
    lock_file = str(tmp_path / "test_parallax.lock")
    init_db(db_file)
    cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=47.641468,
        ref_origin_lon=-122.140165,
        spatial_physical_r_max_m=6.0,
        enable_temporal_parallax_confirmation=True,
        temporal_parallax_min_baseline_m=4.0,
        temporal_parallax_min_duration_s=5.0,
        provisional_object_ttl_ms=30000,
    )
    service = GFSService(cfg)

    # Step 1: Initial observation at t=1000ms
    pkt1 = create_sample_packet("drone1", "trk-px", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state FROM global_objects")
        assert cur.fetchone()["lifecycle_state"] == LifecycleState.PROVISIONAL.value

    # Step 2: At t=7000ms (delta=6.0s >= 5.0s), drone translates 5.0m baseline
    lat2, lon2 = cfg.ref_origin_lat + 0.000045, cfg.ref_origin_lon  # ~5m North
    pkt2 = create_sample_packet("drone1", "trk-px", lat2, lon2, 0.63, now_ms=7000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=7000)
    res = service.process_fusion_batch(now_ms=7000)

    assert res["provisional_promoted"] == 1
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, confirmation_method FROM global_objects")
        row = cur.fetchone()
        assert row["lifecycle_state"] == LifecycleState.CONFIRMED.value
        assert row["confirmation_method"] == "temporal_parallax"
