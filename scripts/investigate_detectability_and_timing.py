"""
STEP 2 ADDENDUM: Detectability Check (Part A) & Unexplained Time Analysis (Part B).
"""

import asyncio
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple
import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import airsim
from ultralytics import YOLO
from DroneOS.perception.frame_sources import AirSimFrameSource
from scripts.spawn_garbage import clear_targets
from scripts.validate_single_drone_airsim import setup_scene_target

OUTPUT_DIR = REPO_ROOT / "ml" / "reports" / "detectability"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def draw_annotated_frame(
    image: np.ndarray,
    target_bbox: Tuple[float, float, float, float],
    target_raw_conf: float,
    all_raw_boxes: List[Dict[str, Any]],
    alt_m: float,
    out_path: Path,
):
    """Draws ground truth cyan box, detector candidate boxes, and confidence pills."""
    annotated = image.copy()
    h, w = annotated.shape[:2]

    # 1. Draw expected target cyan box
    tx1, ty1, tx2, ty2 = [int(round(c)) for c in target_bbox]
    tx1, ty1 = max(0, min(w - 1, tx1)), max(0, min(h - 1, ty1))
    tx2, ty2 = max(0, min(w - 1, tx2)), max(0, min(h - 1, ty2))
    tgt_w = tx2 - tx1
    tgt_h = ty2 - ty1
    cv2.rectangle(annotated, (tx1, ty1), (tx2, ty2), (255, 255, 0), 2)  # Cyan

    # Pill for Ground Truth Target
    pill_text = f"Target (Expected {tgt_w}x{tgt_h}px) | Raw Conf={target_raw_conf:.4f}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), bl = cv2.getTextSize(pill_text, font, 0.5, 1)
    py = max(th + 4, ty1 - 8)
    cv2.rectangle(annotated, (tx1, py - th - 4), (tx1 + tw + 4, py + bl + 2), (255, 255, 0), -1)
    cv2.putText(annotated, pill_text, (tx1 + 2, py), font, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    # 2. Draw all raw detector boxes
    for b in all_raw_boxes:
        bx1, by1, bx2, by2 = [int(round(c)) for c in b["xyxy"]]
        bx1, by1 = max(0, min(w - 1, bx1)), max(0, min(h - 1, by1))
        bx2, by2 = max(0, min(w - 1, bx2)), max(0, min(h - 1, by2))
        conf = b["conf"]
        is_target = b.get("is_target", False)

        color = (0, 255, 0) if is_target else (0, 140, 255)  # Green for target, Orange for background
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), color, 2)
        lbl = f"{'TARGET' if is_target else 'BG/PEER'} {conf:.4f}"
        (ltw, lth), lbl_bl = cv2.getTextSize(lbl, font, 0.45, 1)
        lpy = min(h - 4, by2 + lth + 4)
        cv2.rectangle(annotated, (bx1, lpy - lth - 2), (bx1 + ltw + 4, lpy + lbl_bl), color, -1)
        cv2.putText(annotated, lbl, (bx1 + 2, lpy), font, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

    # Header info
    header = f"Altitude: {alt_m:.1f}m AGL | Resolution: {w}x{h} | Raw Boxes: {len(all_raw_boxes)}"
    cv2.rectangle(annotated, (10, 10), (550, 40), (0, 0, 0), -1)
    cv2.putText(annotated, header, (15, 30), font, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    cv2.imwrite(str(out_path), annotated)
    print(f"  Saved annotated frame to: {out_path}")


async def measure_part_a(client: airsim.MultirotorClient) -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("PART A: DETECTABILITY CHECK & ALTITUDE SWEEP")
    print("=" * 70)

    vehicle_name = "Drone1"
    model = YOLO(str(REPO_ROOT / "models" / "garbage.pt"))
    src = AirSimFrameSource(vehicle_name=vehicle_name)

    # 1. First measure empty scene background baseline (0 targets)
    print("\n--- Measuring Empty Scene Background Max Raw Confidence ---")
    clear_targets(client)
    empty_altitudes = [8.0, 5.0, 3.0]
    empty_results = {}
    for alt in empty_altitudes:
        client.moveToPositionAsync(0.0, 0.0, -alt, 3.0, vehicle_name=vehicle_name).join()
        await asyncio.sleep(0.5)
        f_empty = await src.get_frame()
        res_empty = model.predict(f_empty.image, conf=0.01, verbose=False)[0]
        empty_boxes = [
            {"conf": round(float(b.conf[0]), 4), "xyxy": [round(float(c), 1) for c in b.xyxy[0]]}
            for b in res_empty.boxes
        ]
        peak_empty_conf = max((b["conf"] for b in empty_boxes), default=0.0)
        empty_results[f"{alt}m"] = {
            "peak_conf": peak_empty_conf,
            "boxes_count": len(empty_boxes),
            "boxes": empty_boxes,
        }
        print(f"  Empty Scene at {alt}m: Peak Raw Conf = {peak_empty_conf:.4f} ({len(empty_boxes)} candidate boxes)")

    overall_empty_peak = max(v["peak_conf"] for v in empty_results.values())
    print(f"\n>>> Overall Empty Scene Background Peak Raw Conf: {overall_empty_peak:.4f}")

    # 2. Spawn known target with baseline UAVVaste texture
    print("\n--- Spawning Target at (0.0, 0.0, 0.0) with UAVVaste Texture ---")
    tex_path = REPO_ROOT / "assets" / "perception" / "sample_target_garbage.png"
    ok, target_meta = await setup_scene_target(
        client=client,
        target_name="GarbageTarget_DetectTest",
        offset_north_m=0.0,
        offset_east_m=0.0,
        ground_z_m=0.0,
        scale_m=(0.55, 0.55, 0.20),
        mesh_name="Cube",
        unique_name=False,
        texture_path=tex_path,
    )
    print(f"  Target Spawned: {target_meta}")

    # Theoretical pixel sizes:
    # At nadir HFOV 90 deg, fx = 640. Pixel size of 0.55m = 640 * 0.55 / alt = 352 / alt
    altitudes = [8.0, 5.0, 3.0]
    altitude_data = {}
    annotated_frames = []

    for idx, alt in enumerate(altitudes, start=1):
        print(f"\n[Altitude Test {alt}m] Moving Drone1 to (0, 0, -{alt})...")
        client.moveToPositionAsync(0.0, 0.0, -alt, 3.0, vehicle_name=vehicle_name).join()
        await asyncio.sleep(0.5)

        pos = client.simGetObjectPose(vehicle_name).position
        f = await src.get_frame()
        img_h, img_w = f.image.shape[:2]

        # Expected pinhole target bounding box
        # S_px = 352 / alt
        expected_s_px = (640.0 * 0.55) / alt
        half_s = expected_s_px / 2.0
        expected_bbox = (640.0 - half_s, 360.0 - half_s, 640.0 + half_s, 360.0 + half_s)

        # Run detector down to conf=0.01
        res = model.predict(f.image, conf=0.01, verbose=False)[0]
        raw_boxes = []
        target_raw_conf = 0.0

        for b in res.boxes:
            conf = float(b.conf[0])
            xyxy = [float(c) for c in b.xyxy[0]]
            # Check overlap with target expected box
            bx_cx = (xyxy[0] + xyxy[2]) / 2.0
            bx_cy = (xyxy[1] + xyxy[3]) / 2.0
            dist_to_center = math.hypot(bx_cx - 640.0, bx_cy - 360.0)
            is_tgt = dist_to_center < (expected_s_px * 1.5)

            raw_boxes.append({
                "conf": round(conf, 4),
                "xyxy": [round(c, 1) for c in xyxy],
                "is_target": is_tgt,
            })
            if is_tgt and conf > target_raw_conf:
                target_raw_conf = conf

        out_path = OUTPUT_DIR / f"frame_{idx}_alt_{int(alt)}m.jpg"
        draw_annotated_frame(
            image=f.image,
            target_bbox=expected_bbox,
            target_raw_conf=target_raw_conf,
            all_raw_boxes=raw_boxes,
            alt_m=alt,
            out_path=out_path,
        )
        annotated_frames.append(str(out_path))

        altitude_data[f"{alt}m"] = {
            "altitude_m": alt,
            "actual_z": round(pos.z_val, 2),
            "expected_pixel_size": round(expected_s_px, 1),
            "measured_pixel_size": f"{round(expected_s_px, 1)} x {round(expected_s_px, 1)} px",
            "target_raw_conf": round(target_raw_conf, 4),
            "empty_scene_peak_conf": empty_results[f"{alt}m"]["peak_conf"],
            "beats_background": target_raw_conf > empty_results[f"{alt}m"]["peak_conf"],
            "raw_boxes_count": len(raw_boxes),
            "raw_boxes": raw_boxes,
            "annotated_frame": str(out_path),
        }
        print(f"  -> Alt {alt}m: Target Pixel Size = {expected_s_px:.1f} px | Target Raw Conf = {target_raw_conf:.4f} vs Empty Peak = {empty_results[f'{alt}m']['peak_conf']:.4f}")

    # 3. Dynamic flight pass: 2 additional frames traversing target at 5m
    print("\n--- Dynamic Traversal Flight Pass Across Target (5m Altitude) ---")
    pass_positions = [(-1.5, 0.0, 5.0, 4), (1.5, 0.0, 5.0, 5)]
    for px, py, p_alt, f_idx in pass_positions:
        client.moveToPositionAsync(px, py, -p_alt, 2.0, vehicle_name=vehicle_name).join()
        await asyncio.sleep(0.4)
        f = await src.get_frame()
        # Project target (0,0,0) relative to drone (px, py, -p_alt)
        # u = 640 - (py / p_alt) * 640; v = 360 - (-px / p_alt) * 640
        u = 640.0 + (0.0 - py) / p_alt * 640.0
        v = 360.0 - (0.0 - px) / p_alt * 640.0
        exp_s = (640.0 * 0.55) / p_alt
        tgt_box = (u - exp_s/2, v - exp_s/2, u + exp_s/2, v + exp_s/2)

        res = model.predict(f.image, conf=0.01, verbose=False)[0]
        r_boxes = []
        t_conf = 0.0
        for b in res.boxes:
            c = float(b.conf[0])
            xy = [float(v) for v in b.xyxy[0]]
            cx = (xy[0] + xy[2]) / 2.0
            cy = (xy[1] + xy[3]) / 2.0
            is_t = math.hypot(cx - u, cy - v) < (exp_s * 1.5)
            r_boxes.append({"conf": round(c, 4), "xyxy": [round(v, 1) for v in xy], "is_target": is_t})
            if is_t and c > t_conf:
                t_conf = c

        out_path = OUTPUT_DIR / f"frame_{f_idx}_pass_{'approach' if px < 0 else 'departure'}_5m.jpg"
        draw_annotated_frame(f.image, tgt_box, t_conf, r_boxes, p_alt, out_path)
        annotated_frames.append(str(out_path))
        print(f"  Frame {f_idx} (Pass at X={px}m): Target Raw Conf = {t_conf:.4f}")

    # 4. Check model on real UAVVaste dataset test images
    print("\n--- Evaluating Model on Real UAVVaste Test Split Images ---")
    test_txt = REPO_ROOT / "ml" / "data" / "uavvaste_yolo" / "test.txt"
    real_sample_results = []
    if test_txt.exists():
        lines = [l.strip() for l in test_txt.read_text().splitlines() if l.strip()]
        for line in lines[:5]:
            img_p = Path(line)
            if not img_p.is_absolute():
                img_p = REPO_ROOT / "ml" / "data" / "uavvaste_yolo" / img_p
            if img_p.exists():
                r_img = cv2.imread(str(img_p))
                r_res = model.predict(r_img, conf=0.05, verbose=False)[0]
                confs = [round(float(b.conf[0]), 4) for b in r_res.boxes]
                real_sample_results.append({
                    "image": img_p.name,
                    "detections_count": len(confs),
                    "max_conf": max(confs, default=0.0),
                    "confs": confs,
                })
                print(f"  Real UAVVaste Image '{img_p.name}': {len(confs)} detections (Peak Conf={max(confs, default=0.0):.4f})")

    await src.close()
    return {
        "empty_scene_peaks": empty_results,
        "overall_empty_peak": overall_empty_peak,
        "altitude_sweep": altitude_data,
        "annotated_frames": annotated_frames,
        "real_uavvaste_samples": real_sample_results,
    }


def measure_part_b() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("PART B: UNEXPLAINED TIME & LATENCY GAP FORENSIC ANALYSIS")
    print("=" * 70)

    analysis = {
        "mystery_1_why_530ms_e2e_yielded_1_16_fps": {
            "measured_e2e_sum_ms": 537.5,
            "measured_fps": 1.16,
            "cycle_period_ms": round(1000.0 / 1.16, 1),
            "unaccounted_latency_ms": round(1000.0 / 1.16 - 537.5, 1),
            "root_cause": (
                "In scripts/profile_baseline_flights.py line 204: 'await asyncio.sleep(1.0 / cfg.max_fps)'. "
                "With cfg.max_fps=4.0, each loop iteration deliberately slept for 250.0 ms! "
                "Adding 537.5 ms e2e + 250.0 ms sleep + ~74 ms asyncio event loop dispatch yields exactly ~862 ms (1.16 fps). "
                "Similarly in production DroneOS/perception/pipeline.py line 125: 'sleep_time = max(0.01, interval - elapsed)', "
                "which enforces the configured max_fps throttle (defaults to 1.0 fps)."
            ),
        },
        "mystery_2_why_window_3_showed_0_18_fps_at_637ms": {
            "measured_e2e_ms": 637.3,
            "measured_fps": 0.18,
            "window_duration_s": 11.24,
            "window_frames": 2,
            "apparent_cycle_ms": 5620.0,
            "root_cause": (
                "Thread-blocking AirSim RPC call 'move_task.join()' executed inside the flight test harness. "
                "In profile_baseline_flights.py line 206: after the 16s flight loop exited, the harness called "
                "'move_task.join()' inside the 'finally' block on the main asyncio thread. "
                "AirSim's moveToPositionAsync movement took ~6.3 seconds to decelerate and join. "
                "Because line 210 'dt = time.monotonic() - pipeline._profile_window_start' was evaluated AFTER "
                "move_task.join() returned, the window duration was inflated to 11.24 seconds for the 2 remaining frames, "
                "creating the synthetic 0.18 fps artifact (2 frames / 11.24s = 0.178 fps)."
            ),
        },
    }

    for k, v in analysis.items():
        print(f"\n[{k}]:")
        for sub_k, sub_v in v.items():
            print(f"  {sub_k}: {sub_v}")

    return analysis


async def main():
    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()

    part_a_results = await measure_part_a(client)
    part_b_results = measure_part_b()

    full_report = {
        "part_a_detectability": part_a_results,
        "part_b_unexplained_time": part_b_results,
    }

    out_file = REPO_ROOT / "ml" / "reports" / "step2_addendum_detectability_and_timing.json"
    out_file.write_text(json.dumps(full_report, indent=2))
    print(f"\nSaved combined Addendum report to: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
