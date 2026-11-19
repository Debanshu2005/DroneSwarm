"""
Ingestion, session validation, packet-item ledger, and OCC claim management for GFS.
"""

from __future__ import annotations
import json
import time
import uuid
import sqlite3
from typing import List, Dict, Tuple, Optional, Any

from gfs.models import Observation
from gfs.canonical import construct_canonical_obs_id, compute_cnor_v1_hash
from gfs.config import GFSConfig


class IngestionError(Exception):
    pass


class StaleSessionError(IngestionError):
    pass


class SessionConflictError(IngestionError):
    pass


def validate_and_register_session(
    con: sqlite3.Connection,
    drone_id: str,
    session_id: str,
    generation: int,
    now_ms: int
) -> None:
    """
    Validates the drone's session and generation against the drone_sessions ledger.
    Enforces generational monotonicity and UUID consistency per drone.
    """
    cur = con.cursor()
    cur.execute(
        "SELECT active_session_id, session_generation FROM drone_sessions WHERE drone_id = ?",
        (drone_id,)
    )
    row = cur.fetchone()

    if row is None:
        # First session for this drone
        cur.execute(
            """INSERT INTO drone_sessions 
               (drone_id, active_session_id, session_generation, registered_at_ms, last_seen_ms)
               VALUES (?, ?, ?, ?, ?)""",
            (drone_id, session_id, generation, now_ms, now_ms)
        )
    else:
        active_sess = row["active_session_id"]
        reg_gen = row["session_generation"]

        if generation < reg_gen:
            raise StaleSessionError(
                f"Packet generation {generation} is stale; registered generation is {reg_gen}"
            )
        elif generation == reg_gen:
            if session_id.lower() != active_sess.lower():
                raise SessionConflictError(
                    f"Conflicting session UUID {session_id} for generation {generation}; expected {active_sess}"
                )
            cur.execute(
                "UPDATE drone_sessions SET last_seen_ms = ? WHERE drone_id = ?",
                (now_ms, drone_id)
            )
        else:
            # New generation
            cur.execute(
                """UPDATE drone_sessions 
                   SET active_session_id = ?, session_generation = ?, last_seen_ms = ?
                   WHERE drone_id = ?""",
                (session_id, generation, now_ms, drone_id)
            )


def ingest_raw_packet(
    con: sqlite3.Connection,
    packet_dict: Dict[str, Any],
    config: GFSConfig,
    now_ms: Optional[int] = None
) -> Tuple[str, int, int]:
    """
    Ingests an incoming raw packet and its observations atomically into the inbox ledger.
    Applies deduplication and CNOR v1 payload collision checking.

    Returns:
        (packet_id, valid_pending_items, rejected_or_duplicate_items)
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    packet_id = packet_dict.get("packet_id") or f"pkt-{uuid.uuid4().hex}"
    drone_id = packet_dict.get("drone_id") or packet_dict.get("sender_id")
    drone_session_id = packet_dict.get("drone_session_id")
    session_generation = packet_dict.get("session_generation", 1)
    raw_items = packet_dict.get("observations")
    if raw_items is None:
        raw_items = packet_dict.get("detections", [])

    if not drone_id or not drone_session_id:
        raise IngestionError("Packet must contain drone_id and drone_session_id")

    # Validate / advance session
    validate_and_register_session(con, drone_id, drone_session_id, session_generation, now_ms)

    cur = con.cursor()

    # Check for duplicate packet_id
    cur.execute("SELECT packet_status FROM inbox_packets WHERE packet_id = ?", (packet_id,))
    if cur.fetchone() is not None:
        # Packet already ingested idempotently
        return packet_id, 0, len(raw_items)

    cur.execute(
        """INSERT INTO inbox_packets 
           (packet_id, drone_id, drone_session_id, session_generation, received_at_ms, item_count, packet_status)
           VALUES (?, ?, ?, ?, ?, ?, 'RECEIVED')""",
        (packet_id, drone_id, drone_session_id, session_generation, now_ms, len(raw_items))
    )

    valid_pending = 0
    rejected_count = 0

    for idx, item in enumerate(raw_items):
        item["drone_id"] = drone_id
        item["drone_session_id"] = drone_session_id
        item["session_generation"] = session_generation

        # Schema normalization for direct drone broadcasts vs pipeline observations
        if "local_track_id" not in item and "id" in item:
            item["local_track_id"] = str(item["id"])
        if "frame_id" not in item:
            item["frame_id"] = int(packet_dict.get("frame_id", 1))
        if "capture_timestamp_ms" not in item:
            raw_ts = item.get("timestamp") or packet_dict.get("timestamp", now_ms)
            item["capture_timestamp_ms"] = int(raw_ts * 1000) if (1e8 < raw_ts < 1e11) else int(raw_ts)
        else:
            raw_ts = item["capture_timestamp_ms"]
            if 1e8 < raw_ts < 1e11:
                item["capture_timestamp_ms"] = int(raw_ts * 1000)
        if "cov_xx" not in item:
            item["cov_xx"] = 0.25
        if "cov_yy" not in item:
            item["cov_yy"] = 0.25
        if "cov_xy" not in item:
            item["cov_xy"] = 0.0
        if "alt" not in item:
            item["alt"] = float(item.get("altitude_agl", 0.0) or 0.0)

        try:
            obs_id = construct_canonical_obs_id(
                drone_id=drone_id,
                session_id=drone_session_id,
                generation=session_generation,
                frame_id=int(item["frame_id"]),
                local_track_id=str(item["local_track_id"])
            )
            payload_hash = compute_cnor_v1_hash(item)
            item_raw_json = json.dumps(item, sort_keys=True)
        except Exception as e:
            # Malformed item
            cur.execute(
                """INSERT INTO inbox_packet_items 
                   (packet_id, item_index, obs_id, payload_hash, raw_payload, disposition, rejection_reason, disposition_timestamp_ms)
                   VALUES (?, ?, ?, ?, ?, 'REJECTED', ?, ?)""",
                (packet_id, idx, f"malformed-{idx}", "none", json.dumps(item), str(e), now_ms)
            )
            rejected_count += 1
            continue

        # Check deduplication registry
        cur.execute("SELECT payload_hash FROM observation_registry WHERE obs_id = ?", (obs_id,))
        reg_row = cur.fetchone()

        if reg_row is not None:
            existing_hash = reg_row["payload_hash"]
            if existing_hash == payload_hash:
                # Safe identical duplicate
                cur.execute(
                    """INSERT INTO inbox_packet_items 
                       (packet_id, item_index, obs_id, payload_hash, raw_payload, disposition, rejection_reason, disposition_timestamp_ms)
                       VALUES (?, ?, ?, ?, ?, 'EXPIRED', 'IDEMPOTENT_DUPLICATE', ?)""",
                    (packet_id, idx, obs_id, payload_hash, item_raw_json, now_ms)
                )
                rejected_count += 1
            else:
                # Collision with conflicting payload
                cur.execute(
                    """INSERT INTO observation_rejections
                       (obs_id, incoming_hash, existing_hash, packet_id, rejection_reason, rejected_at_ms)
                       VALUES (?, ?, ?, ?, 'PAYLOAD_HASH_MISMATCH_COLLISION', ?)""",
                    (obs_id, payload_hash, existing_hash, packet_id, now_ms)
                )
                cur.execute(
                    """INSERT INTO inbox_packet_items 
                       (packet_id, item_index, obs_id, payload_hash, raw_payload, disposition, rejection_reason, disposition_timestamp_ms)
                       VALUES (?, ?, ?, ?, ?, 'REJECTED', 'PAYLOAD_HASH_MISMATCH_COLLISION', ?)""",
                    (packet_id, idx, obs_id, payload_hash, item_raw_json, now_ms)
                )
                rejected_count += 1
        else:
            # Fresh observation
            cur.execute(
                """INSERT INTO observation_registry (obs_id, payload_hash, packet_id, created_at_ms)
                   VALUES (?, ?, ?, ?)""",
                (obs_id, payload_hash, packet_id, now_ms)
            )
            cur.execute(
                """INSERT INTO inbox_packet_items 
                   (packet_id, item_index, obs_id, payload_hash, raw_payload, disposition, disposition_timestamp_ms)
                   VALUES (?, ?, ?, ?, ?, 'PENDING', ?)""",
                (packet_id, idx, obs_id, payload_hash, item_raw_json, now_ms)
            )
            valid_pending += 1

    return packet_id, valid_pending, rejected_count


def claim_pending_packets(
    con: sqlite3.Connection,
    worker_id: str,
    lease_duration_ms: int,
    now_ms: Optional[int] = None,
    limit: int = 10
) -> Tuple[str, List[Observation]]:
    """
    Claims pending inbox packets under OCC and returns parsed Observation objects.
    Reclaims any packets whose lease has expired.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    claim_token = f"{worker_id}:{uuid.uuid4().hex}"
    expires_at = now_ms + lease_duration_ms
    cur = con.cursor()

    # Reclaim expired processing packets
    cur.execute(
        """UPDATE inbox_packets 
           SET packet_status = 'RECEIVED', claim_token = NULL, claim_expires_at_ms = NULL
           WHERE packet_status = 'PROCESSING' AND claim_expires_at_ms < ?""",
        (now_ms,)
    )

    # Claim available packets
    cur.execute(
        """UPDATE inbox_packets
           SET packet_status = 'PROCESSING', claim_token = ?, claim_expires_at_ms = ?
           WHERE packet_id IN (
               SELECT packet_id FROM inbox_packets
               WHERE packet_status = 'RECEIVED'
               ORDER BY received_at_ms ASC
               LIMIT ?
           )""",
        (claim_token, expires_at, limit)
    )

    # Fetch pending items for claimed packets
    cur.execute(
        """SELECT i.packet_id, i.item_index, i.obs_id, i.payload_hash, i.raw_payload
           FROM inbox_packet_items i
           JOIN inbox_packets p ON i.packet_id = p.packet_id
           WHERE p.claim_token = ? AND i.disposition = 'PENDING'
           ORDER BY p.received_at_ms ASC, i.item_index ASC""",
        (claim_token,)
    )
    rows = cur.fetchall()

    observations: List[Observation] = []
    for r in rows:
        data = json.loads(r["raw_payload"])
        obs = Observation(
            obs_id=r["obs_id"],
            drone_id=data["drone_id"],
            drone_session_id=data["drone_session_id"],
            session_generation=int(data["session_generation"]),
            frame_id=int(data["frame_id"]),
            local_track_id=str(data["local_track_id"]),
            capture_timestamp_ms=int(data["capture_timestamp_ms"]),
            lat=float(data["lat"]),
            lon=float(data["lon"]),
            alt=float(data["alt"]),
            cov_xx=float(data["cov_xx"]),
            cov_yy=float(data["cov_yy"]),
            cov_xy=float(data["cov_xy"]),
            label=str(data["label"]),
            confidence=float(data["confidence"]),
            payload_hash=r["payload_hash"],
            raw_payload=r["raw_payload"]
        )
        observations.append(obs)

    return claim_token, observations


def commit_packet_dispositions(
    con: sqlite3.Connection,
    claim_token: str,
    item_dispositions: Dict[str, Tuple[str, Optional[str], Optional[str]]], # obs_id -> (disposition, reason, target_global_id)
    now_ms: Optional[int] = None,
    deferred_ttl_ms: int = 60000
) -> None:
    """
    Atomically updates item dispositions, commits deferred records, and marks packets COMPLETED.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    cur = con.cursor()

    for obs_id, (disp, reason, target_id) in item_dispositions.items():
        cur.execute(
            """UPDATE inbox_packet_items
               SET disposition = ?, rejection_reason = ?, target_global_id = ?, disposition_timestamp_ms = ?
               WHERE obs_id = ? AND packet_id IN (
                   SELECT packet_id FROM inbox_packets WHERE claim_token = ?
               )""",
            (disp, reason, target_id, now_ms, obs_id, claim_token)
        )

        if disp == "DEFERRED":
            # Retrieve raw payload to persist into deferred_observations
            cur.execute(
                "SELECT packet_id, raw_payload FROM inbox_packet_items WHERE obs_id = ?",
                (obs_id,)
            )
            item_row = cur.fetchone()
            if item_row:
                cur.execute(
                    """INSERT OR REPLACE INTO deferred_observations
                       (obs_id, packet_id, raw_payload, defer_reason, created_at_ms, expires_at_ms)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (obs_id, item_row["packet_id"], item_row["raw_payload"], reason or "AMBIGUOUS_CLIQUE", now_ms, now_ms + deferred_ttl_ms)
                )

    # Mark claimed packets as COMPLETED
    cur.execute(
        """UPDATE inbox_packets
           SET packet_status = 'COMPLETED', claim_token = NULL, claim_expires_at_ms = NULL
           WHERE claim_token = ?""",
        (claim_token,)
    )

