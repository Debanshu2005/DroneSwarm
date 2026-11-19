"""
tests/test_milestone2_temporal_parallax.py

Phase 6: Temporal Parallax Audit & Geometric Validity Testing.
Audits:
1. Stationary hovering repeated detections cannot independently promote candidate.
2. Translation below 4 metres baseline does not promote.
3. Tracking duration below 5 seconds does not promote.
4. Production default (flag disabled) guarantees provisional candidate remains provisional.
5. Inconsistent / wild object position shifting cannot promote.
6. Valid translational baseline (>= 4m) and duration (>= 5s) promotes only when explicitly enabled.
"""

import sys
import math
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gfs.models import Observation, LocalTracklet, GlobalObject, LifecycleState
from gfs.config import GFSConfig
from gfs.database import init_db, get_db
from gfs.service import GFSService
from gfs.inbox import ingest_raw_packet


@pytest.fixture
def parallax_env(tmp_path):
    db_file = str(tmp_path / "parallax_audit.db")
    lock_file = str(tmp_path / "parallax_audit.lock")
    init_db(db_file)
    cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=47.641468,
        ref_origin_lon=-122.140165,
        ref_origin_alt=0.0,
        spatial_physical_r_max_m=6.0,
        enable_temporal_parallax_confirmation=False,  # Default production
        temporal_parallax_min_baseline_m=4.0,
        temporal_parallax_min_duration_s=5.0,
        provisional_object_ttl_ms=60000,
    )
    service = GFSService(cfg)
    return cfg, service, db_file


def make_parallax_packet(drone_id: str, track_id: str, lat: float, lon: float, conf: float, now_ms: int):
    return {
        "packet_id": f"pkt-{drone_id}-{track_id}-{now_ms}",
        "drone_id": drone_id,
        "drone_session_id": f"sess-{drone_id}",
        "session_generation": 1,
        "observations": [{
            "frame_id": int(now_ms // 10),
            "local_track_id": track_id,
            "capture_timestamp_ms": now_ms,
            "lat": lat,
            "lon": lon,
            "alt": 10.0,
            "label": "rubbish",
            "confidence": conf,
            "cov_xx": 0.25,
            "cov_yy": 0.25,
            "cov_xy": 0.0
        }]
    }


def test_stationary_hovering_does_not_promote(parallax_env):
    """
    Audit 1: Stationary hovering.
    Drone hovers at identical coordinates for 15 seconds (t=1000ms to t=16000ms),
    emitting repeated detections for trk-hov with conf=0.62.
    Invariant: Candidate MUST remain PROVISIONAL. Never promoted.
    """
    cfg, service, db_file = parallax_env
    # Even if feature was mistakenly enabled:
    cfg.enable_temporal_parallax_confirmation = True

    # Initial frame
    pkt1 = make_parallax_packet("drone1", "trk-hov", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Hovering frames over 15 seconds at exact same position (baseline = 0.0m)
    for t_ms in [4000, 8000, 12000, 16000]:
        pkt = make_parallax_packet("drone1", "trk-hov", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.63, now_ms=t_ms)
        with get_db(db_file) as con:
            ingest_raw_packet(con, pkt, cfg, now_ms=t_ms)
        service.process_fusion_batch(now_ms=t_ms)

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, confirmation_method, provisional_reason, observation_count FROM global_objects")
        row = cur.fetchone()
        assert row is not None
        assert row["lifecycle_state"] == LifecycleState.PROVISIONAL.value, "Stationary hovering must NEVER promote candidate"
        assert row["confirmation_method"] is None
        assert row["provisional_reason"] == "SINGLE_DRONE_LOW_CONFIDENCE"
        assert row["observation_count"] == 5


def test_translation_below_min_baseline_does_not_promote(parallax_env):
    """
    Audit 2: Translation below 4 metres (e.g. 2.0 metres).
    Drone translates 2.0m North (+0.000018 deg lat) over 10 seconds.
    Invariant: baseline < 4.0m -> Remains PROVISIONAL.
    """
    cfg, service, db_file = parallax_env
    cfg.enable_temporal_parallax_confirmation = True

    pkt1 = make_parallax_packet("drone1", "trk-sub4", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # Translate 2.0m North (approx 0.000018 deg lat) at t=11000 (10s later)
    lat_2m = cfg.ref_origin_lat + 0.000018
    pkt2 = make_parallax_packet("drone1", "trk-sub4", lat_2m, cfg.ref_origin_lon, 0.63, now_ms=11000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=11000)
    res = service.process_fusion_batch(now_ms=11000)

    assert res["provisional_promoted"] == 0
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state FROM global_objects")
        assert cur.fetchone()["lifecycle_state"] == LifecycleState.PROVISIONAL.value


def test_duration_below_min_time_does_not_promote(parallax_env):
    """
    Audit 3: Tracking duration below 5 seconds.
    Drone translates 5.0m North, but delta_t is only 2.0s (t=1000 to t=3000).
    Invariant: duration < 5.0s -> Remains PROVISIONAL.
    """
    cfg, service, db_file = parallax_env
    cfg.enable_temporal_parallax_confirmation = True

    pkt1 = make_parallax_packet("drone1", "trk-time", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    # 5.0m North at t=3000 (duration = 2.0s < 5.0s)
    lat_5m = cfg.ref_origin_lat + 0.000045
    pkt2 = make_parallax_packet("drone1", "trk-time", lat_5m, cfg.ref_origin_lon, 0.63, now_ms=3000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=3000)
    res = service.process_fusion_batch(now_ms=3000)

    assert res["provisional_promoted"] == 0
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state FROM global_objects")
        assert cur.fetchone()["lifecycle_state"] == LifecycleState.PROVISIONAL.value


def test_production_default_disabled_guarantees_provisional(parallax_env):
    """
    Audit 4: Production default configuration (enable_temporal_parallax_confirmation = False).
    Even when drone translates 6.0m over 10.0s, candidate remains PROVISIONAL.
    """
    cfg, service, db_file = parallax_env
    assert cfg.enable_temporal_parallax_confirmation is False, "Production default must be False"

    pkt1 = make_parallax_packet("drone1", "trk-prod", cfg.ref_origin_lat, cfg.ref_origin_lon, 0.62, now_ms=1000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt1, cfg, now_ms=1000)
    service.process_fusion_batch(now_ms=1000)

    lat_6m = cfg.ref_origin_lat + 0.000054
    pkt2 = make_parallax_packet("drone1", "trk-prod", lat_6m, cfg.ref_origin_lon, 0.63, now_ms=11000)
    with get_db(db_file) as con:
        ingest_raw_packet(con, pkt2, cfg, now_ms=11000)
    res = service.process_fusion_batch(now_ms=11000)

    assert res["provisional_promoted"] == 0
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT lifecycle_state, confirmation_method FROM global_objects")
        row = cur.fetchone()
        assert row["lifecycle_state"] == LifecycleState.PROVISIONAL.value
        assert row["confirmation_method"] is None
