#!/usr/bin/env python3
"""
scripts/reproduce_five_target_regression.py

Forensic investigation script to reproduce and isolate the regression where
5 spawned garbage targets result in only 1 PROVISIONAL marker in PhoneOS.

Traces all 5 targets across all 7 stages:
Stage 1: YOLO detections on each DroneOS instance
Stage 2: Local TrackAggregator output
Stage 3: UDP observations sent to GFS
Stage 4: GFS inbox processing, deduplication, track association, and provisional lifecycle
Stage 5: GFS SQLite database and outbox events
Stage 6: Relay snapshot response
Stage 7: PhoneOS snapshot reconciliation and GarbageLayer rendering
"""

import sys
import math
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.geolocate import (
    meters_to_lat_lon,
    lat_lon_to_meters,
    pixel_to_ray_ned,
    pixel_to_lat_lon,
    compute_ray_to_point_cpa,
)
from DroneOS.perception.aggregator import TrackAggregator, GeoreferencedDetection
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage

from gfs.models import Observation, LocalTracklet, GlobalObject, LifecycleState, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.database import init_db, get_db
from gfs.service import GFSService
from gfs.inbox import ingest_raw_packet
from relay.relay import UdpWebsocketRelay
from scripts.forensic_five_target_investigation import project_world_ned_to_pixel


def run_pipeline_trace():
    print("=" * 80)
    print("STAGE-BY-STAGE PIPELINE TRACE: 5 SPAWNED TARGETS")
    print("=" * 80)

    origin_lat = 47.641468
    origin_lon = -122.140165
    ref_alt = 0.0

    # 5 targets
    targets = [
        {"id": "Target1", "ned": (4.0, 4.0, 0.0), "label": "rubbish", "simulated_conf": 0.62},
        {"id": "Target2", "ned": (4.0, 12.0, 0.0), "label": "rubbish", "simulated_conf": 0.59},
        {"id": "Target3", "ned": (10.0, 8.0, 0.0), "label": "rubbish", "simulated_conf": 0.65},
        {"id": "Target4", "ned": (16.0, 4.0, 0.0), "label": "rubbish", "simulated_conf": 0.58},
        {"id": "Target5", "ned": (16.0, 12.0, 0.0), "label": "rubbish", "simulated_conf": 0.61},
    ]
    for t in targets:
        t["lat"], t["lon"] = meters_to_lat_lon(t["ned"][0], t["ned"][1], origin_lat, origin_lon)

    drones = {
        "drone1": {"ned": (10.0, 8.0, -15.0), "alt": 15.0, "attitude": (0.0, 0.0, 0.0)},
        "drone2": {"ned": (5.0, 3.0, -12.0), "alt": 12.0, "attitude": (0.0, 0.0, 0.0)},
        "drone3": {"ned": (5.0, 13.0, -12.0), "alt": 12.0, "attitude": (0.0, 0.0, 0.0)},
        "drone4": {"ned": (0.0, 8.0, -10.0), "alt": 10.0, "attitude": (0.0, 0.0, 0.0)},
    }
    for d_id, d in drones.items():
        d["lat"], d["lon"] = meters_to_lat_lon(d["ned"][0], d["ned"][1], origin_lat, origin_lon)

    # ---------------------------------------------------------
    # STAGE 1: YOLO Detections on Each DroneOS Instance
    # ---------------------------------------------------------
    img_w, img_h = 1280, 720
    hfov_deg = 90.0
    cam_mount_pitch = 90.0

    raw_dets_by_drone = {d: [] for d in drones}
    stage1_records = []
    seen_targets_stage1 = set()

    for d_id, d_info in drones.items():
        for t in targets:
            u, v, in_fov, depth = project_world_ned_to_pixel(
                target_ned=t["ned"],
                drone_ned=d_info["ned"],
                drone_attitude=d_info["attitude"],
                cam_mount_pitch_deg=cam_mount_pitch,
                hfov_deg=hfov_deg,
                img_w=img_w,
                img_h=img_h
            )
            if in_fov:
                seen_targets_stage1.add(t["id"])
                det_record = {
                    "drone_id": d_id,
                    "target_id": t["id"],
                    "u": u,
                    "v": v,
                    "confidence": t["simulated_conf"],
                    "label": t["label"],
                    "depth_m": depth,
                    "true_lat": t["lat"],
                    "true_lon": t["lon"],
                    "bbox": (max(0, u - 20), max(0, v - 20), min(img_w, u + 20), min(img_h, v + 20))
                }
                raw_dets_by_drone[d_id].append(det_record)
                stage1_records.append(det_record)

    print(f"\n[Stage 1] YOLO Detections:")
    print(f"  Unique targets detected: {len(seen_targets_stage1)} / {len(targets)}")
    print(f"  Total sightings: {len(stage1_records)}")
    for d in stage1_records:
        print(f"    - {d['drone_id']} detected {d['target_id']}: conf={d['confidence']:.2f}, pixel=({d['u']}, {d['v']})")

    # ---------------------------------------------------------
    # STAGE 2: Local TrackAggregator Output (min_hits=2, georeferenced, peer-exclusion)
    # ---------------------------------------------------------
    georef_by_drone = {d: [] for d in drones}
    for d_id, dets in raw_dets_by_drone.items():
        d_info = drones[d_id]
        peers = {p_id: p_data for p_id, p_data in drones.items() if p_id != d_id}
        for det in dets:
            u = (det["bbox"][0] + det["bbox"][2]) / 2.0
            v = (det["bbox"][1] + det["bbox"][3]) / 2.0
            res = pixel_to_lat_lon(
                u=u, v=v, img_w=img_w, img_h=img_h,
                hfov_deg=hfov_deg, mount_pitch_deg=cam_mount_pitch,
                roll_deg=d_info["attitude"][0], pitch_deg=d_info["attitude"][1], yaw_deg=d_info["attitude"][2],
                drone_alt=d_info["alt"], ground_elevation_m=0.0,
                drone_lat=d_info["lat"], drone_lon=d_info["lon"]
            )
            if res is None: continue
            glat, glon, agl = res

            ray_ned = pixel_to_ray_ned(
                u=u, v=v, img_w=img_w, img_h=img_h,
                hfov_deg=hfov_deg, mount_pitch_deg=cam_mount_pitch,
                roll_deg=d_info["attitude"][0], pitch_deg=d_info["attitude"][1], yaw_deg=d_info["attitude"][2]
            )
            # Peer exclusion
            is_peer = False
            for p_id, p_data in peers.items():
                p_dn, p_de = lat_lon_to_meters(p_data["lat"], p_data["lon"], d_info["lat"], d_info["lon"])
                p_dd = -(p_data["alt"] - d_info["alt"])
                s_depth, d_perp = compute_ray_to_point_cpa(ray_ned, (p_dn, p_de, p_dd))
                if s_depth > 0.0 and d_perp < 2.0:
                    is_peer = True
                    break
            if not is_peer:
                georef_by_drone[d_id].append(
                    GeoreferencedDetection(
                        label=det["label"],
                        confidence=det["confidence"],
                        lat=glat,
                        lon=glon,
                        altitude_agl=agl
                    )
                )

    aggregators = {d: TrackAggregator(drone_id=d, merge_radius_m=2.5, min_hits=2, update_interval_s=10.0) for d in drones}
    # Frame 1: hit 1 (not emitted)
    for d, dets in georef_by_drone.items():
        aggregators[d].update(dets, now=1000.0)
    # Frame 2: hit 2 (emitted)
    emitted_by_drone = {}
    for d, dets in georef_by_drone.items():
        emitted_by_drone[d] = aggregators[d].update(dets, now=1000.1)

    stage2_items = []
    seen_targets_stage2 = set()
    for d, items in emitted_by_drone.items():
        for it in items:
            stage2_items.append((d, it))
            # match with ground truth target
            for t in targets:
                dn, de = lat_lon_to_meters(it.lat, it.lon, t["lat"], t["lon"])
                if math.hypot(dn, de) < 2.0:
                    seen_targets_stage2.add(t["id"])

    print(f"\n[Stage 2] Local TrackAggregator Output (min_hits=2):")
    print(f"  Unique targets represented: {len(seen_targets_stage2)} / {len(targets)}")
    print(f"  Total emitted tracks: {len(stage2_items)}")
    for d, it in stage2_items:
        print(f"    - Drone '{d}': track '{it.id}', conf={it.confidence:.2f}, hits={it.hits}, pos=({it.lat:.6f}, {it.lon:.6f})")

    # ---------------------------------------------------------
    # STAGE 3: UDP Observations Sent to GFS
    # ---------------------------------------------------------
    now_ms = 1001000
    packets_by_drone = {}
    stage3_observations = []
    seen_targets_stage3 = set()

    for d_id, items in emitted_by_drone.items():
        obs_list = []
        for it in items:
            obs_dict = {
                "frame_id": 100,
                "local_track_id": it.id,
                "capture_timestamp_ms": now_ms,
                "lat": it.lat,
                "lon": it.lon,
                "alt": it.altitude_agl or 0.0,
                "label": it.label,
                "confidence": it.confidence,
                "cov_xx": 0.35,
                "cov_yy": 0.35,
                "cov_xy": 0.01
            }
            obs_list.append(obs_dict)
            stage3_observations.append((d_id, obs_dict))
            for t in targets:
                dn, de = lat_lon_to_meters(it.lat, it.lon, t["lat"], t["lon"])
                if math.hypot(dn, de) < 2.0:
                    seen_targets_stage3.add(t["id"])

        packets_by_drone[d_id] = {
            "packet_id": f"pkt-{d_id}-100",
            "drone_id": d_id,
            "drone_session_id": f"sess-{d_id}",
            "session_generation": 1,
            "observations": obs_list
        }

    print(f"\n[Stage 3] UDP Observations Sent to GFS:")
    print(f"  Unique targets represented: {len(seen_targets_stage3)} / {len(targets)}")
    print(f"  Total observations in UDP packets: {len(stage3_observations)}")
    for d_id, obs in stage3_observations:
        print(f"    - Packet from '{d_id}': local_track_id='{obs['local_track_id']}', conf={obs['confidence']:.2f}")

    # ---------------------------------------------------------
    # STAGE 4: GFS Inbox Processing, Deduplication, Track Association & Lifecycle
    # ---------------------------------------------------------
    db_file = str(REPO_ROOT / "data" / "reproduce_five_target_gfs.db")
    lock_file = str(REPO_ROOT / "data" / "reproduce_five_target_gfs.lock")
    if Path(db_file).exists(): Path(db_file).unlink()
    if Path(lock_file).exists(): Path(lock_file).unlink()

    init_db(db_file)
    cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=origin_lat,
        ref_origin_lon=origin_lon,
        ref_origin_alt=ref_alt,
        spatial_physical_r_max_m=3.5,
        provisional_object_ttl_ms=60000
    )
    service = GFSService(cfg)

    with get_db(db_file) as con:
        for d_id, pkt in packets_by_drone.items():
            if pkt["observations"]:
                ingest_raw_packet(con, pkt, cfg, now_ms=now_ms)

    fusion_res = service.process_fusion_batch(now_ms=now_ms)

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute(
            """SELECT global_id, label, status, lat, lon, confidence, source_count,
                      source_estimates_json, lifecycle_state, confirmation_method, provisional_reason
               FROM global_objects WHERE status = 'ACTIVE' ORDER BY global_id"""
        )
        active_global_rows = cur.fetchall()

    seen_targets_stage4 = set()
    for r in active_global_rows:
        for t in targets:
            dn, de = lat_lon_to_meters(r["lat"], r["lon"], t["lat"], t["lon"])
            if math.hypot(dn, de) < 2.5:
                seen_targets_stage4.add(t["id"])

    print(f"\n[Stage 4] GFS Inbox & Hypothesis Formation:")
    print(f"  Unique targets represented: {len(seen_targets_stage4)} / {len(targets)}")
    print(f"  GFS Global Objects Formed: {len(active_global_rows)}")
    for r in active_global_rows:
        srcs = list(json.loads(r["source_estimates_json"]).keys())
        print(f"    - [{r['lifecycle_state']}] ID: {r['global_id']}, Sources: {srcs}, Conf: {r['confidence']:.4f}, Reason/Method: {r['provisional_reason'] or r['confirmation_method']}")

    # ---------------------------------------------------------
    # STAGE 5: GFS SQLite Database & Outbox Events
    # ---------------------------------------------------------
    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute("SELECT id, payload, status FROM gfs_outbox_events ORDER BY id")
        outbox_rows = cur.fetchall()

    seen_targets_stage5 = set()
    for row in outbox_rows:
        p = json.loads(row["payload"])
        for t in targets:
            dn, de = lat_lon_to_meters(p["lat"], p["lon"], t["lat"], t["lon"])
            if math.hypot(dn, de) < 2.5:
                seen_targets_stage5.add(t["id"])

    print(f"\n[Stage 5] GFS SQLite DB & Outbox Events:")
    print(f"  Unique targets in Outbox: {len(seen_targets_stage5)} / {len(targets)}")
    print(f"  Outbox event count: {len(outbox_rows)}")
    for row in outbox_rows:
        p = json.loads(row["payload"])
        print(f"    - Event {row['id']}: [{p.get('lifecycle_state')}] ID: {p.get('global_id')}, Conf: {p.get('confidence'):.2f}")

    # ---------------------------------------------------------
    # STAGE 6: Relay Snapshot Response
    # ---------------------------------------------------------
    import asyncio
    relay = UdpWebsocketRelay(
        ws_port=8099,
        udp_bind_port=14598,
        gfs_db_path=db_file,
        relay_ack_db=str(Path(db_file).parent / "reproduce_relay_acks.db")
    )
    class MockWs:
        def __init__(self):
            self.sent_msgs = []
        async def send(self, data):
            self.sent_msgs.append(json.loads(data))

    ws = MockWs()
    asyncio.run(relay._handle_gfs_snapshot_request(ws, {"type": "gfs_snapshot_request"}))
    snap_objects = ws.sent_msgs[0].get("objects", [])

    seen_targets_stage6 = set()
    for o in snap_objects:
        for t in targets:
            dn, de = lat_lon_to_meters(o["lat"], o["lon"], t["lat"], t["lon"])
            if math.hypot(dn, de) < 2.5:
                seen_targets_stage6.add(t["id"])

    print(f"\n[Stage 6] Relay Snapshot Response:")
    print(f"  Unique targets serialized in snapshot: {len(seen_targets_stage6)} / {len(targets)}")
    print(f"  Total serialized objects: {len(snap_objects)}")
    for o in snap_objects:
        print(f"    - [{o.get('lifecycle_state')}] ID: {o.get('global_id')}, Conf: {o.get('confidence'):.2f}, Pos: ({o.get('lat'):.6f}, {o.get('lon'):.6f})")

    # ---------------------------------------------------------
    # STAGE 7: PhoneOS Snapshot Reconciliation & GarbageLayer
    # ---------------------------------------------------------
    # Run through actual Node PhoneOS reconcileSnapshot
    import subprocess
    import shutil

    node_exe = shutil.which("node")
    node_script = f"""
    import {{ reconcileSnapshot, getConfirmedGarbage, getProvisionalGarbage }} from './mobile/src/utils/garbage.js';
    const snapshotObjects = {json.dumps(snap_objects)};
    const reconciled = reconcileSnapshot({{}}, snapshotObjects, {{}}, {{}}, 0, new Set());
    const confirmed = getConfirmedGarbage(reconciled);
    const provisional = getProvisionalGarbage(reconciled);
    console.log(JSON.stringify({{
        total: Object.keys(reconciled).length,
        confirmedCount: Object.keys(confirmed).length,
        provisionalCount: Object.keys(provisional).length,
        markers: reconciled
    }}));
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", node_script], capture_output=True, text=True)
    phoneos_res = json.loads(proc.stdout)

    seen_targets_stage7 = set()
    for mid, m in phoneos_res["markers"].items():
        for t in targets:
            dn, de = lat_lon_to_meters(m["lat"], m["lon"], t["lat"], t["lon"])
            if math.hypot(dn, de) < 2.5:
                seen_targets_stage7.add(t["id"])

    print(f"\n[Stage 7] PhoneOS Snapshot Reconciliation & GarbageLayer:")
    print(f"  Unique targets rendered: {len(seen_targets_stage7)} / {len(targets)}")
    print(f"  Total markers rendered: {phoneos_res['total']}")
    print(f"  Confirmed markers: {phoneos_res['confirmedCount']}")
    print(f"  Provisional markers: {phoneos_res['provisionalCount']}")
    for mid, m in phoneos_res["markers"].items():
        print(f"    - [{m['lifecycle_state']}] ID: {mid}, Conf: {m['confidence']:.2f}, SeenBy: {m['seenBy']}")

    print("\n" + "=" * 80)
    print("STAGE-BY-STAGE COUNT TABLE:")
    print("=" * 80)
    print(f"| {'Stage':<6} | {'Pipeline Component':<40} | {'Unique Targets':<15} | {'Status':<10} |")
    print(f"|{'-'*8}|{'-'*42}|{'-'*17}|{'-'*12}|")
    stages = [
        ("0", "Spawned Ground Truth Targets", len(targets)),
        ("1", "YOLO Detections (DroneOS Instances)", len(seen_targets_stage1)),
        ("2", "Local TrackAggregator Output", len(seen_targets_stage2)),
        ("3", "UDP Observations Sent to GFS", len(seen_targets_stage3)),
        ("4", "GFS Inbox Ingestion & Stage B Hypothesis", len(seen_targets_stage4)),
        ("5", "GFS SQLite Database & Outbox Events", len(seen_targets_stage5)),
        ("6", "Relay Snapshot Response Serialization", len(seen_targets_stage6)),
        ("7", "PhoneOS Snapshot Reconciliation & Map Rendering", len(seen_targets_stage7)),
    ]
    for s_num, s_name, count in stages:
        status = "PASSED (5)" if count == 5 else f"DROPPED ({count})"
        print(f"| {s_num:<6} | {s_name:<40} | {count:<15} | {status:<10} |")
    print("=" * 80)


if __name__ == "__main__":
    run_pipeline_trace()
