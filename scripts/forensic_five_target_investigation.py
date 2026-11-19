#!/usr/bin/env python3
"""
scripts/forensic_five_target_investigation.py

Forensic investigation script for:
Five Spawned Garbage Targets, Only One PhoneOS Marker.

Instruments every stage:
1. Target Spawning & Ground Truth
2. Multi-Drone Formation Geometric Coverage & Camera FOV Unprojection
3. Raw Detection & Confidence Evaluation
4. Geolocation Projection & Attitude Rotation
5. 3D Ray-to-Peer CPA Exclusion Filter
6. Local Track Aggregation & Hit Gating (min_hits=2)
7. Emitted Observation Network Payload
8. GFS Inbox Ingestion & Tracklet Construction
9. GFS Stage A Association (Existing Globals)
10. GFS Stage B Hypothesis Formation (Clique Acceptance & Deferral Policies)
11. Relay Broadcast & WebSocket Delivery
12. PhoneOS mergeDetections & Snapshot Reconciliation
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

from gfs.models import Observation, LocalTracklet, GlobalObject, wgs84_to_enu
from gfs.config import GFSConfig
from gfs.association import build_local_tracklets, associate_drone_tracklets_to_globals
from gfs.hypothesis import form_new_global_objects, tracklets_are_compatible


def project_world_ned_to_pixel(
    target_ned: Tuple[float, float, float],
    drone_ned: Tuple[float, float, float],
    drone_attitude: Tuple[float, float, float], # roll, pitch, yaw in deg
    cam_mount_pitch_deg: float, # 90 = nadir
    hfov_deg: float,
    img_w: int,
    img_h: int
) -> Tuple[Optional[float], Optional[float], bool, float]:
    """
    Projects world NED target coordinate to camera pixel coordinates (u, v).
    Returns (u, v, in_fov, along_ray_depth).
    """
    dx = target_ned[0] - drone_ned[0]
    dy = target_ned[1] - drone_ned[1]
    dz = target_ned[2] - drone_ned[2] # dz is positive downward in NED

    roll = math.radians(drone_attitude[0])
    pitch = math.radians(drone_attitude[1])
    yaw = math.radians(drone_attitude[2])

    # R_ned_to_body = Rx(-roll) * Ry(-pitch) * Rz(-yaw)
    cos_y, sin_y = math.cos(-yaw), math.sin(-yaw)
    cos_p, sin_p = math.cos(-pitch), math.sin(-pitch)
    cos_r, sin_r = math.cos(-roll), math.sin(-roll)

    # Rotate by yaw
    x1 = cos_y * dx - sin_y * dy
    y1 = sin_y * dx + cos_y * dy
    z1 = dz

    # Rotate by pitch
    x2 = cos_p * x1 + sin_p * z1
    y2 = y1
    z2 = -sin_p * x1 + cos_p * z1

    # Rotate by roll
    x_b = x2
    y_b = cos_r * y2 - sin_r * z2
    z_b = sin_r * y2 + cos_r * z2

    # Camera mount: nadir pitch down by mount_pitch_deg (90 deg)
    # Ry(mount_pitch)
    mount_rad = math.radians(-cam_mount_pitch_deg)
    cos_m, sin_m = math.cos(mount_rad), math.sin(mount_rad)
    x_c = cos_m * x_b + sin_m * z_b
    y_c = y_b
    z_c = -sin_m * x_b + cos_m * z_b

    # In camera frame: z_c is forward along optical axis, x_c is right, y_c is down
    if z_c <= 0.05:
        return None, None, False, z_c

    f_x = (img_w / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    f_y = f_x # square pixels

    # Note camera frame mapping:
    # Body: X forward, Y right, Z down.
    # Nadir Mount (Ry(-90 deg)):
    # optical Z_cam is along body +Z (Downwards)
    # optical X_cam is along body +Y (Rightwards/East)
    # optical Y_cam is along body -X (Backwards/South)
    # Therefore u is along Y_body, v is along -X_body
    u = (img_w / 2.0) + (y_b / z_b) * f_x
    v = (img_h / 2.0) - (x_b / z_b) * f_y

    in_fov = (0.0 <= u <= img_w) and (0.0 <= v <= img_h)
    return round(u, 2), round(v, 2), in_fov, round(z_c, 3)


def run_forensic_investigation():
    print("=" * 80)
    print("FORENSIC INVESTIGATION: 5 SPAWNED TARGETS -> 1 PHONEOS MARKER")
    print("=" * 80)

    origin_lat = 47.641468
    origin_lon = -122.140165
    ref_alt = 0.0

    # 1. Controlled Targets Setup
    # 5 targets with known, well-separated coordinates (>= 6m separation)
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

    # 2. Swarm Drone Positions in Operational V-Formation
    # Drone 1 (Leader/Anchor) at (10.0, 8.0, alt=15m)
    # Drone 2 at (5.0, 3.0, alt=12m)
    # Drone 3 at (5.0, 13.0, alt=12m)
    # Drone 4 at (0.0, 8.0, alt=10m)
    drones = {
        "drone1": {"ned": (10.0, 8.0, -15.0), "alt": 15.0, "attitude": (0.0, 0.0, 0.0)},
        "drone2": {"ned": (5.0, 3.0, -12.0), "alt": 12.0, "attitude": (0.0, 0.0, 0.0)},
        "drone3": {"ned": (5.0, 13.0, -12.0), "alt": 12.0, "attitude": (0.0, 0.0, 0.0)},
        "drone4": {"ned": (0.0, 8.0, -10.0), "alt": 10.0, "attitude": (0.0, 0.0, 0.0)},
    }
    for d_id, d in drones.items():
        d["lat"], d["lon"] = meters_to_lat_lon(d["ned"][0], d["ned"][1], origin_lat, origin_lon)

    print("\n--- STAGE 1 & 2: FRUSTUM COVERAGE & RAW DETECTION ---")
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
                # Form bounding box around projected pixel
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

    for t_id, visible_drones in target_visibility_matrix.items():
        print(f"Target '{t_id}': Visible to {len(visible_drones)} drone(s): {visible_drones}")

    for d_id, dets in raw_detections_by_drone.items():
        print(f"Drone '{d_id}': {len(dets)} target(s) in FOV: {[d['target_id'] for d in dets]}")

    print("\n--- STAGE 3 & 4: GEOLOCATION & 3D PEER EXCLUSION ---")
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

            # Check 3D CPA against each peer
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

    print(f"Total Georeferenced Detections passed peer-exclusion: {sum(len(v) for v in georeferenced_by_drone.values())}")
    for rec in peer_exclusion_records:
        if rec["is_peer"]:
            print(f"  -> EXCLUDED: {rec['drone_id']} seeing {rec['target_id']} intercepted peer {rec['matched_peer']} (d={rec['matched_dist']:.2f}m)")

    print("\n--- STAGE 5 & 6: LOCAL TRACK AGGREGATION (min_hits=2) ---")
    aggregators = {d: TrackAggregator(drone_id=d, merge_radius_m=2.5, min_hits=2, update_interval_s=10.0) for d in drones}
    emitted_by_drone: Dict[str, List[Any]] = {d: [] for d in drones}

    # Step 1: First observation (hits = 1) -> must NOT emit because min_hits = 2
    step1_emitted = {}
    for d_id, geo_list in georeferenced_by_drone.items():
        step1_emitted[d_id] = aggregators[d_id].update(geo_list, now=1000.0)

    # Step 2: Second observation (hits = 2) -> confirms tracks and emits "new"
    for d_id, geo_list in georeferenced_by_drone.items():
        emitted_by_drone[d_id] = aggregators[d_id].update(geo_list, now=1001.0)

    print(f"Aggregator Step 1 (hits=1) total emitted items: {sum(len(v) for v in step1_emitted.values())} (Guards noise!)")
    print(f"Aggregator Step 2 (hits=2) total emitted items: {sum(len(v) for v in emitted_by_drone.values())}")
    for d_id, items in emitted_by_drone.items():
        print(f"  Drone '{d_id}': emitted {len(items)} tracks: {[it.id for it in items]}")

    print("\n--- STAGE 7, 8, 9: GFS INBOX, TRACKLETS & HYPOTHESIS FORMATION ---")
    gfs_cfg = GFSConfig(
        ref_origin_lat=origin_lat,
        ref_origin_lon=origin_lon,
        ref_origin_alt=ref_alt,
        spatial_physical_r_max_m=3.5,
        spatial_mahalanobis_thresh=9.21
    )

    # Convert emitted tracks into GFS Observations
    gfs_observations: List[Observation] = []
    obs_seq = 0
    for d_id, items in emitted_by_drone.items():
        for it in items:
            obs_seq += 1
            e, n, u = wgs84_to_enu(it.lat, it.lon, it.altitude_agl or 0.0, origin_lat, origin_lon, ref_alt)
            obs = Observation(
                obs_id=f"obs-{d_id}-{obs_seq:04d}",
                drone_id=d_id,
                drone_session_id=f"sess-{d_id}",
                session_generation=1,
                frame_id=100,
                local_track_id=it.id,
                capture_timestamp_ms=1001000,
                lat=it.lat,
                lon=it.lon,
                alt=it.altitude_agl or 0.0,
                cov_xx=0.35,
                cov_yy=0.35,
                cov_xy=0.01,
                label=it.label,
                confidence=it.confidence,
                payload_hash=f"hash-{obs_seq}",
                raw_payload="{}"
            )
            obs.east_m = e
            obs.north_m = n
            obs.up_m = u
            gfs_observations.append(obs)

    print(f"Total GFS Claimed Observations: {len(gfs_observations)}")
    local_tracklets = build_local_tracklets(gfs_observations)
    print(f"Total Constructed Local Tracklets: {len(local_tracklets)}")

    # Stage A: Associate to existing (initially empty)
    unmatched_tracklets = local_tracklets

    # Stage B: Form new global objects
    new_globals, ambiguous_deferred, low_confidence_deferred = form_new_global_objects(
        unmatched_tracklets=unmatched_tracklets,
        config=gfs_cfg,
        ref_lat=origin_lat,
        ref_lon=origin_lon,
        ref_alt=ref_alt
    )

    print(f"GFS Stage B Results:")
    print(f"  -> New Global Objects Created: {len(new_globals)}")
    for g in new_globals:
        print(f"     * [{g.global_id}] sources={list(g.source_estimates.keys())}, conf={g.confidence:.4f}, ENU=({g.east_m:.2f}, {g.north_m:.2f})")
    print(f"  -> Low Confidence Deferred: {len(low_confidence_deferred)}")
    for t in low_confidence_deferred:
        print(f"     * [DEFERRED_LOW_CONF] Drone={t.drone_id}, track={t.local_track_id}, conf={t.confidence:.2f}, ENU=({t.east_m:.2f}, {t.north_m:.2f})")
    print(f"  -> Ambiguous Clique Deferred: {len(ambiguous_deferred)}")

    print("\n--- STAGE 10: PHONEOS GCS CLIENT MERGE & ACTIVE MARKERS ---")
    # Simulate PhoneOS mergeDetections
    # PhoneOS receives either GFS Global Objects OR direct local tracks.
    # In authoritative GFS mode, PhoneOS displays GFS confirmed global objects.
    phoneos_markers = {}
    for g in new_globals:
        phoneos_markers[g.global_id] = {
            "id": g.global_id,
            "label": g.label,
            "confidence": g.confidence,
            "lat": g.lat,
            "lon": g.lon,
            "seenBy": list(g.source_estimates.keys()),
            "status": "ACTIVE"
        }

    print(f"PhoneOS Final Active Markers: {len(phoneos_markers)}")
    for mid, mdata in phoneos_markers.items():
        print(f"  - Marker '{mid}': Seen by {mdata['seenBy']}, Conf={mdata['confidence']:.2f}, GPS=({mdata['lat']:.6f}, {mdata['lon']:.6f})")

    # Compile Full Trace and Lifecycle Table
    lifecycle_table = []
    for t in targets:
        # Check YOLO
        yolo_seen = target_visibility_matrix[t["id"]]
        # Check Geolocated
        geolocated = [rec["drone_id"] for rec in peer_exclusion_records if rec["target_id"] == t["id"] and not rec["is_peer"]]
        # Check Local Track
        local_tracks = []
        for d in geolocated:
            for it in emitted_by_drone[d]:
                # find if this track corresponds to target
                dn, de = lat_lon_to_meters(it.lat, it.lon, t["lat"], t["lon"])
                if math.hypot(dn, de) < 2.0:
                    local_tracks.append(f"{d}:{it.id}")

        # Check GFS
        gfs_status = "NONE"
        global_id_match = None
        for g in new_globals:
            dn, de = lat_lon_to_meters(g.lat, g.lon, t["lat"], t["lon"])
            if math.hypot(dn, de) < 2.5:
                gfs_status = "GLOBAL_OBJECT"
                global_id_match = g.global_id
                break
        if gfs_status == "NONE":
            # Check if in deferred
            for d_item in low_confidence_deferred:
                dn, de = lat_lon_to_meters(d_item.lat, d_item.lon, t["lat"], t["lon"])
                if math.hypot(dn, de) < 2.5:
                    gfs_status = "DEFERRED_LOW_CONF"
                    break

        # Check PhoneOS
        phoneos_status = global_id_match if global_id_match and global_id_match in phoneos_markers else "MISSING"

        lifecycle_table.append({
            "target": t["id"],
            "yolo_drones": yolo_seen,
            "geolocated_drones": geolocated,
            "local_tracks": local_tracks,
            "gfs_status": gfs_status,
            "gfs_global_id": global_id_match,
            "phoneos_status": phoneos_status,
            "root_cause": (
                "CONFIRMED_GLOBAL" if phoneos_status != "MISSING"
                else "OUTSIDE_FOV" if not yolo_seen
                else "GFS_SINGLE_DRONE_LOW_CONFIDENCE_DEFERRED" if gfs_status == "DEFERRED_LOW_CONF"
                else "UNKNOWN"
            )
        })

    trace_output = {
        "targets": targets,
        "drones": drones,
        "lifecycle_table": lifecycle_table,
        "counts": {
            "spawned_targets": len(targets),
            "yolo_detected_targets": len([t for t in lifecycle_table if t["yolo_drones"]]),
            "geolocated_targets": len([t for t in lifecycle_table if t["geolocated_drones"]]),
            "locally_tracked_targets": len([t for t in lifecycle_table if t["local_tracks"]]),
            "gfs_global_targets": len(new_globals),
            "phoneos_markers": len(phoneos_markers),
        }
    }

    out_json = REPO_ROOT / "ml" / "reports" / "five_target_forensic_event_trace.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(trace_output, f, indent=2)
    print(f"\nTrace saved to {out_json}")

    return trace_output


if __name__ == "__main__":
    run_forensic_investigation()
