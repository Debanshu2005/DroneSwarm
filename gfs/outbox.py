"""
Durable Outbox Delivery, Worker Leasing (lease_token), and HMAC-Authenticated ACK Protocol for GFS.
"""

from __future__ import annotations
import hmac
import hashlib
import json
import time
import uuid
import sqlite3
from typing import List, Dict, Tuple, Optional, Any

from gfs.models import OutboxEvent, GlobalObject
from gfs.config import GFSConfig


def enqueue_global_object_event(
    con: sqlite3.Connection,
    glob: GlobalObject,
    event_type: str = "GLOBAL_OBJECT_UPDATE",
    now_ms: Optional[int] = None
) -> str:
    """
    Enqueues a global object lifecycle event into the durable outbox.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    event_id = f"evt-{uuid.uuid4().hex}"
    payload_dict = {
        "event_id": event_id,
        "event_type": event_type,
        "global_id": glob.global_id,
        "label": glob.label,
        "status": "REACTIVATED" if event_type == "GLOBAL_OBJECT_REACTIVATED" else glob.status,
        "lat": glob.lat,
        "lon": glob.lon,
        "alt": glob.alt,
        "confidence": glob.confidence,
        "observation_count": glob.observation_count,
        "source_count": glob.source_count,
        "source_drones": list(glob.source_estimates.keys()),
        "first_seen_ms": glob.first_seen_ms,
        "last_seen_ms": glob.last_seen_ms,
        "version": glob.version,
        "fusion_method": getattr(glob, "fusion_method", "single_estimate"),
        "emitted_at_ms": now_ms
    }

    cur = con.cursor()
    cur.execute(
        """INSERT INTO gfs_outbox_events (id, payload, status)
           VALUES (?, ?, 'PENDING')""",
        (event_id, json.dumps(payload_dict, sort_keys=True))
    )
    return event_id


def lease_outbox_events(
    con: sqlite3.Connection,
    worker_id: str,
    lease_duration_ms: int,
    config: GFSConfig,
    now_ms: Optional[int] = None,
    limit: int = 20
) -> Tuple[str, List[OutboxEvent]]:
    """
    Acquires leases on pending or timed-out outbox events using a unique lease_token.
    Reclaims expired leases and marks max-retry events as FAILED.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    lease_token = f"{worker_id}:{uuid.uuid4().hex}"
    expires_at = now_ms + lease_duration_ms
    cur = con.cursor()

    # 1. Fail events that exceeded max retries
    cur.execute(
        """UPDATE gfs_outbox_events 
           SET status = 'FAILED'
           WHERE status IN ('SENT', 'LEASED') 
             AND delivery_attempt_count >= ?""",
        (config.outbox_max_retries,)
    )

    # 2. Reclaim expired leased events back to PENDING
    cur.execute(
        """UPDATE gfs_outbox_events 
           SET status = 'PENDING', lease_token = NULL, lease_expires_at_ms = NULL
           WHERE status = 'LEASED' AND lease_expires_at_ms < ?""",
        (now_ms,)
    )

    # 3. Reclaim timed-out SENT events (no ACK received within ack_timeout_ms)
    cur.execute(
        """UPDATE gfs_outbox_events
           SET status = 'PENDING', lease_token = NULL, lease_expires_at_ms = NULL
           WHERE status = 'SENT' AND ack_timeout_ms < ? AND delivery_attempt_count < ?""",
        (now_ms, config.outbox_max_retries)
    )

    # 4. Lease available PENDING events
    cur.execute(
        """UPDATE gfs_outbox_events
           SET status = 'LEASED', lease_token = ?, lease_expires_at_ms = ?
           WHERE id IN (
               SELECT id FROM gfs_outbox_events
               WHERE status = 'PENDING'
               ORDER BY id ASC
               LIMIT ?
           )""",
        (lease_token, expires_at, limit)
    )

    # Fetch leased events
    cur.execute(
        """SELECT id, payload, status, lease_token, lease_expires_at_ms,
                  delivery_attempt_id, delivery_attempt_count, sent_at_ms,
                  ack_timeout_ms, ack_client_id, ack_id, acked_at_ms
           FROM gfs_outbox_events
           WHERE lease_token = ?""",
        (lease_token,)
    )
    rows = cur.fetchall()

    events: List[OutboxEvent] = []
    for r in rows:
        events.append(OutboxEvent(
            id=r["id"],
            payload=r["payload"],
            status=r["status"],
            lease_token=r["lease_token"],
            lease_expires_at_ms=r["lease_expires_at_ms"],
            delivery_attempt_id=r["delivery_attempt_id"],
            delivery_attempt_count=r["delivery_attempt_count"],
            sent_at_ms=r["sent_at_ms"],
            ack_timeout_ms=r["ack_timeout_ms"],
            ack_client_id=r["ack_client_id"],
            ack_id=r["ack_id"],
            acked_at_ms=r["acked_at_ms"]
        ))

    return lease_token, events


def record_successful_transmission(
    con: sqlite3.Connection,
    event_id: str,
    lease_token: str,
    ack_timeout_ms: int,
    now_ms: Optional[int] = None
) -> str:
    """
    Transitions an outbox event from LEASED to SENT after transmission,
    generating a unique delivery_attempt_id.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    delivery_attempt_id = f"att-{uuid.uuid4().hex}"
    timeout_at = now_ms + ack_timeout_ms

    cur = con.cursor()
    cur.execute(
        """UPDATE gfs_outbox_events
           SET status = 'SENT',
               lease_token = NULL,
               lease_expires_at_ms = NULL,
               delivery_attempt_id = ?,
               delivery_attempt_count = delivery_attempt_count + 1,
               sent_at_ms = ?,
               ack_timeout_ms = ?
           WHERE id = ? AND status = 'LEASED' AND lease_token = ?""",
        (delivery_attempt_id, now_ms, timeout_at, event_id, lease_token)
    )

    if cur.rowcount == 0:
        raise ValueError(f"Could not transition event {event_id} to SENT: lease_token mismatch or invalid state")

    return delivery_attempt_id


def compute_ack_hmac(
    event_id: str,
    delivery_attempt_id: str,
    ack_id: str,
    gcs_client_id: str,
    timestamp_ms: int,
    kid: str,
    secret_key: bytes
) -> str:
    """
    Computes canonical HMAC-SHA256 signature for GCS delivery acknowledgment.
    Covers all security-relevant fields: event_id, delivery_attempt_id, ack_id,
    gcs_client_id, timestamp_ms, and key identifier (kid).
    """
    canonical_ack = f"{event_id}|{delivery_attempt_id}|{ack_id}|{gcs_client_id}|{timestamp_ms}|{kid}"
    return hmac.new(secret_key, canonical_ack.encode('utf-8'), hashlib.sha256).hexdigest()


def verify_ack_hmac(
    event_id: str,
    delivery_attempt_id: str,
    gcs_client_id: str,
    timestamp_ms: int,
    ack_id: str,
    kid: str,
    signature: str,
    secret_key: bytes
) -> bool:
    """
    Verifies HMAC-SHA256 signature for incoming GCS ACK.
    """
    expected_sig = compute_ack_hmac(
        event_id=event_id,
        delivery_attempt_id=delivery_attempt_id,
        ack_id=ack_id,
        gcs_client_id=gcs_client_id,
        timestamp_ms=timestamp_ms,
        kid=kid,
        secret_key=secret_key
    )
    return hmac.compare_digest(signature, expected_sig)


def process_gcs_ack(
    con: sqlite3.Connection,
    ack_dict: Dict[str, Any],
    secret_key_or_config: Any,
    now_ms: Optional[int] = None
) -> Tuple[bool, str]:
    """
    Processes an authenticated GCS ACK packet against the outbox ledger.
    Enforces delivery attempt binding, client authorization, HMAC security,
    and deduplication via the consumed ACK ledger.

    Returns:
        (is_accepted, reason)
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    event_id = ack_dict.get("event_id")
    delivery_attempt_id = ack_dict.get("delivery_attempt_id")
    gcs_client_id = ack_dict.get("gcs_client_id")
    ack_id = ack_dict.get("ack_id")
    timestamp_ms = ack_dict.get("timestamp_ms") or ack_dict.get("ack_timestamp_ms")
    kid = ack_dict.get("kid", "gcs-key-1")
    signature = ack_dict.get("signature")

    if not all([event_id, delivery_attempt_id, gcs_client_id, ack_id, timestamp_ms, signature]):
        return False, "MISSING_REQUIRED_ACK_FIELDS"

    # Resolve config, authorized clients, and key material
    if isinstance(secret_key_or_config, GFSConfig):
        cfg = secret_key_or_config
        authorized_clients = set(cfg.authorized_gcs_clients)
        raw_key = cfg.hmac_keys.get(kid, cfg.hmac_secret_key)
        secret_key = raw_key.encode('utf-8') if isinstance(raw_key, str) else raw_key
    elif isinstance(secret_key_or_config, (bytes, bytearray)):
        authorized_clients = {"gcs-primary", "gcs-backup", "gcs-pilot-1", "gcs-client-1"}
        secret_key = bytes(secret_key_or_config)
    elif isinstance(secret_key_or_config, str):
        authorized_clients = {"gcs-primary", "gcs-backup", "gcs-pilot-1", "gcs-client-1"}
        secret_key = secret_key_or_config.encode('utf-8')
    else:
        return False, "INVALID_SERVER_CONFIG"

    # 1. Authorize GCS client
    if gcs_client_id not in authorized_clients:
        return False, f"UNAUTHORIZED_GCS_CLIENT: {gcs_client_id}"

    # 2. Verify HMAC
    if not verify_ack_hmac(
        event_id=event_id,
        delivery_attempt_id=delivery_attempt_id,
        gcs_client_id=gcs_client_id,
        timestamp_ms=int(timestamp_ms),
        ack_id=ack_id,
        kid=kid,
        signature=signature,
        secret_key=secret_key
    ):
        return False, "INVALID_HMAC_SIGNATURE"

    cur = con.cursor()

    # 3. Check for already consumed ACK (replay / duplicate protection)
    cur.execute(
        "SELECT 1 FROM gfs_consumed_acks WHERE ack_id = ?",
        (ack_id,)
    )
    if cur.fetchone() is not None:
        return False, f"ACK_ALREADY_CONSUMED: {ack_id}"

    # 4. Check event existence and state
    cur.execute(
        "SELECT status, delivery_attempt_id FROM gfs_outbox_events WHERE id = ?",
        (event_id,)
    )
    row = cur.fetchone()

    if row is None:
        return False, "EVENT_NOT_FOUND"

    curr_status = row["status"]
    curr_attempt = row["delivery_attempt_id"]

    if curr_status != "SENT":
        # Event is not awaiting acknowledgement (e.g. already ACKED, LEASED, PENDING, FAILED)
        return False, f"EVENT_NOT_AWAITING_ACK: current status is {curr_status}"

    if curr_attempt != delivery_attempt_id:
        # Delayed ACK from a previous attempt cannot acknowledge retry attempt
        return False, f"STALE_DELIVERY_ATTEMPT: expected {curr_attempt}, got {delivery_attempt_id}"

    # 5. Atomically commit consumed ACK and update event to ACKED
    cur.execute(
        """INSERT INTO gfs_consumed_acks (ack_id, event_id, delivery_attempt_id, gcs_client_id, kid, consumed_at_ms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ack_id, event_id, delivery_attempt_id, gcs_client_id, kid, now_ms)
    )

    cur.execute(
        """UPDATE gfs_outbox_events
           SET status = 'ACKED',
               acked_at_ms = ?,
               ack_client_id = ?,
               ack_id = ?
           WHERE id = ? AND status = 'SENT' AND delivery_attempt_id = ?""",
        (now_ms, gcs_client_id, ack_id, event_id, delivery_attempt_id)
    )

    return True, "ACK_ACCEPTED"

