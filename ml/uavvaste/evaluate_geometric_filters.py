#!/usr/bin/env python3
"""
Evaluate bounding box geometric quality filters on UAVVaste validation split.

Analyzes:
1. Relative box area: area / (W * H)
2. Aspect ratio: max(w/h, h/w)
3. Edge boundary touch: distance to image perimeter <= border_margin_px

Measures Precision, Recall, and False Positive rejection under:
- Raw detections (no geometric filters)
- Filter 1: Min box area (rejecting subpixel / distant specks)
- Filter 2: Max box area (rejecting whole-scene / sky anomalies)
- Filter 3: Extreme aspect ratio (rejecting long road lines / poles)
- Filter 4: Combined geometric filter
"""

import argparse
import json
from pathlib import Path
from typing import Dict

import cv2
import torch
from ultralytics import YOLO


def compute_iou(b1, b2):
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def evaluate_geometric_filtering(
    weights_path: str = "models/garbage.pt",
    split: str = "val",
    conf_thresh: float = 0.55,
    min_area_ratio: float = 0.0001,   # 0.01% of image area
    max_area_ratio: float = 0.35,     # 35% of image area
    max_aspect_ratio: float = 6.0,    # w/h or h/w > 6.0
    border_margin_px: int = 2,
    out_dir: str = "ml/reports",
) -> Dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    model = YOLO(weights_path)
    val_txt = Path("ml/data/uavvaste_yolo") / f"{split}.txt"
    image_paths = [Path(line.strip()) for line in val_txt.read_text().splitlines() if line.strip()]

    # Collect all predictions and ground truths
    all_gt_boxes = []
    all_raw_preds = []

    print(f"Running inference across {len(image_paths)} images at conf={conf_thresh}...")

    for img_rel in image_paths:
        img_p = Path("ml/data/uavvaste_yolo") / img_rel
        if not img_p.exists():
            img_p = Path(img_rel)
        lbl_p = Path("ml/data/uavvaste_yolo/labels") / (img_p.stem + ".txt")

        img = cv2.imread(str(img_p))
        if img is None:
            continue
        h, w = img.shape[:2]

        gt_boxes = []
        if lbl_p.exists():
            for line in lbl_p.read_text().splitlines():
                parts = line.strip().split()
                if len(parts) >= 5:
                    xc, yc, bw, bh = [float(v) for v in parts[1:5]]
                    gt_boxes.append([
                        (xc - bw / 2.0) * w,
                        (yc - bh / 2.0) * h,
                        (xc + bw / 2.0) * w,
                        (yc + bh / 2.0) * h,
                    ])

        with torch.no_grad():
            preds = model.predict(source=img, imgsz=1280, conf=conf_thresh, iou=0.5, device="cpu", verbose=False)[0]

        pred_boxes = []
        for box in preds.boxes:
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            bw = xyxy[2] - xyxy[0]
            bh = xyxy[3] - xyxy[1]
            area_ratio = (bw * bh) / float(w * h)
            ar = max(bw / max(1.0, bh), bh / max(1.0, bw))
            touches_border = (
                xyxy[0] <= border_margin_px
                or xyxy[1] <= border_margin_px
                or (w - xyxy[2]) <= border_margin_px
                or (h - xyxy[3]) <= border_margin_px
            )

            pred_boxes.append({
                "bbox": xyxy,
                "conf": conf,
                "area_ratio": area_ratio,
                "aspect_ratio": ar,
                "touches_border": touches_border,
            })

        all_gt_boxes.append(gt_boxes)
        all_raw_preds.append(pred_boxes)

    # Evaluate multiple filter combinations
    filter_configs = {
        "Raw (No Geometric Filter)": lambda p: True,
        "Min Area Only (area >= 0.01%)": lambda p: p["area_ratio"] >= min_area_ratio,
        "Max Area Only (area <= 35%)": lambda p: p["area_ratio"] <= max_area_ratio,
        "Aspect Ratio Only (ar <= 6.0)": lambda p: p["aspect_ratio"] <= max_aspect_ratio,
        "Combined Filter (Area + AR)": lambda p: (
            p["area_ratio"] >= min_area_ratio
            and p["area_ratio"] <= max_area_ratio
            and p["aspect_ratio"] <= max_aspect_ratio
        ),
    }

    results = {}
    total_gt_count = sum(len(gt) for gt in all_gt_boxes)

    for cfg_name, filt_fn in filter_configs.items():
        tp = 0
        fp = 0
        for gts, preds in zip(all_gt_boxes, all_raw_preds):
            filtered_preds = [p for p in preds if filt_fn(p)]
            matched_gts = set()
            for p in sorted(filtered_preds, key=lambda x: x["conf"], reverse=True):
                matched = False
                for g_idx, g_box in enumerate(gts):
                    if g_idx in matched_gts:
                        continue
                    if compute_iou(p["bbox"], g_box) >= 0.50:
                        matched_gts.add(g_idx)
                        matched = True
                        break
                if matched:
                    tp += 1
                else:
                    fp += 1

        fn = total_gt_count - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / total_gt_count if total_gt_count > 0 else 0.0
        f1 = (2.0 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

        results[cfg_name] = {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1_score": round(f1, 4),
        }

    # Export JSON
    json_path = out_path / "geometric_filter_evaluation.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "confidence_threshold": conf_thresh,
                "total_ground_truth": total_gt_count,
                "parameters": {
                    "min_area_ratio": min_area_ratio,
                    "max_area_ratio": max_area_ratio,
                    "max_aspect_ratio": max_aspect_ratio,
                },
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"[OK] Exported geometric filter results: {json_path}")
    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate geometric bounding box filters.")
    parser.add_argument("--weights", default="models/garbage.pt")
    parser.add_argument("--conf", type=float, default=0.55)
    parser.add_argument("--out-dir", default="ml/reports")
    args = parser.parse_args()

    print("=" * 72)
    print("DroneSwarm — Bounding Box Geometric Filter Evaluation")
    print("=" * 72)

    res = evaluate_geometric_filtering(
        weights_path=args.weights,
        conf_thresh=args.conf,
        out_dir=args.out_dir,
    )

    print("\n--- Geometric Filter Results (at conf=0.55) ---")
    print(f"{'Configuration':<35} {'Precision':<10} {'Recall':<10} {'F1':<8} {'TP':<6} {'FP':<6}")
    print("-" * 72)
    for name, r in res.items():
        print(
            f"{name:<35} {r['precision']*100:<9.2f}% {r['recall']*100:<9.2f}% {r['f1_score']*100:<7.2f}% "
            f"{r['true_positives']:<6d} {r['false_positives']:<6d}"
        )
    print("-" * 72)


if __name__ == "__main__":
    main()

