"""
Global Fusion Service (GFS) Orchestration Daemon.
Implements Two-Phase OCC Batch Pipeline, STAGE A Association, STAGE B Hypothesis Formation,
and Outbox Event Delivery.
"""

from __future__ import annotations
import math
import json
import time
import logging
from typing import List, Dict, Tuple, Optional, Any, Set

from gfs.models import (
    Observation,
    LocalTracklet,
    GlobalObject,
    SourceEstimate,
    LifecycleState,
    wgs84_to_enu
)
from gfs.config import GFSConfig
from gfs.lock import CrossPlatformProcessLock
from gfs.database import get_db, init_db
from gfs.association import (
    build_local_tracklets,
    associate_drone_tracklets_to_globals,
    update_global_object_with_tracklet
)
from gfs.hypothesis import form_new_global_objects
from gfs.inbox import (
    ingest_raw_packet,
    claim_pending_packets,
    commit_packet_dispositions
)
from gfs.outbox import (
    enqueue_global_object_event,
    lease_outbox_events,
    record_successful_transmission,
    process_gcs_ack
)

logger = logging.getLogger("gfs.service")


class GFSService:
    """
    Main Global Fusion Service coordinator.
    """
    def __init__(self, config: Optional[GFSConfig] = None):
        self.config = config or GFSConfig.load()
        self.lock = CrossPlatformProcessLock(self.config.lock_path)
        init_db(self.config.db_path)

    def process_fusion_batch(self, now_ms: Optional[int] = None) -> Dict[str, Any]:
        """
        Executes a single Two-Phase OCC fusion batch over claimed inbox packets
        and active deferred observations.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        with get_db(self.config.db_path) as con:
            cur = con.cursor()

            # 1. Claim pending inbox packets
            worker_id = "gfs-worker-1"
            claim_token, claimed_observations = claim_pending_packets(
                con,
                worker_id=worker_id,
                lease_duration_ms=self.config.worker_lease_duration_ms,
                now_ms=now_ms,
                limit=10
            )

            # 2. Enforce deferred observation TTL policy and fetch eligible deferred observations
            cur.execute(
                """UPDATE inbox_packet_items
                   SET disposition = 'EXPIRED', rejection_reason = 'DEFERRED_OBSERVATION_EXPIRED', disposition_timestamp_ms = ?
                   WHERE obs_id IN (
                       SELECT obs_id FROM deferred_observations WHERE expires_at_ms <= ?
                   )""",
                (now_ms, now_ms)
            )
            cur.execute("DELETE FROM deferred_observations WHERE expires_at_ms <= ?", (now_ms,))

            # 2b. Enforce provisional candidate TTL policy (transition to RETIRED)
            cur.execute(
                """SELECT global_id, version, last_seen_ms FROM global_objects
                   WHERE status = 'ACTIVE' AND lifecycle_state = 'PROVISIONAL' AND expires_at_ms IS NOT NULL AND expires_at_ms <= ?""",
                (now_ms,)
            )
            expired_prov_rows = cur.fetchall()
            for er in expired_prov_rows:
                eg_id = er["global_id"]
                eg_ver = int(er["version"] or 1) + 1
                cur.execute(
                    """UPDATE global_objects
                       SET status = 'RETIRED', lifecycle_state = 'RETIRED', version = ?
                       WHERE global_id = ?""",
                    (eg_ver, eg_id)
                )
                import uuid
                ret_evt_id = f"evt-{uuid.uuid4().hex}"
                ret_payload = {
                    "event_id": ret_evt_id,
                    "event_type": "GLOBAL_OBJECT_RETIRED",
                    "global_id": eg_id,
                    "id": eg_id,
                    "status": "RETIRED",
                    "lifecycle_state": "RETIRED",
                    "version": eg_ver,
                    "last_seen_ms": er["last_seen_ms"] or now_ms,
                    "emitted_at_ms": now_ms
                }
                cur.execute(
                    "INSERT INTO gfs_outbox_events (id, payload, status) VALUES (?, ?, 'PENDING')",
                    (ret_evt_id, json.dumps(ret_payload, sort_keys=True))
                )
                logger.info("Provisional object %s expired (TTL exceeded), transitioned to RETIRED (ver=%d)", eg_id, eg_ver)

            cur.execute(
                """SELECT obs_id, packet_id, raw_payload, defer_reason 
                   FROM deferred_observations 
                   WHERE expires_at_ms > ? 
                   ORDER BY created_at_ms ASC LIMIT 50""",
                (now_ms,)
            )
            deferred_rows = cur.fetchall()
            recovered_deferred_ids: Set[str] = set()

            for r in deferred_rows:
                data = json.loads(r["raw_payload"])
                # Project coordinates to ENU
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
                    payload_hash=r["obs_id"], # canonical ID
                    raw_payload=r["raw_payload"]
                )
                claimed_observations.append(obs)
                recovered_deferred_ids.add(r["obs_id"])

            if not claimed_observations:
                if expired_prov_rows:
                    con.commit()
                cur.execute("SELECT lifecycle_state, count(*) as cnt FROM global_objects WHERE status = 'ACTIVE' GROUP BY lifecycle_state")
                counts = {r["lifecycle_state"]: r["cnt"] for r in cur.fetchall()}
                return {
                    "status": "NO_OBSERVATIONS",
                    "fused": 0,
                    "new_globals": 0,
                    "provisional_created": 0,
                    "confirmed_created": 0,
                    "provisional_promoted": 0,
                    "updated_globals": 0,
                    "active_confirmed_count": counts.get(LifecycleState.CONFIRMED.value, 0),
                    "active_provisional_count": counts.get(LifecycleState.PROVISIONAL.value, 0),
                    "provisional_expired": len(expired_prov_rows),
                    "deferred": 0
                }

            try:
                from DroneOS.perception.diagnostics import log_gfs_observation_rx, log_gfs_fusion_result
            except ImportError:
                log_gfs_observation_rx = None
                log_gfs_fusion_result = None

            if log_gfs_observation_rx:
                for obs in claimed_observations:
                    log_gfs_observation_rx(
                        drone_id=obs.drone_id,
                        obs_id=obs.obs_id,
                        local_track_id=obs.local_track_id,
                        trace_id=getattr(obs, "trace_id", None) or f"{obs.drone_id}-{obs.local_track_id}",
                        lat=obs.lat,
                        lon=obs.lon,
                        alt=obs.alt,
                        confidence=obs.confidence
                    )

            # Project coordinates into topocentric ENU in metres
            for obs in claimed_observations:
                e, n, u = wgs84_to_enu(
                    obs.lat, obs.lon, obs.alt,
                    self.config.ref_origin_lat,
                    self.config.ref_origin_lon,
                    self.config.ref_origin_alt
                )
                obs.east_m = e
                obs.north_m = n
                obs.up_m = u

            # Convert observations into local tracklets
            local_tracklets = build_local_tracklets(claimed_observations)

            # 3. Fetch frozen snapshot of active global objects and identity history
            cur.execute(
                """SELECT global_id, label, status, lat, lon, alt, east_m, north_m,
                          cov_xx, cov_yy, cov_xy, confidence, first_seen_ms, last_seen_ms,
                          observation_count, source_count, source_estimates_json, version,
                          lifecycle_state, confirmation_method, provisional_reason, expires_at_ms
                   FROM global_objects WHERE status = 'ACTIVE'"""
            )
            active_globals: Dict[str, GlobalObject] = {}
            initial_lifecycle_states: Dict[str, str] = {}
            for r in cur.fetchall():
                src_dict: Dict[str, SourceEstimate] = {}
                raw_src = json.loads(r["source_estimates_json"])
                for d_id, s_data in raw_src.items():
                    src_dict[d_id] = SourceEstimate(
                        drone_id=s_data["drone_id"],
                        drone_session_id=s_data["drone_session_id"],
                        session_generation=s_data["session_generation"],
                        local_track_id=s_data["local_track_id"],
                        east_m=s_data["east_m"],
                        north_m=s_data["north_m"],
                        cov_xx=s_data["cov_xx"],
                        cov_yy=s_data["cov_yy"],
                        cov_xy=s_data["cov_xy"],
                        confidence=s_data["confidence"],
                        last_seen_ms=s_data["last_seen_ms"],
                        observation_count=s_data["observation_count"]
                    )

                l_state = r["lifecycle_state"] if "lifecycle_state" in r.keys() and r["lifecycle_state"] else LifecycleState.CONFIRMED.value
                conf_method = r["confirmation_method"] if "confirmation_method" in r.keys() else None
                prov_reason = r["provisional_reason"] if "provisional_reason" in r.keys() else None
                exp_at = r["expires_at_ms"] if "expires_at_ms" in r.keys() else None

                initial_lifecycle_states[r["global_id"]] = l_state

                active_globals[r["global_id"]] = GlobalObject(
                    global_id=r["global_id"],
                    label=r["label"],
                    status=r["status"],
                    lat=r["lat"],
                    lon=r["lon"],
                    alt=r["alt"],
                    east_m=r["east_m"],
                    north_m=r["north_m"],
                    cov_xx=r["cov_xx"],
                    cov_yy=r["cov_yy"],
                    cov_xy=r["cov_xy"],
                    confidence=r["confidence"],
                    first_seen_ms=r["first_seen_ms"],
                    last_seen_ms=r["last_seen_ms"],
                    observation_count=r["observation_count"],
                    source_count=r["source_count"],
                    source_estimates=src_dict,
                    version=r["version"],
                    lifecycle_state=l_state,
                    confirmation_method=conf_method,
                    provisional_reason=prov_reason,
                    expires_at_ms=exp_at
                )

            # Fetch identity history
            cur.execute(
                """SELECT drone_id, drone_session_id, session_generation, local_track_id, global_id
                   FROM global_track_identity_history"""
            )
            identity_history: Dict[Tuple[str, str, int, str], str] = {}
            for r in cur.fetchall():
                key = (r["drone_id"], r["drone_session_id"], r["session_generation"], r["local_track_id"])
                identity_history[key] = r["global_id"]

            # 4. STAGE A: Independent Hungarian Assignment per drone
            tracklets_by_drone: Dict[str, List[LocalTracklet]] = {}
            for t in local_tracklets:
                if t.drone_id not in tracklets_by_drone:
                    tracklets_by_drone[t.drone_id] = []
                tracklets_by_drone[t.drone_id].append(t)

            drone_matched_pairs: Dict[Tuple[str, str], str] = {}  # (drone_id, local_track_id) -> global_id
            swarm_unmatched_tracklets: List[LocalTracklet] = []
            updated_global_ids: Set[str] = set()
            promoted_global_ids: Set[str] = set()
            reactivated_global_ids: Set[str] = set()
            new_identity_records: List[Tuple[LocalTracklet, str]] = []

            for drone_id, d_tracklets in tracklets_by_drone.items():
                matched, unmatched = associate_drone_tracklets_to_globals(
                    drone_tracklets=d_tracklets,
                    active_globals=list(active_globals.values()),
                    identity_history=identity_history,
                    config=self.config
                )
                swarm_unmatched_tracklets.extend(unmatched)

                for trk in d_tracklets:
                    if trk.local_track_id in matched:
                        g_id = matched[trk.local_track_id]
                        glob = active_globals[g_id]
                        update_global_object_with_tracklet(
                            glob=glob,
                            tracklet=trk,
                            ref_lat=self.config.ref_origin_lat,
                            ref_lon=self.config.ref_origin_lon,
                            ref_alt=self.config.ref_origin_alt,
                            config=self.config
                        )
                        updated_global_ids.add(g_id)
                        if initial_lifecycle_states.get(g_id) == LifecycleState.PROVISIONAL.value and glob.lifecycle_state == LifecycleState.CONFIRMED.value:
                            promoted_global_ids.add(g_id)
                        drone_matched_pairs[(trk.drone_id, trk.local_track_id)] = g_id
                        new_identity_records.append((trk, g_id))

            # 5. RETRY HISTORICAL UNMATCHED TRACKS AGAINST THEIR HISTORICAL IDENTITY
            # Invariant: A local tracklet with an existing historical identity binding
            # can NEVER form a new global object. If its historical global track was
            # temporarily inactive or unavailable, retry re-associating or reactivating it.
            fresh_unmatched: List[LocalTracklet] = []
            historical_unmatched: List[LocalTracklet] = []
            historical_deferred: List[LocalTracklet] = []

            for trk in swarm_unmatched_tracklets:
                hist_key = (trk.drone_id, trk.drone_session_id, trk.session_generation, trk.local_track_id)
                if hist_key in identity_history:
                    historical_unmatched.append(trk)
                else:
                    fresh_unmatched.append(trk)

            for h_trk in historical_unmatched:
                hist_key = (h_trk.drone_id, h_trk.drone_session_id, h_trk.session_generation, h_trk.local_track_id)
                hist_g_id = identity_history[hist_key]

                # Check if historical global object is already loaded or in DB
                target_glob = active_globals.get(hist_g_id)
                is_reactivation = False
                if target_glob is None:
                    # Query inactive or historical global object from DB
                    cur.execute(
                        """SELECT global_id, label, status, lat, lon, alt, east_m, north_m,
                                  cov_xx, cov_yy, cov_xy, confidence, first_seen_ms, last_seen_ms,
                                  observation_count, source_count, source_estimates_json, version
                           FROM global_objects WHERE global_id = ?""",
                        (hist_g_id,)
                    )
                    hr = cur.fetchone()
                    if hr is not None:
                        is_reactivation = (hr["status"] != "ACTIVE")
                        src_dict = {}
                        raw_src = json.loads(hr["source_estimates_json"])
                        for d_id, s_data in raw_src.items():
                            src_dict[d_id] = SourceEstimate(
                                drone_id=s_data["drone_id"],
                                drone_session_id=s_data["drone_session_id"],
                                session_generation=s_data["session_generation"],
                                local_track_id=s_data["local_track_id"],
                                east_m=s_data["east_m"],
                                north_m=s_data["north_m"],
                                cov_xx=s_data["cov_xx"],
                                cov_yy=s_data["cov_yy"],
                                cov_xy=s_data["cov_xy"],
                                confidence=s_data["confidence"],
                                last_seen_ms=s_data["last_seen_ms"],
                                observation_count=s_data["observation_count"]
                            )
                        target_glob = GlobalObject(
                            global_id=hr["global_id"],
                            label=hr["label"],
                            status="ACTIVE",
                            lat=hr["lat"],
                            lon=hr["lon"],
                            alt=hr["alt"],
                            east_m=hr["east_m"],
                            north_m=hr["north_m"],
                            cov_xx=hr["cov_xx"],
                            cov_yy=hr["cov_yy"],
                            cov_xy=hr["cov_xy"],
                            confidence=hr["confidence"],
                            first_seen_ms=hr["first_seen_ms"],
                            last_seen_ms=hr["last_seen_ms"],
                            observation_count=hr["observation_count"],
                            source_count=hr["source_count"],
                            source_estimates=src_dict,
                            version=hr["version"],
                            lifecycle_state=hr["lifecycle_state"] if "lifecycle_state" in hr.keys() and hr["lifecycle_state"] else LifecycleState.CONFIRMED.value,
                            confirmation_method=hr["confirmation_method"] if "confirmation_method" in hr.keys() else None,
                            provisional_reason=hr["provisional_reason"] if "provisional_reason" in hr.keys() else None,
                            expires_at_ms=hr["expires_at_ms"] if "expires_at_ms" in hr.keys() else None
                        )
                        active_globals[hist_g_id] = target_glob

                if target_glob is not None and target_glob.label == h_trk.label:
                    dx = h_trk.east_m - target_glob.east_m
                    dy = h_trk.north_m - target_glob.north_m
                    dist = math.hypot(dx, dy)
                    if dist <= self.config.spatial_physical_r_max_m * 2.0:
                        # Re-associated or reactivated with historical global identity
                        target_glob.status = "ACTIVE"
                        if is_reactivation:
                            reactivated_global_ids.add(hist_g_id)
                        update_global_object_with_tracklet(
                            glob=target_glob,
                            tracklet=h_trk,
                            ref_lat=self.config.ref_origin_lat,
                            ref_lon=self.config.ref_origin_lon,
                            ref_alt=self.config.ref_origin_alt,
                            config=self.config
                        )
                        updated_global_ids.add(hist_g_id)
                        if initial_lifecycle_states.get(hist_g_id) == LifecycleState.PROVISIONAL.value and target_glob.lifecycle_state == LifecycleState.CONFIRMED.value:
                            promoted_global_ids.add(hist_g_id)
                        drone_matched_pairs[(h_trk.drone_id, h_trk.local_track_id)] = hist_g_id
                        new_identity_records.append((h_trk, hist_g_id))
                        continue

                # Safely defer historical tracklet for next batch retry against its historical identity
                historical_deferred.append(h_trk)

            # 6. STAGE B: New Global Object Formation ONLY on genuinely fresh unmatched tracklets
            new_globals, ambiguous_def, low_conf_def = form_new_global_objects(
                unmatched_tracklets=fresh_unmatched,
                config=self.config,
                ref_lat=self.config.ref_origin_lat,
                ref_lon=self.config.ref_origin_lon,
                ref_alt=self.config.ref_origin_alt
            )

            for ng in new_globals:
                active_globals[ng.global_id] = ng
                updated_global_ids.add(ng.global_id)
                for trk in fresh_unmatched:
                    if trk.drone_id in ng.source_estimates:
                        se = ng.source_estimates[trk.drone_id]
                        if se.local_track_id == trk.local_track_id:
                            new_identity_records.append((trk, ng.global_id))
                            drone_matched_pairs[(trk.drone_id, trk.local_track_id)] = ng.global_id

            # 7. Item Dispositions
            item_dispositions: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
            for trk in local_tracklets:
                trk_key = (trk.drone_id, trk.local_track_id)
                if trk_key in drone_matched_pairs:
                    g_id = drone_matched_pairs[trk_key]
                    for o in trk.observations:
                        item_dispositions[o.obs_id] = ("FUSED", None, g_id)
                elif trk in historical_deferred:
                    for o in trk.observations:
                        item_dispositions[o.obs_id] = ("DEFERRED", "HISTORICAL_TRACK_DEFERRED_RETRY", None)
                elif trk in ambiguous_def:
                    for o in trk.observations:
                        item_dispositions[o.obs_id] = ("DEFERRED", "AMBIGUOUS_CLIQUE", None)
                elif trk in low_conf_def:
                    for o in trk.observations:
                        item_dispositions[o.obs_id] = ("DEFERRED", "LOW_CONFIDENCE", None)

            # 7. Phase B: Atomic Database Commit
            if log_gfs_fusion_result:
                new_ids = {ng.global_id for ng in new_globals}
                for g_id in updated_global_ids:
                    glob = active_globals.get(g_id)
                    if glob:
                        action = "CREATED_NEW" if g_id in new_ids else "FUSED_EXISTING"
                        src_drones = list(glob.source_estimates.keys())
                        trk_ids = [se.local_track_id for se in glob.source_estimates.values()]
                        log_gfs_fusion_result(
                            global_id=glob.global_id,
                            action=action,
                            label=glob.label,
                            lat=glob.lat,
                            lon=glob.lon,
                            alt=glob.alt,
                            confidence=glob.confidence,
                            observation_count=glob.observation_count,
                            source_drones=src_drones,
                            contributing_local_tracks=trk_ids
                        )

            # Update / Insert Global Objects
            for g_id in updated_global_ids:
                glob = active_globals[g_id]
                src_json = json.dumps({
                    d: {
                        "drone_id": e.drone_id,
                        "drone_session_id": e.drone_session_id,
                        "session_generation": e.session_generation,
                        "local_track_id": e.local_track_id,
                        "east_m": e.east_m,
                        "north_m": e.north_m,
                        "cov_xx": e.cov_xx,
                        "cov_yy": e.cov_yy,
                        "cov_xy": e.cov_xy,
                        "confidence": e.confidence,
                        "last_seen_ms": e.last_seen_ms,
                        "observation_count": e.observation_count
                    } for d, e in glob.source_estimates.items()
                })

                cur.execute(
                    """INSERT INTO global_objects
                       (global_id, label, status, lat, lon, alt, east_m, north_m,
                        cov_xx, cov_yy, cov_xy, confidence, first_seen_ms, last_seen_ms,
                        observation_count, source_count, source_estimates_json, version,
                        lifecycle_state, confirmation_method, provisional_reason, expires_at_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(global_id) DO UPDATE SET
                        status = excluded.status,
                        lat = excluded.lat,
                        lon = excluded.lon,
                        alt = excluded.alt,
                        east_m = excluded.east_m,
                        north_m = excluded.north_m,
                        cov_xx = excluded.cov_xx,
                        cov_yy = excluded.cov_yy,
                        cov_xy = excluded.cov_xy,
                        confidence = excluded.confidence,
                        last_seen_ms = excluded.last_seen_ms,
                        observation_count = excluded.observation_count,
                        source_count = excluded.source_count,
                        source_estimates_json = excluded.source_estimates_json,
                        version = version + 1,
                        lifecycle_state = excluded.lifecycle_state,
                        confirmation_method = excluded.confirmation_method,
                        provisional_reason = excluded.provisional_reason,
                        expires_at_ms = excluded.expires_at_ms""",
                    (glob.global_id, glob.label, glob.status, glob.lat, glob.lon, glob.alt,
                     glob.east_m, glob.north_m, glob.cov_xx, glob.cov_yy, glob.cov_xy,
                     glob.confidence, glob.first_seen_ms, glob.last_seen_ms,
                     glob.observation_count, glob.source_count, src_json, glob.version,
                     glob.lifecycle_state, glob.confirmation_method, glob.provisional_reason, glob.expires_at_ms)
                )

                # Enqueue Outbox Event
                new_gid_set = {ng.global_id for ng in new_globals}
                if g_id in reactivated_global_ids:
                    evt_type = "GLOBAL_OBJECT_REACTIVATED"
                elif g_id in promoted_global_ids:
                    evt_type = "GLOBAL_OBJECT_CONFIRMED"
                elif g_id in new_gid_set:
                    evt_type = "GLOBAL_OBJECT_NEW"
                else:
                    evt_type = "GLOBAL_OBJECT_UPDATE"
                enqueue_global_object_event(con, glob, evt_type, now_ms)

            # Insert / Update identity history
            for trk, g_id in new_identity_records:
                cur.execute(
                    """INSERT INTO global_track_identity_history
                       (drone_id, drone_session_id, session_generation, local_track_id, global_id,
                        first_frame_id, last_frame_id, first_seen_ms, last_seen_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(drone_id, drone_session_id, session_generation, local_track_id)
                       DO UPDATE SET
                        last_frame_id = excluded.last_frame_id,
                        last_seen_ms = excluded.last_seen_ms""",
                    (trk.drone_id, trk.drone_session_id, trk.session_generation, trk.local_track_id, g_id,
                     trk.first_frame_id, trk.last_frame_id, trk.first_seen_ms, trk.last_seen_ms)
                )

            # Commit item dispositions and mark inbox packets COMPLETED
            commit_packet_dispositions(con, claim_token, item_dispositions, now_ms, deferred_ttl_ms=self.config.deferred_observation_ttl_ms)

            # Remove resolved observations from deferred_observations
            for oid in recovered_deferred_ids:
                if oid in item_dispositions and item_dispositions[oid][0] == "FUSED":
                    cur.execute("DELETE FROM deferred_observations WHERE obs_id = ?", (oid,))

            provisional_created = len([ng for ng in new_globals if ng.lifecycle_state == LifecycleState.PROVISIONAL.value])
            confirmed_created = len([ng for ng in new_globals if ng.lifecycle_state == LifecycleState.CONFIRMED.value])
            provisional_promoted = len(promoted_global_ids)
            active_confirmed = len([g for g in active_globals.values() if g.lifecycle_state == LifecycleState.CONFIRMED.value and g.status == "ACTIVE"])
            active_provisional = len([g for g in active_globals.values() if g.lifecycle_state == LifecycleState.PROVISIONAL.value and g.status == "ACTIVE"])

            batch_metrics = {
                "status": "SUCCESS",
                "fused": len([d for d in item_dispositions.values() if d[0] == "FUSED"]),
                "new_globals": len(new_globals),
                "provisional_created": provisional_created,
                "confirmed_created": confirmed_created,
                "provisional_promoted": provisional_promoted,
                "updated_globals": len(updated_global_ids),
                "active_confirmed_count": active_confirmed,
                "active_provisional_count": active_provisional,
                "provisional_expired": len(expired_prov_rows),
                "deferred": len(ambiguous_def) + len(low_conf_def) + len(historical_deferred)
            }
            logger.info("GFS Batch Completed: %s", json.dumps(batch_metrics))
            return batch_metrics

    def run_daemon(self) -> None:
        """
        Runs the continuous OCC fusion daemon loop.
        Acquires exclusive process lock, ingests UDP packets, executes OCC fusion batches,
        and leases outbox events.
        """
        import sys
        import socket
        logger.info(f"Starting GFS Daemon with db={self.config.db_path}, lock={self.config.lock_path}")
        if not self.lock.acquire(blocking=False):
            logger.error(f"Failed to acquire exclusive GFS process lock on {self.config.lock_path}. Another GFS instance is running.")
            sys.exit(1)

        inbox_sock = None
        ack_sock = None

        try:
            # Set up UDP inbox socket
            inbox_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            inbox_sock.setblocking(False)
            try:
                inbox_sock.bind(("0.0.0.0", self.config.inbox_udp_port))
                logger.info(f"GFS Inbox listening on UDP port {self.config.inbox_udp_port}")
            except OSError as e:
                logger.warning(f"Could not bind GFS inbox UDP port {self.config.inbox_udp_port}: {e}")
                inbox_sock = None

            # Set up UDP ACK socket
            ack_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ack_sock.setblocking(False)
            try:
                ack_sock.bind(("0.0.0.0", self.config.ack_udp_port))
                logger.info(f"GFS ACK receiver listening on UDP port {self.config.ack_udp_port}")
            except OSError as e:
                logger.warning(f"Could not bind GFS ACK UDP port {self.config.ack_udp_port}: {e}")
                ack_sock = None

            print("=" * 60)
            print(" PhoneOS Swarm — Global Fusion Service (GFS) Daemon")
            print("=" * 60)
            print(f"  Database:        {self.config.db_path}")
            print(f"  Lockfile:        {self.config.lock_path}")
            print(f"  Inbox UDP Port:  {self.config.inbox_udp_port}")
            print(f"  Outbox UDP Port: {self.config.outbox_udp_port}")
            print(f"  ACK UDP Port:    {self.config.ack_udp_port}")
            print(f"  Batch Interval:  {self.config.batch_interval_ms} ms")
            print(f"  Origin Ref:      ({self.config.ref_origin_lat:.6f}, {self.config.ref_origin_lon:.6f})")
            print("=" * 60)
            print("  GFS status: ACTIVE (running OCC fusion pipeline)")
            print("  Press Ctrl+C to terminate.")
            print("=" * 60)

            interval_s = self.config.batch_interval_ms / 1000.0

            while True:
                now_ms = int(time.time() * 1000)

                # 1. Drain inbox UDP socket
                if inbox_sock:
                    while True:
                        try:
                            data, addr = inbox_sock.recvfrom(65535)
                            pkt_dict = json.loads(data.decode("utf-8"))
                            with get_db(self.config.db_path) as con:
                                ingest_raw_packet(con, pkt_dict, self.config, now_ms=now_ms)
                        except (BlockingIOError, OSError):
                            break
                        except Exception as e:
                            logger.warning(f"Error ingesting raw UDP packet: {e}")

                # 2. Drain ACK UDP socket
                if ack_sock:
                    while True:
                        try:
                            data, addr = ack_sock.recvfrom(65535)
                            ack_dict = json.loads(data.decode("utf-8"))
                            with get_db(self.config.db_path) as con:
                                conf_msg = process_gcs_ack(con, ack_dict, self.config, now_ms=now_ms)
                                if conf_msg and ack_sock:
                                    ack_sock.sendto(json.dumps(conf_msg).encode("utf-8"), addr)
                        except (BlockingIOError, OSError):
                            break
                        except Exception as e:
                            logger.warning(f"Error processing GFS ACK packet: {e}")

                # 3. Execute OCC fusion batch
                try:
                    res = self.process_fusion_batch(now_ms=now_ms)
                    if res.get("fused", 0) > 0 or res.get("new_globals", 0) > 0 or res.get("updated_globals", 0) > 0:
                        logger.info(f"GFS Batch: fused={res['fused']}, new={res['new_globals']}, updated={res['updated_globals']}")
                except Exception as b_err:
                    logger.error(f"Error in GFS OCC batch: {b_err}")

                # 4. Lease outbox events and broadcast
                try:
                    with get_db(self.config.db_path) as con:
                        lease_token, events = lease_outbox_events(con, "gfs-daemon", 10000, self.config, now_ms=now_ms)
                        for evt in events:
                            record_successful_transmission(con, evt.id, lease_token, self.config.outbox_ack_timeout_ms, now_ms=now_ms)
                            if self.config.outbox_udp_port:
                                try:
                                    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                    out_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                                    payload = json.loads(evt.payload)
                                    msg = {
                                        "msg_type": "detection",
                                        "sender_id": "gfs",
                                        "timestamp": time.time(),
                                        "event_id": payload.get("event_id"),
                                        "detections": [{
                                            "id": payload.get("global_id"),
                                            "global_id": payload.get("global_id"),
                                            "label": payload.get("label", "rubbish"),
                                            "confidence": payload.get("confidence", 0.8),
                                            "lat": payload.get("lat"),
                                            "lon": payload.get("lon"),
                                            "alt": payload.get("alt"),
                                            "status": payload.get("status", "update"),
                                            "lifecycle_state": payload.get("lifecycle_state", "CONFIRMED"),
                                            "confirmation_method": payload.get("confirmation_method"),
                                            "provisional_reason": payload.get("provisional_reason"),
                                            "expires_at_ms": payload.get("expires_at_ms"),
                                            "hits": payload.get("observation_count", 1),
                                            "synthetic": False,
                                            "reportingDrone": "GFS",
                                            "version": payload.get("version", 1),
                                        }]
                                    }
                                    out_sock.sendto(json.dumps(msg).encode("utf-8"), ("127.0.0.1", self.config.outbox_udp_port))
                                    out_sock.close()
                                except Exception:
                                    pass
                except Exception as out_err:
                    logger.debug(f"Outbox broadcast cycle: {out_err}")

                time.sleep(interval_s)

        except KeyboardInterrupt:
            print("\n[GFS] Stopping daemon cleanly...")
        finally:
            self.lock.release()
            if inbox_sock:
                try:
                    inbox_sock.close()
                except Exception:
                    pass
            if ack_sock:
                try:
                    ack_sock.close()
                except Exception:
                    pass
            print("[GFS] Daemon stopped.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PhoneOS Swarm Global Fusion Service (GFS)")
    parser.add_argument("--config", "-c", default=None, help="Path to gfs_config.yaml")
    parser.add_argument("--db", default=None, help="Override database path")
    args = parser.parse_args()

    cfg = GFSConfig.load(args.config)
    if args.db:
        cfg.db_path = args.db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    service = GFSService(cfg)
    service.run_daemon()


if __name__ == "__main__":
    main()

