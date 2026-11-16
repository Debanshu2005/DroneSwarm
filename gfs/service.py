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
import sqlite3

from gfs.models import (
    Observation,
    LocalTracklet,
    GlobalObject,
    SourceEstimate,
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
                return {"status": "NO_OBSERVATIONS", "fused": 0, "new_globals": 0}

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
                          observation_count, source_count, source_estimates_json, version
                   FROM global_objects WHERE status = 'ACTIVE'"""
            )
            active_globals: Dict[str, GlobalObject] = {}
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
                    version=r["version"]
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

            drone_matched_pairs: Dict[str, str] = {}  # local_track_id -> global_id
            swarm_unmatched_tracklets: List[LocalTracklet] = []
            updated_global_ids: Set[str] = set()
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
                            ref_alt=self.config.ref_origin_alt
                        )
                        updated_global_ids.add(g_id)
                        drone_matched_pairs[trk.local_track_id] = g_id
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
                            version=hr["version"]
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
                            ref_alt=self.config.ref_origin_alt
                        )
                        updated_global_ids.add(hist_g_id)
                        drone_matched_pairs[h_trk.local_track_id] = hist_g_id
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
                            drone_matched_pairs[trk.local_track_id] = ng.global_id

            # 7. Item Dispositions
            item_dispositions: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
            for trk in local_tracklets:
                if trk.local_track_id in drone_matched_pairs:
                    g_id = drone_matched_pairs[trk.local_track_id]
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
                        observation_count, source_count, source_estimates_json, version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        version = version + 1""",
                    (glob.global_id, glob.label, glob.status, glob.lat, glob.lon, glob.alt,
                     glob.east_m, glob.north_m, glob.cov_xx, glob.cov_yy, glob.cov_xy,
                     glob.confidence, glob.first_seen_ms, glob.last_seen_ms,
                     glob.observation_count, glob.source_count, src_json, glob.version)
                )

                # Enqueue Outbox Event
                new_gid_set = {ng.global_id for ng in new_globals}
                if g_id in reactivated_global_ids:
                    evt_type = "GLOBAL_OBJECT_REACTIVATED"
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

            return {
                "status": "SUCCESS",
                "fused": len([d for d in item_dispositions.values() if d[0] == "FUSED"]),
                "new_globals": len(new_globals),
                "updated_globals": len(updated_global_ids),
                "deferred": len(ambiguous_def) + len(low_conf_def) + len(historical_deferred)
            }

