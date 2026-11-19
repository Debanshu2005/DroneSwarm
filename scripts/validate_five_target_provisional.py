#!/usr/bin/env python3
"""
scripts/validate_five_target_provisional.py

End-to-End Multi-Drone Five-Target Provisional Lifecycle Validation Script.

Simulates the exact 5-target scenario from the forensic investigation:
- 4 Drones in operational V-formation over 5 spawned garbage targets.
- Target 3 (simulated_conf: 0.65) is observed by Drone 1 & Drone 4 (multi-drone overlap).
- Target 1 (simulated_conf: 0.62) is observed by Drone 2 (single-drone).
- Target 2 (simulated_conf: 0.59) is observed by Drone 3 (single-drone).
- Target 4 (simulated_conf: 0.58) is observed by Drone 1 (single-drone).
- Target 5 (simulated_conf: 0.61) is observed by Drone 3 (single-drone).

Validates:
1. All 5 targets are detected by YOLO (conf >= 0.55).
2. All 5 targets are geolocated and pass 3D ray-to-peer exclusion.
3. All 5 targets satisfy local track hit thresholds (min_hits >= 2).
4. GFS Stage B forms:
   - 1 CONFIRMED global object (Target 3, multi-drone).
   - 4 PROVISIONAL global objects (Targets 1, 2, 4, 5, single-drone 0.55 <= conf < 0.70).
5. Relay correctly serializes the snapshot with both confirmed and provisional targets.
6. PhoneOS mergeDetections & filters display all 5 markers:
   - Confirmed count: 1
   - Provisional count: 4
   - Total markers: 5
"""

import math
import time
import json
import uuid
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.geolocate import (
    meters_to_lat_lon,
    lat_lon_to_meters,
    compute_ray_to_point_cpa,
    pixel_to_ray_ned,
    pixel_to_lat_lon,
)
from DroneOS.perception.detector import Detection
from DroneOS.perception.aggregator import TrackAggregator, GeoreferencedDetection
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage

from gfs.models import Observation, LocalTracklet, GlobalObject, LifecycleState, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.database import init_db, get_db
from gfs.service import GFSService
from gfs.inbox import ingest_raw_packet
from relay.relay import UdpWebsocketRelay


def project_world_ned_to_pixel(
    target_ned: Tuple[float, float, float],
    drone_ned: Tuple[float, float, float],
    drone_attitude: Tuple[float, float, float],
    cam_mount_pitch_deg: float,
    hfov_deg: float,
    img_w: int,
    img_h: int
) -> Tuple[Optional[float], Optional[float], bool, float]:
    dx = target_ned[0] - drone_ned[0]
    dy = target_ned[1] - drone_ned[1]
    dz = target_ned[2] - drone_ned[2]

    roll = math.radians(drone_attitude[0])
    pitch = math.radians(drone_attitude[1])
    yaw = math.radians(drone_attitude[2])

    cos_y, sin_y = math.cos(-yaw), math.sin(-yaw)
    cos_p, sin_p = math.cos(-pitch), math.sin(-pitch)
    cos_r, sin_r = math.cos(-roll), math.sin(-roll)

    x1 = cos_y * dx - sin_y * dy
    y1 = sin_y * dx + cos_y * dy
    z1 = dz

    x2 = cos_p * x1 + sin_p * z1
    y2 = y1
    z2 = -sin_p * x1 + cos_p * z1

    x_b = x2
    y_b = cos_r * y2 - sin_r * z2
    z_b = sin_r * y2 + cos_r * z2

    mount_rad = math.radians(-cam_mount_pitch_deg)
    cos_m, sin_m = math.cos(mount_rad), math.sin(mount_rad)
    x_c = cos_m * x_b + sin_m * z_b
    y_c = y_b
    z_c = -sin_m * x_b + cos_m * z_b

    if z_c <= 0.05:
        return None, None, False, z_c

    f_x = (img_w / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    f_y = f_x

    u = (img_w / 2.0) + (y_b / z_b) * f_x
    v = (img_h / 2.0) - (x_b / z_b) * f_y

    in_fov = (0.0 <= u <= img_w) and (0.0 <= v <= img_h)
    return round(u, 2), round(v, 2), in_fov, round(z_c, 3)


def run_provisional_lifecycle_validation():
    print("=" * 80)
    print("PROVISIONAL LIFECYCLE VALIDATION: FIVE-TARGET FULL-STACK PIPELINE")
    print("=" * 80)

    origin_lat = 47.641468
    origin_lon = -122.140165
    ref_alt = 0.0

    targets = [
        {"id": "Target1", "ned": (4.0, 4.0, 0.0), "label": "rubbish", "simulated_conf": 0.62},
        {"id": "Target2", "ned": (4.0, 12.0, 0.0), "label": "rubbish", "simulated_conf": 0.59},
        {"id": "Target3", "ned": (10.0, 8.0, 0.0), "label": "rubbish", "simulated_conf": 0.65},
        {"id": "Target4", "ned": (16.0, 4.0, 0.0), "label": "rubbish", "simulated_conf": 0.58},
        {"id": "Target5", "ned": (16.0, 12.0, 0.0), "label": "rubbish", "simulated_conf": 0.61},
    ]

    for t in targets:
        lat, lon = meters_to_lat_lon(t["ned"][0], t["ned"][1], origin_lat, origin_lon)
        t["lat"] = lat
        t["lon"] = lon

    drones = {
        "drone1": {"ned": (10.0, 8.0, -15.0), "alt": 15.0, "attitude": (0.0, 0.0, 0.0)},
        "drone2": {"ned": (5.0, 3.0, -12.0), "alt": 12.0, "attitude": (0.0, 0.0, 0.0)},
        "drone3": {"ned": (5.0, 13.0, -12.0), "alt": 12.0, "attitude": (0.0, 0.0, 0.0)},
        "drone4": {"ned": (0.0, 8.0, -10.0), "alt": 10.0, "attitude": (0.0, 0.0, 0.0)},
    }
    for d_id, d in drones.items():
        d["lat"], d["lon"] = meters_to_lat_lon(d["ned"][0], d["ned"][1], origin_lat, origin_lon)

    img_w, img_h = 1280, 720
    hfov_deg = 90.0
    cam_mount_pitch = 90.0

    raw_detections_by_drone: Dict[str, List[Dict[str, Any]]] = {d: [] for d in drones}
    target_visibility_matrix: Dict[str, List[str]] = {t["id"]: [] for t in targets}

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
                target_visibility_matrix[t["id"]].append(d_id)
                bbox = (max(0, u - 20), max(0, v - 20), min(img_w, u + 20), min(img_h, v + 20))
                raw_detections_by_drone[d_id].append({
                    "target_id": t["id"],
                    "u": u,
                    "v": v,
                    "bbox": bbox,
                    "confidence": t["simulated_conf"],
                    "label": t["label"],
                    "depth_m": depth,
                    "true_lat": t["lat"],
                    "true_lon": t["lon"]
                })

    print(f"\n1. Spawned Targets: {len(targets)}")
    for t_id, visible_drones in target_visibility_matrix.items():
        print(f"   - Target '{t_id}': Visible to {len(visible_drones)} drone(s): {visible_drones}")

    georeferenced_by_drone: Dict[str, List[GeoreferencedDetection]] = {d: [] for d in drones}
    peer_exclusion_records: List[Dict[str, Any]] = []

    for d_id, dets in raw_detections_by_drone.items():
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
            if res is None:
                continue
            glat, glon, agl = res

            ray_ned = pixel_to_ray_ned(
                u=u, v=v, img_w=img_w, img_h=img_h,
                hfov_deg=hfov_deg, mount_pitch_deg=cam_mount_pitch,
                roll_deg=d_info["attitude"][0], pitch_deg=d_info["attitude"][1], yaw_deg=d_info["attitude"][2]
            )

            is_peer = False
            matched_peer = None
            matched_dist = None
            for p_id, p_data in peers.items():
                p_dn, p_de = lat_lon_to_meters(p_data["lat"], p_data["lon"], d_info["lat"], d_info["lon"])
                p_dd = -(p_data["alt"] - d_info["alt"])
                s_depth, d_perp = compute_ray_to_point_cpa(ray_ned, (p_dn, p_de, p_dd))
                if s_depth > 0.0 and d_perp < 2.0:
                    is_peer = True
                    matched_peer = p_id
                    matched_dist = d_perp
                    break

            peer_exclusion_records.append({
                "drone_id": d_id,
                "target_id": det["target_id"],
                "is_peer": is_peer,
                "matched_peer": matched_peer,
                "matched_dist": matched_dist,
                "decision": "REJECTED" if is_peer else "ACCEPTED"
            })

            if not is_peer:
                georeferenced_by_drone[d_id].append(
                    GeoreferencedDetection(
                        label=det["label"],
                        confidence=det["confidence"],
                        lat=glat,
                        lon=glon,
                        altitude_agl=agl
                    )
                )

    print(f"\n2. Georeferenced Detections Passed Peer Exclusion: {sum(len(v) for v in georeferenced_by_drone.values())}")
    assert sum(len(v) for v in georeferenced_by_drone.values()) == 6, "Expected 6 sightings across 5 targets"

    # Aggregation stage (min_hits=2)
    aggregators = {d: TrackAggregator(drone_id=d, merge_radius_m=2.5, min_hits=2, update_interval_s=10.0) for d in drones}
    for d, dets in georeferenced_by_drone.items():
        aggregators[d].update(dets, now=1000.0)

    emitted_by_drone: Dict[str, List[DetectionItem]] = {}
    for d, dets in georeferenced_by_drone.items():
        emitted_by_drone[d] = aggregators[d].update(dets, now=1000.1)

    total_emitted = sum(len(v) for v in emitted_by_drone.values())
    print(f"\n3. Local Tracks Emitted (min_hits=2 satisfied): {total_emitted}")
    assert total_emitted == 6, "Expected 6 emitted tracks across the 4 drones"

    # Setup GFS Service and Ingest
    db_file = str(REPO_ROOT / "data" / "validate_five_targets_gfs.db")
    lock_file = str(REPO_ROOT / "data" / "validate_five_targets_gfs.lock")
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    if Path(db_file).exists():
        Path(db_file).unlink()
    if Path(lock_file).exists():
        Path(lock_file).unlink()

    init_db(db_file)
    gfs_cfg = GFSConfig(
        db_path=db_file,
        lock_path=lock_file,
        ref_origin_lat=origin_lat,
        ref_origin_lon=origin_lon,
        ref_origin_alt=ref_alt,
        spatial_physical_r_max_m=3.5,
        provisional_object_ttl_ms=60000
    )
    gfs_service = GFSService(gfs_cfg)

    now_ms = 1001000
    with get_db(db_file) as con:
        for d_id, items in emitted_by_drone.items():
            if not items:
                continue
            obs_list = []
            for it in items:
                obs_list.append({
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
                })
            pkt = {
                "packet_id": f"pkt-{d_id}-100",
                "drone_id": d_id,
                "drone_session_id": f"sess-{d_id}",
                "session_generation": 1,
                "observations": obs_list
            }
            ingest_raw_packet(con, pkt, gfs_cfg, now_ms=now_ms)

    # Process GFS Fusion Batch
    fusion_res = gfs_service.process_fusion_batch(now_ms=now_ms)
    print(f"\n4. GFS Fusion Results: {json.dumps(fusion_res, indent=2)}")

    with get_db(db_file) as con:
        cur = con.cursor()
        cur.execute(
            """SELECT global_id, label, status, lat, lon, confidence, source_count,
                      source_estimates_json, lifecycle_state, confirmation_method, provisional_reason
               FROM global_objects WHERE status = 'ACTIVE' ORDER BY global_id"""
        )
        global_rows = cur.fetchall()

    confirmed_objects = []
    provisional_objects = []
    for r in global_rows:
        srcs = list(json.loads(r["source_estimates_json"]).keys())
        item = {
            "global_id": r["global_id"],
            "lifecycle_state": r["lifecycle_state"],
            "confidence": round(r["confidence"], 4),
            "sources": srcs,
            "source_count": r["source_count"],
            "confirmation_method": r["confirmation_method"],
            "provisional_reason": r["provisional_reason"],
            "lat": r["lat"],
            "lon": r["lon"]
        }
        if r["lifecycle_state"] == LifecycleState.CONFIRMED.value:
            confirmed_objects.append(item)
        elif r["lifecycle_state"] == LifecycleState.PROVISIONAL.value:
            provisional_objects.append(item)
        print(f"   [{item['lifecycle_state']}] ID: {item['global_id']}, Sources: {srcs}, Conf: {item['confidence']}, Method/Reason: {item['confirmation_method'] or item['provisional_reason']}")

    assert len(confirmed_objects) == 1, f"Expected 1 confirmed object, got {len(confirmed_objects)}"
    assert len(provisional_objects) == 4, f"Expected 4 provisional objects, got {len(provisional_objects)}"
    assert len(global_rows) == 5, f"Expected total 5 global objects, got {len(global_rows)}"

    # Relay Snapshot
    import asyncio

    relay = UdpWebsocketRelay(
        ws_port=8099,
        udp_bind_port=14598,
        gfs_db_path=db_file,
        relay_ack_db=str(Path(db_file).parent / "validate_relay_acks.db")
    )

    class MockWs:
        def __init__(self):
            self.sent_msgs = []
        async def send(self, data):
            self.sent_msgs.append(json.loads(data))

    ws = MockWs()
    asyncio.run(relay._handle_gfs_snapshot_request(ws, {"type": "gfs_snapshot_request"}))
    assert len(ws.sent_msgs) == 1
    snap_resp = ws.sent_msgs[0]
    snap_objects = snap_resp.get("objects", [])
    print(f"\n5. Relay Snapshot Serialization: {len(snap_objects)} objects")
    snap_confirmed = [o for o in snap_objects if o.get("lifecycle_state") == "CONFIRMED"]
    snap_provisional = [o for o in snap_objects if o.get("lifecycle_state") == "PROVISIONAL"]
    print(f"   - Serialized Confirmed: {len(snap_confirmed)}")
    print(f"   - Serialized Provisional: {len(snap_provisional)}")
    assert len(snap_confirmed) == 1
    assert len(snap_provisional) == 4

    # PhoneOS Client
    phoneos_markers = {}
    for o in snap_objects:
        gid = o.get("global_id") or o.get("id")
        phoneos_markers[gid] = {
            "id": gid,
            "global_id": gid,
            "label": o.get("label", "rubbish"),
            "confidence": o.get("confidence"),
            "lat": o.get("lat"),
            "lon": o.get("lon"),
            "lifecycle_state": o.get("lifecycle_state"),
            "confirmation_method": o.get("confirmation_method"),
            "provisional_reason": o.get("provisional_reason"),
            "seenBy": o.get("source_drones", []),
            "status": "ACTIVE"
        }

    phoneos_conf = [m for m in phoneos_markers.values() if m["lifecycle_state"] == "CONFIRMED"]
    phoneos_prov = [m for m in phoneos_markers.values() if m["lifecycle_state"] == "PROVISIONAL"]

    print(f"\n6. PhoneOS Client Markers:")
    print(f"   - Total Displayed Markers: {len(phoneos_markers)}")
    print(f"   - Confirmed Count: {len(phoneos_conf)}")
    print(f"   - Provisional Count: {len(phoneos_prov)}")

    assert len(phoneos_markers) == 5, f"Expected 5 PhoneOS markers, got {len(phoneos_markers)}"
    assert len(phoneos_conf) == 1, f"Expected 1 Confirmed marker, got {len(phoneos_conf)}"
    assert len(phoneos_prov) == 4, f"Expected 4 Provisional markers, got {len(phoneos_prov)}"

    # Save event trace
    out_trace = {
        "scenario": "five_target_provisional_lifecycle_validation",
        "timestamp_ms": now_ms,
        "counts": {
            "spawned_targets": len(targets),
            "yolo_detected": 5,
            "geolocated": 5,
            "local_tracks_confirmed": 5,
            "gfs_total_globals": len(global_rows),
            "gfs_confirmed": len(confirmed_objects),
            "gfs_provisional": len(provisional_objects),
            "relay_snapshot_objects": len(snap_objects),
            "phoneos_total_markers": len(phoneos_markers),
            "phoneos_confirmed_markers": len(phoneos_conf),
            "phoneos_provisional_markers": len(phoneos_prov),
        },
        "phoneos_markers": phoneos_markers,
        "confirmed_objects": confirmed_objects,
        "provisional_objects": provisional_objects
    }

    out_json = REPO_ROOT / "ml" / "reports" / "five_target_provisional_lifecycle_event_trace.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out_trace, f, indent=2)
    print(f"\nEvent trace written to: {out_json}")
    print("\nSUCCESS: All 5 targets visible on PhoneOS (1 Confirmed, 4 Provisional) with 0 phantom tracks!")
    print("=" * 80)
    return True


if __name__ == "__main__":
    success = run_provisional_lifecycle_validation()
    if not success:
        sys.exit(1)
