"""
Verification Suite for PersistentSessionAllocator & GFS Session Lifecycle.

Classification: Category A — Synthetic Unit & Concurrency Verification.

Verifies:
1. Generation Monotonicity: Allocations strictly increment (G_{k+1} = G_k + 1).
2. Incarnation UUID Binding: Unique process incarnation UUID per allocation.
3. Crash & Restart Persistence: New allocator instances on disk strictly resume monotonically.
4. Multi-Drone Isolation: Independent generational counters per drone identity.
5. Concurrent Allocation Serialization: Thread/process contention handled cleanly via SQLite locks.
6. Corrupted State Detection & Zero Silent Rollback: Corrupt state raises CorruptedSessionStateError.
7. Active Session Inspection: Read-only query does not mutate state.
8. Authoritative GFS Inbox Integration: StaleSessionError and SessionConflictError enforcement.
9. Relay Snapshot Request Handling (Contract B): Relay serves active GFS objects over WebSocket.
"""

import os
import sqlite3
import pytest
import concurrent.futures
from pathlib import Path

from gfs.session_allocator import (
    PersistentSessionAllocator,
    SessionAllocationError,
    CorruptedSessionStateError,
    ConcurrentAllocationTimeoutError,
)
from gfs.inbox import ingest_raw_packet, StaleSessionError, SessionConflictError
from gfs.config import GFSConfig
from gfs.database import init_db, get_db
from relay.relay import UdpWebsocketRelay, init_relay_ack_db


def test_initial_allocation_monotonic(tmp_path):
    db_file = str(tmp_path / "session_state.db")
    allocator = PersistentSessionAllocator(state_db_path=db_file)

    sess1, gen1 = allocator.allocate_session("DroneOS")
    assert gen1 == 1
    assert sess1.startswith("sess-DroneOS-")

    sess2, gen2 = allocator.allocate_session("DroneOS")
    assert gen2 == 2
    assert sess2.startswith("sess-DroneOS-")
    assert sess1 != sess2

    sess3, gen3 = allocator.allocate_session("DroneOS")
    assert gen3 == 3
    assert sess3 != sess2


def test_persistence_across_process_restart(tmp_path):
    db_file = str(tmp_path / "session_state.db")

    # Incarnation 1
    alloc1 = PersistentSessionAllocator(state_db_path=db_file)
    s1, g1 = alloc1.allocate_session("DroneOS")
    assert g1 == 1

    # Simulate process death: alloc1 destroyed, new process boots
    del alloc1

    # Incarnation 2
    alloc2 = PersistentSessionAllocator(state_db_path=db_file)
    s2, g2 = alloc2.allocate_session("DroneOS")
    assert g2 == 2
    assert s2 != s1

    # Simulate another reboot
    del alloc2
    alloc3 = PersistentSessionAllocator(state_db_path=db_file)
    s3, g3 = alloc3.allocate_session("DroneOS")
    assert g3 == 3
    assert s3 != s2


def test_multi_drone_isolation(tmp_path):
    db_file = str(tmp_path / "session_state.db")
    alloc = PersistentSessionAllocator(state_db_path=db_file)

    # Drone 0
    _, d0_g1 = alloc.allocate_session("DroneOS")
    assert d0_g1 == 1

    # Drone 1
    _, d1_g1 = alloc.allocate_session("DroneOS1")
    assert d1_g1 == 1

    # Drone 2
    _, d2_g1 = alloc.allocate_session("DroneOS2")
    assert d2_g1 == 1

    # Advance Drone 0 again
    _, d0_g2 = alloc.allocate_session("DroneOS")
    assert d0_g2 == 2

    # Drone 1 should still be at 1, so next is 2
    _, d1_g2 = alloc.allocate_session("DroneOS1")
    assert d1_g2 == 2


def test_get_active_session_read_only(tmp_path):
    db_file = str(tmp_path / "session_state.db")
    alloc = PersistentSessionAllocator(state_db_path=db_file)

    # Empty initially
    assert alloc.get_active_session("DroneOS") is None

    # Allocate
    s1, g1 = alloc.allocate_session("DroneOS")

    # Read back without mutating
    active = alloc.get_active_session("DroneOS")
    assert active is not None
    assert active[0] == s1
    assert active[1] == g1

    # Read again
    active2 = alloc.get_active_session("DroneOS")
    assert active2 == active


def test_concurrent_allocation_protection(tmp_path):
    db_file = str(tmp_path / "session_state.db")
    alloc = PersistentSessionAllocator(state_db_path=db_file, busy_timeout_ms=15000)

    # Concurrently allocate from 10 workers for the same drone
    def worker(_):
        worker_alloc = PersistentSessionAllocator(state_db_path=db_file, busy_timeout_ms=15000)
        return worker_alloc.allocate_session("DroneOS")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(worker, range(10)))

    generations = [res[1] for res in results]
    session_ids = [res[0] for res in results]

    # All generations must be unique and span 1..10
    assert len(set(generations)) == 10
    assert set(generations) == set(range(1, 11))
    # All session UUIDs must be distinct
    assert len(set(session_ids)) == 10


def test_corrupted_state_rejection_no_silent_rollback(tmp_path):
    db_file = str(tmp_path / "session_state.db")
    alloc = PersistentSessionAllocator(state_db_path=db_file)
    alloc.allocate_session("DroneOS")

    # Inject corrupt non-integer generation into the DB
    con = sqlite3.connect(db_file)
    con.execute("UPDATE node_session_state SET last_generation = -99 WHERE drone_id = 'DroneOS'")
    con.commit()
    con.close()

    # Must raise CorruptedSessionStateError, NOT silently roll back to 1
    with pytest.raises(CorruptedSessionStateError, match="Non-monotonic or invalid generation"):
        alloc.allocate_session("DroneOS")


def test_corrupted_file_bytes_rejection(tmp_path):
    db_file = str(tmp_path / "corrupt_session.db")
    Path(db_file).write_bytes(b"CORRUPTED_GARBAGE_BYTES_NOT_A_SQLITE_FILE")

    with pytest.raises(CorruptedSessionStateError):
        PersistentSessionAllocator(state_db_path=db_file)


def test_gfs_inbox_session_generation_validation(tmp_path):
    db_file = str(tmp_path / "gfs_test.db")
    init_db(db_file)
    cfg = GFSConfig(db_path=db_file)

    now_ms = 100000

    # Gen 1 packet
    pkt_g1 = {
        "packet_id": "pkt-d1-g1",
        "drone_id": "DroneOS",
        "drone_session_id": "sess-DroneOS-uuid-1",
        "session_generation": 1,
        "observations": []
    }
    with get_db(cfg.db_path) as con:
        ingest_raw_packet(con, pkt_g1, cfg, now_ms=now_ms)

    # Drone restarts into Gen 2
    pkt_g2 = {
        "packet_id": "pkt-d1-g2",
        "drone_id": "DroneOS",
        "drone_session_id": "sess-DroneOS-uuid-2",
        "session_generation": 2,
        "observations": []
    }
    with get_db(cfg.db_path) as con:
        ingest_raw_packet(con, pkt_g2, cfg, now_ms=now_ms + 1000)

    # Delayed Gen 1 arrives -> MUST be rejected as StaleSessionError
    pkt_delayed_g1 = {
        "packet_id": "pkt-d1-delayed-g1",
        "drone_id": "DroneOS",
        "drone_session_id": "sess-DroneOS-uuid-1",
        "session_generation": 1,
        "observations": []
    }
    with get_db(cfg.db_path) as con:
        with pytest.raises(StaleSessionError, match="stale"):
            ingest_raw_packet(con, pkt_delayed_g1, cfg, now_ms=now_ms + 1100)

    # Conflicting session UUID for Gen 2 -> MUST be rejected as SessionConflictError
    pkt_conflict_g2 = {
        "packet_id": "pkt-d1-conflict-g2",
        "drone_id": "DroneOS",
        "drone_session_id": "sess-DroneOS-ROGUE-UUID",
        "session_generation": 2,
        "observations": []
    }
    with get_db(cfg.db_path) as con:
        with pytest.raises(SessionConflictError, match="Conflicting session UUID"):
            ingest_raw_packet(con, pkt_conflict_g2, cfg, now_ms=now_ms + 1200)


@pytest.mark.asyncio
async def test_relay_snapshot_request_contract_b(tmp_path):
    gfs_db_file = str(tmp_path / "gfs_snap.db")
    relay_db_file = str(tmp_path / "relay_snap.db")

    init_db(gfs_db_file)
    init_relay_ack_db(relay_db_file)

    # Populate GFS database with 1 ACTIVE and 1 RETIRED object
    con = sqlite3.connect(gfs_db_file)
    with con:
        con.execute("""
            INSERT INTO global_objects (
                global_id, label, status, lat, lon, alt, east_m, north_m,
                cov_xx, cov_yy, cov_xy, confidence, first_seen_ms, last_seen_ms,
                observation_count, source_count, source_estimates_json, version
            ) VALUES (
                'G-ACTIVE-1', 'rubbish', 'ACTIVE', 22.314, 87.310, 10.0, 0.0, 0.0,
                0.2, 0.2, 0.0, 0.92, 1000, 2000, 5, 2, '{}', 3
            );
        """)
        con.execute("""
            INSERT INTO global_objects (
                global_id, label, status, lat, lon, alt, east_m, north_m,
                cov_xx, cov_yy, cov_xy, confidence, first_seen_ms, last_seen_ms,
                observation_count, source_count, source_estimates_json, version
            ) VALUES (
                'G-RETIRED-1', 'rubbish', 'RETIRED', 22.315, 87.311, 10.0, 0.0, 0.0,
                0.2, 0.2, 0.0, 0.85, 1000, 3000, 4, 1, '{}', 4
            );
        """)
    con.close()

    relay = UdpWebsocketRelay(
        ws_port=8099,
        udp_bind_port=14598,
        relay_ack_db=relay_db_file,
        gfs_db_path=gfs_db_file
    )

    class MockWebSocket:
        def __init__(self):
            self.sent = []
        async def send(self, data):
            self.sent.append(data)

    mock_ws = MockWebSocket()

    # GCS sends snapshot request
    req = {"type": "gfs_snapshot_request"}
    await relay._handle_gfs_snapshot_request(mock_ws, req)

    assert len(mock_ws.sent) == 1
    import json
    resp = json.loads(mock_ws.sent[0])
    assert resp["type"] == "gfs_snapshot_response"
    assert "objects" in resp
    assert "tombstones" in resp
    assert "snapshot_revision" in resp
    assert "watermark_event_id" in resp

    objects = resp["objects"]
    tombstones = resp["tombstones"]

    # Exactly the 1 ACTIVE object in objects
    assert len(objects) == 1
    assert objects[0]["id"] == "G-ACTIVE-1"
    assert objects[0]["version"] == 3
    assert objects[0]["confidence"] == 0.92

    # Exactly the 1 RETIRED object in tombstones
    assert "G-RETIRED-1" in tombstones
    assert tombstones["G-RETIRED-1"]["version"] == 4
    assert resp["snapshot_revision"] == 4


def test_four_drone_independent_session_allocators(tmp_path):
    """
    Verifies that all 4 drones ('DroneOS', 'DroneOS1', 'DroneOS2', 'DroneOS3')
    maintain independent persistent session namespaces on disk.
    Simulates startup -> generation 1, restart -> generation 2 with unique UUIDs.
    """
    drone_names = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]
    allocators = {}
    sessions_boot1 = {}
    generations_boot1 = {}

    session_dir = tmp_path / "drone_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Initial Boot for all 4 drones
    for d_name in drone_names:
        db_path = str(session_dir / f"{d_name}.db")
        alloc = PersistentSessionAllocator(state_db_path=db_path)
        s_id, gen = alloc.allocate_session(d_name)
        assert gen == 1, f"{d_name} initial generation must be 1"
        assert s_id.startswith(f"sess-{d_name}-")
        sessions_boot1[d_name] = s_id
        generations_boot1[d_name] = gen

    # All session UUIDs must be distinct
    assert len(set(sessions_boot1.values())) == 4

    # Simulate Process Restart for all 4 drones
    sessions_boot2 = {}
    for d_name in drone_names:
        db_path = str(session_dir / f"{d_name}.db")
        alloc_restarted = PersistentSessionAllocator(state_db_path=db_path)
        s_id, gen = alloc_restarted.allocate_session(d_name)
        assert gen == 2, f"{d_name} post-restart generation must be strictly 2"
        assert s_id.startswith(f"sess-{d_name}-")
        assert s_id != sessions_boot1[d_name], f"{d_name} must allocate fresh UUID on restart"
        sessions_boot2[d_name] = s_id

    # Verify all boot 2 UUIDs are distinct and no collisions with boot 1
    all_uuids = set(sessions_boot1.values()) | set(sessions_boot2.values())
    assert len(all_uuids) == 8


def test_package_parity_session_allocator():
    """
    Verifies package parity across DroneOS, DroneOS1, DroneOS2, DroneOS3:
    1. PersistentSessionAllocator is importable from shared.session_allocator.
    2. DetectionMessage includes drone_session_id and session_generation fields.
    3. PerceptionPipeline accepts session_id and session_generation in constructor.
    """
    packages = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]
    for pkg in packages:
        # 1. Allocator import
        mod = __import__(f"{pkg}.shared.session_allocator", fromlist=["PersistentSessionAllocator"])
        assert hasattr(mod, "PersistentSessionAllocator")

        # 2. Protocol message fields
        msg_mod = __import__(f"{pkg}.shared.protocol.messages", fromlist=["DetectionMessage", "DetectionItem"])
        det_msg = msg_mod.DetectionMessage(
            sender_id="test-drone",
            timestamp=123456.78,
            drone_session_id="sess-test-123",
            session_generation=3
        )
        assert det_msg.drone_session_id == "sess-test-123"
        assert det_msg.session_generation == 3

        # 3. PerceptionPipeline signature
        pipe_mod = __import__(f"{pkg}.perception.pipeline", fromlist=["PerceptionPipeline"])
        import inspect
        sig = inspect.signature(pipe_mod.PerceptionPipeline.__init__)
        assert "session_id" in sig.parameters
        assert "session_generation" in sig.parameters

