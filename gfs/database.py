"""
SQLite database engine, connection pooling, and schema migrations for the Global Fusion Service (GFS).
"""

import sqlite3
from pathlib import Path
from typing import Generator
from contextlib import contextmanager


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- 1. Drone Sessions Ledger
CREATE TABLE IF NOT EXISTS drone_sessions (
    drone_id TEXT PRIMARY KEY,
    active_session_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL,
    registered_at_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);

-- 2. Global Objects
CREATE TABLE IF NOT EXISTS global_objects (
    global_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'RETIRED', 'ARCHIVED')),
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    alt REAL NOT NULL,
    east_m REAL NOT NULL,
    north_m REAL NOT NULL,
    cov_xx REAL NOT NULL,
    cov_yy REAL NOT NULL,
    cov_xy REAL NOT NULL,
    confidence REAL NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 1,
    source_count INTEGER NOT NULL DEFAULT 1,
    source_estimates_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    lifecycle_state TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK(lifecycle_state IN ('PROVISIONAL', 'CONFIRMED', 'REJECTED', 'RETIRED')),
    confirmation_method TEXT,
    provisional_reason TEXT,
    expires_at_ms INTEGER
);

-- 3. Permanent Identity History (Tombstones - ON DELETE RESTRICT)
CREATE TABLE IF NOT EXISTS global_track_identity_history (
    drone_id TEXT NOT NULL,
    drone_session_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL,
    local_track_id TEXT NOT NULL,
    global_id TEXT NOT NULL,
    first_frame_id INTEGER NOT NULL,
    last_frame_id INTEGER NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL,
    PRIMARY KEY (drone_id, drone_session_id, session_generation, local_track_id),
    FOREIGN KEY (global_id) REFERENCES global_objects(global_id) ON DELETE RESTRICT
);

-- 4. Fast Observation Deduplication Registry
CREATE TABLE IF NOT EXISTS observation_registry (
    obs_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

-- 5. Observation Collision and Rejection Audit Log
CREATE TABLE IF NOT EXISTS observation_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_id TEXT NOT NULL,
    incoming_hash TEXT NOT NULL,
    existing_hash TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    rejected_at_ms INTEGER NOT NULL
);

-- 6. Ingestion Inbox Packet Ledger
CREATE TABLE IF NOT EXISTS inbox_packets (
    packet_id TEXT PRIMARY KEY,
    drone_id TEXT NOT NULL,
    drone_session_id TEXT NOT NULL,
    session_generation INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    packet_status TEXT NOT NULL CHECK(packet_status IN ('RECEIVED', 'PROCESSING', 'COMPLETED', 'FAILED')),
    claim_token TEXT,
    claim_expires_at_ms INTEGER
);

CREATE TABLE IF NOT EXISTS inbox_packet_items (
    packet_id TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    obs_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('PENDING', 'FUSED', 'DEFERRED', 'REJECTED', 'EXPIRED')),
    rejection_reason TEXT,
    target_global_id TEXT,
    disposition_timestamp_ms INTEGER,
    PRIMARY KEY (packet_id, item_index),
    FOREIGN KEY (packet_id) REFERENCES inbox_packets(packet_id) ON DELETE CASCADE
);

-- 7. Deferred Observations Ledger
CREATE TABLE IF NOT EXISTS deferred_observations (
    obs_id TEXT PRIMARY KEY,
    packet_id TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    defer_reason TEXT NOT NULL CHECK(defer_reason IN ('FUTURE_TIMESTAMP', 'AMBIGUOUS_CLIQUE', 'LOW_CONFIDENCE', 'CLIQUE_TIMEOUT_DEFERRED', 'CLIQUE_COUNT_EXHAUSTED', 'HISTORICAL_TRACK_DEFERRED_RETRY', 'VERTEX_LIMIT_DEFERRED')),
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL
);

-- 8. Durable Outbox Events
CREATE TABLE IF NOT EXISTS gfs_outbox_events (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'LEASED', 'SENT', 'FAILED', 'ACKED')),
    lease_token TEXT,
    lease_expires_at_ms INTEGER,
    delivery_attempt_id TEXT,
    delivery_attempt_count INTEGER NOT NULL DEFAULT 0,
    sent_at_ms INTEGER,
    ack_timeout_ms INTEGER,
    ack_client_id TEXT,
    ack_id TEXT,
    acked_at_ms INTEGER
);

-- 9. Durable Consumed ACK Ledger (Deduplication and Replay Protection)
CREATE TABLE IF NOT EXISTS gfs_consumed_acks (
    ack_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    delivery_attempt_id TEXT NOT NULL,
    gcs_client_id TEXT NOT NULL,
    kid TEXT NOT NULL,
    consumed_at_ms INTEGER NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_inbox_packets_status ON inbox_packets(packet_status, received_at_ms);
CREATE INDEX IF NOT EXISTS idx_inbox_items_disp ON inbox_packet_items(disposition);
CREATE INDEX IF NOT EXISTS idx_outbox_status_lease ON gfs_outbox_events(status, lease_expires_at_ms);
CREATE INDEX IF NOT EXISTS idx_consumed_acks_event ON gfs_consumed_acks(event_id, delivery_attempt_id);
CREATE INDEX IF NOT EXISTS idx_global_objects_status ON global_objects(status, label);
CREATE INDEX IF NOT EXISTS idx_global_objects_lifecycle ON global_objects(lifecycle_state, status);
CREATE INDEX IF NOT EXISTS idx_identity_hist_global ON global_track_identity_history(global_id);
"""


def migrate_schema(con: sqlite3.Connection) -> None:
    """
    Applies schema migrations to ensure existing databases receive new columns.
    """
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='global_objects'")
    if not cur.fetchone():
        return

    cur.execute("PRAGMA table_info(global_objects)")
    existing_cols = {row[1] for row in cur.fetchall()}

    if "lifecycle_state" not in existing_cols:
        cur.execute("ALTER TABLE global_objects ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'CONFIRMED'")
    if "confirmation_method" not in existing_cols:
        cur.execute("ALTER TABLE global_objects ADD COLUMN confirmation_method TEXT")
    if "provisional_reason" not in existing_cols:
        cur.execute("ALTER TABLE global_objects ADD COLUMN provisional_reason TEXT")
    if "expires_at_ms" not in existing_cols:
        cur.execute("ALTER TABLE global_objects ADD COLUMN expires_at_ms INTEGER")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_global_objects_lifecycle ON global_objects(lifecycle_state, status)")


def create_connection(db_path: str) -> sqlite3.Connection:
    """
    Creates and configures an SQLite connection with WAL, busy_timeout, and foreign keys.
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA busy_timeout = 5000;")
    migrate_schema(con)
    return con


def init_db(db_path: str) -> None:
    """
    Initializes database schema and executes migrations.
    """
    con = create_connection(db_path)
    try:
        con.executescript(SCHEMA_SQL)
        migrate_schema(con)
        con.commit()
    finally:
        con.close()


@contextmanager
def get_db(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager providing a managed database connection.
    Automatically commits on normal exit and rolls back on unhandled exceptions.
    """
    con = create_connection(db_path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

