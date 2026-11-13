#!/usr/bin/env python3
"""
Generate visual error analysis panels on UAVVaste validation split.
Categorizes and renders:
1. Correct Detections (True Positives)
2. False Positives (Commission Errors: terrain, shadows, rocks)
3. Missed Objects (False Negatives: tiny, occluded, or boundary rubbish)
4. Clustered / Multiple Detections on Single Objects
Saves visual crops and analysis to ml/reports/visual_errors/
"""

import json
from pathlib import Path
import cv2
import numpy as np
import torch
from ultralytics import YOLO


def compute_iou(box1, box2):
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    a1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    a2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    union = a1 + a2 - inter
    return inter / union if union > 0 else 0.0


def extract_crops_and_analyze(
    weights_path: str = "models/garbage.pt",
    split: str = "val",
    conf_thresh: float = 0.35,
    iou_thresh: float = 0.5,
    out_dir: str = "ml/reports/visual_errors",
):
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"Loading YOLO model from {weights_path}...")
    model = YOLO(weights_path)

    val_txt = Path("ml/data/uavvaste_yolo") / f"{split}.txt"
    image_paths = [Path(line.strip()) for line in val_txt.read_text().splitlines() if line.strip()]

    categories = {
        "true_positives": [],
        "false_positives": [],
        "false_negatives": [],
        "clusters": [],
    }

    max_per_category = 5

    print(f"Analyzing {len(image_paths)} images for representative error visual samples...")

    for img_rel in image_paths:
        if all(len(v) >= max_per_category for v in categories.values()):
            break

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
                if not parts:
                    continue
                xc, yc, bw, bh = [float(v) for v in parts[1:5]]
                x1 = (xc - bw / 2.0) * w
                y1 = (yc - bh / 2.0) * h
                x2 = (xc + bw / 2.0) * w
                y2 = (yc + bh / 2.0) * h
                gt_boxes.append([x1, y1, x2, y2])

        with torch.no_grad():
            preds = model.predict(source=img, imgsz=1280, conf=conf_thresh, iou=0.5, device="cuda:0", verbose=False)[0]

        pred_boxes = []
        for box in preds.boxes:
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            pred_boxes.append({"bbox": xyxy, "conf": conf})

        # Match predictions to GT
        matched_gt = set()
        matched_preds = set()

        for p_idx, p in enumerate(pred_boxes):
            best_iou = 0.0
            best_gt_idx = -1
            for g_idx, g in enumerate(gt_boxes):
                if g_idx in matched_gt:
                    continue
                iou = compute_iou(p["bbox"], g)
                if iou >= iou_thresh and iou > best_iou:
                    best_iou = iou
                    best_gt_idx = g_idx

            if best_gt_idx >= 0:
                matched_gt.add(best_gt_idx)
                matched_preds.add(p_idx)
                if len(categories["true_positives"]) < max_per_category and p["conf"] > 0.6:
                    categories["true_positives"].append({
                        "image": img_p.name,
                        "gt_box": gt_boxes[best_gt_idx],
                        "pred_box": p["bbox"],
                        "conf": p["conf"],
                        "iou": best_iou,
                    })

        # False Positives
        for p_idx, p in enumerate(pred_boxes):
            if p_idx not in matched_preds and len(categories["false_positives"]) < max_per_category:
                categories["false_positives"].append({
                    "image": img_p.name,
                    "pred_box": p["bbox"],
                    "conf": p["conf"],
                })

        # False Negatives (Missed)
        for g_idx, g in enumerate(gt_boxes):
            if g_idx not in matched_gt and len(categories["false_negatives"]) < max_per_category:
                categories["false_negatives"].append({
                    "image": img_p.name,
                    "gt_box": g,
                })

        # Clusters (multiple predictions close to each other)
        if len(pred_boxes) >= 2 and len(categories["clusters"]) < max_per_category:
            for i in range(len(pred_boxes)):
                for j in range(i + 1, len(pred_boxes)):
                    b1 = pred_boxes[i]["bbox"]
                    b2 = pred_boxes[j]["bbox"]
                    iou = compute_iou(b1, b2)
                    if 0.1 <= iou < 0.5:
                        categories["clusters"].append({
                            "image": img_p.name,
                            "box1": b1,
                            "box2": b2,
                            "conf1": pred_boxes[i]["conf"],
                            "conf2": pred_boxes[j]["conf"],
                            "iou": iou,
                        })
                        break

    # Render Visual Crops
    print("\nRendering annotated crops for each category...")
    report_lines = [
        "# Visual Error Analysis Report",
        "",
        "This report documents visual inspection of representative detections and error failure modes on the UAVVaste validation split.",
        "",
        "**Color Conventions:**",
        "- **Green Box**: Ground Truth Rubbish Annotation",
        "- **Blue Box**: True Positive Prediction (Matches Ground Truth)",
        "- **Red Box**: False Positive Prediction (No Ground Truth match)",
        "- **Orange Dashed Box**: False Negative (Missed Ground Truth Object)",
        "",
    ]

    for cat_name, items in categories.items():
        cat_title = cat_name.replace("_", " ").title()
        report_lines.append(f"## {cat_title}")
        report_lines.append(f"Identified {len(items)} representative instances:\n")

        for idx, item in enumerate(items):
            img_name = item["image"]
            img_p = Path("ml/data/uavvaste_yolo/images") / img_name
            img = cv2.imread(str(img_p))
            if img is None:
                continue
            h, w = img.shape[:2]

            # Determine crop window centered around the target
            if "pred_box" in item:
                target_box = item["pred_box"]
            elif "gt_box" in item:
                target_box = item["gt_box"]
            else:
                target_box = item["box1"]

            x1, y1, x2, y2 = target_box
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            crop_pad = max(160, int(max(x2 - x1, y2 - y1) * 2.0))

            cx1 = max(0, int(cx - crop_pad))
            cy1 = max(0, int(cy - crop_pad))
            cx2 = min(w, int(cx + crop_pad))
            cy2 = min(h, int(cy + crop_pad))

            crop = img[cy1:cy2, cx1:cx2].copy()

            # Draw Ground Truth
            if "gt_box" in item:
                gx1, gy1, gx2, gy2 = [int(v) for v in item["gt_box"]]
                cv2.rectangle(
                    crop,
                    (gx1 - cx1, gy1 - cy1),
                    (gx2 - cx1, gy2 - cy1),
                    (0, 255, 0),
                    2,
                )
                cv2.putText(
                    crop,
                    "GT",
                    (gx1 - cx1, max(15, gy1 - cy1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )

            # Draw Pred Box
            if "pred_box" in item:
                px1, py1, px2, py2 = [int(v) for v in item["pred_box"]]
                color = (255, 0, 0) if cat_name == "true_positives" else (0, 0, 255)
                tag = f"Pred {item['conf']:.2f}"
                cv2.rectangle(
                    crop,
                    (px1 - cx1, py1 - cy1),
                    (px2 - cx1, py2 - cy1),
                    color,
                    2,
                )
                cv2.putText(
                    crop,
                    tag,
                    (px1 - cx1, max(15, py1 - cy1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                )

            # Draw Cluster Boxes
            if "box1" in item and "box2" in item:
                b1 = [int(v) for v in item["box1"]]
                b2 = [int(v) for v in item["box2"]]
                cv2.rectangle(crop, (b1[0] - cx1, b1[1] - cy1), (b1[2] - cx1, b1[3] - cy1), (255, 0, 0), 2)
                cv2.rectangle(crop, (b2[0] - cx1, b2[1] - cy1), (b2[2] - cx1, b2[3] - cy1), (0, 0, 255), 2)
                cv2.putText(crop, f"Box1 {item['conf1']:.2f}", (b1[0] - cx1, max(15, b1[1] - cy1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 1)
                cv2.putText(crop, f"Box2 {item['conf2']:.2f}", (b2[0] - cx1, max(15, b2[1] - cy1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)

            crop_filename = f"{cat_name}_{idx+1}_{img_p.stem}.jpg"
            crop_path = out_path / crop_filename
            cv2.imwrite(str(crop_path), crop)

            report_lines.append(f"- **Sample {idx+1}** (`{img_name}`):")
            report_lines.append(f"  - Crop saved: `{crop_filename}`")
            if "conf" in item:
                report_lines.append(f"  - Confidence: `{item['conf']:.3f}`")
            if "iou" in item:
                report_lines.append(f"  - Matching IoU: `{item['iou']:.3f}`")
            report_lines.append("")

    summary_file = out_path / "visual_error_summary.md"
    summary_file.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"[OK] Visual error crops and summary saved to {out_path}")


if __name__ == "__main__":
    extract_crops_and_analyze()

