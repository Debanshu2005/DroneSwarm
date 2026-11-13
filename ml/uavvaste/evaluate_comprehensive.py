#!/usr/bin/env python3
"""
Comprehensive quantitative evaluation of YOLO26n on UAVVaste validation split.
Computes TP, FP, FN, per-image statistics, confidence distributions, and generates:
- Precision-Recall curve
- F1 vs Confidence curve
- Precision vs Confidence curve
- Recall vs Confidence curve
- Confidence Histogram
Outputs saved to ml/reports/
"""

import json
from pathlib import Path
import sys

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from ultralytics import YOLO


def compute_iou(box1, box2):
    """Compute IoU between two (x1, y1, x2, y2) boxes."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    union_area = area1 + area2 - inter_area

    if union_area <= 0:
        return 0.0
    return inter_area / union_area


def evaluate_dataset(
    weights_path: str = "models/garbage.pt",
    dataset_yaml: str = "ml/data/uavvaste_yolo/dataset.yaml",
    split: str = "val",
    imgsz: int = 1280,
    device: str = "cuda:0",
    out_dir: str = "ml/reports",
):
    out_path = Path(out_dir)
    curves_path = out_path / "curves"
    curves_path.mkdir(parents=True, exist_ok=True)

    print(f"Loading model {weights_path}...")
    model = YOLO(weights_path)

    # 1. Standard Ultralytics Validation on the split
    print(f"Running Ultralytics validation on split='{split}' at imgsz={imgsz} (batch=2, workers=0)...")
    val_res = model.val(
        data=dataset_yaml,
        split=split,
        imgsz=imgsz,
        batch=2,
        workers=0,
        device=device,
        plots=True,
        save_json=False,
        verbose=False,
    )

    results_dict = val_res.results_dict
    print("\n--- Ultralytics Official Metrics ---")
    for k, v in results_dict.items():
        print(f"  {k}: {v}")

    # 2. Detailed Per-Image Analysis on Validation Set
    val_txt = Path("ml/data/uavvaste_yolo") / f"{split}.txt"
    image_paths = [Path(line.strip()) for line in val_txt.read_text().splitlines() if line.strip()]

    all_confidences = []
    tp_confidences = []
    fp_confidences = []

    per_image_stats = []
    total_gt = 0
    total_pred = 0

    # Matching IoU threshold
    iou_thresh = 0.5
    conf_thresh = 0.35

    print(f"\nProcessing {len(image_paths)} images for per-box error analysis (conf >= {conf_thresh})...")

    for img_rel in image_paths:
        img_p = Path("ml/data/uavvaste_yolo") / img_rel
        if not img_p.exists():
            img_p = Path(img_rel)

        lbl_p = Path("ml/data/uavvaste_yolo/labels") / (img_p.stem + ".txt")

        img = cv2.imread(str(img_p))
        if img is None:
            continue
        h, w = img.shape[:2]

        # Load GT boxes (x1, y1, x2, y2 in pixels)
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

        total_gt += len(gt_boxes)

        # Run inference at 0.10 to collect distribution
        with torch.no_grad():
            preds = model.predict(source=img, imgsz=imgsz, conf=0.10, iou=0.5, device=device, verbose=False)[0]

        pred_boxes = []
        for box in preds.boxes:
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            pred_boxes.append({"bbox": xyxy, "conf": conf})
            all_confidences.append(conf)

        if len(per_image_stats) % 25 == 0 and "cuda" in device:
            torch.cuda.empty_cache()

        # Greedy bipartite matching for TP/FP/FN at conf_thresh
        matched_gt = set()
        img_tp = 0
        img_fp = 0

        # Sort predictions by confidence descending
        sorted_preds = sorted(
            [p for p in pred_boxes if p["conf"] >= conf_thresh],
            key=lambda x: x["conf"],
            reverse=True,
        )
        total_pred += len(sorted_preds)

        for p in sorted_preds:
            p_box = p["bbox"]
            p_conf = p["conf"]
            best_iou = 0.0
            best_gt_idx = -1

            for gt_idx, g_box in enumerate(gt_boxes):
                if gt_idx in matched_gt:
                    continue
                iou = compute_iou(p_box, g_box)
                if iou >= iou_thresh and iou > best_iou:
                    best_iou = iou
                    best_gt_idx = gt_idx

            if best_gt_idx >= 0:
                matched_gt.add(best_gt_idx)
                img_tp += 1
                tp_confidences.append(p_conf)
            else:
                img_fp += 1
                fp_confidences.append(p_conf)

        img_fn = len(gt_boxes) - len(matched_gt)

        per_image_stats.append({
            "image": img_p.name,
            "gt_count": len(gt_boxes),
            "pred_count": len(sorted_preds),
            "tp": img_tp,
            "fp": img_fp,
            "fn": img_fn,
        })

    # Summary metrics at conf_thresh
    total_tp = sum(s["tp"] for s in per_image_stats)
    total_fp = sum(s["fp"] for s in per_image_stats)
    total_fn = sum(s["fn"] for s in per_image_stats)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    print(f"\n--- Box Matching Summary @ conf={conf_thresh}, iou={iou_thresh} ---")
    print(f"Total Ground Truth: {total_gt}")
    print(f"Total Predictions:  {total_pred}")
    print(f"True Positives:     {total_tp}")
    print(f"False Positives:    {total_fp}")
    print(f"False Negatives:    {total_fn}")
    print(f"Precision:          {precision*100:.2f}%")
    print(f"Recall:             {recall*100:.2f}%")
    print(f"F1 Score:           {f1:.4f}")

    # 3. Generate Curves
    confs_sweep = np.linspace(0.1, 0.9, 33)
    p_curve = []
    r_curve = []
    f1_curve = []

    for c in confs_sweep:
        tp_c = sum(1 for conf in tp_confidences if conf >= c)
        fp_c = sum(1 for conf in fp_confidences if conf >= c)
        fn_c = total_gt - tp_c
        p_c = tp_c / (tp_c + fp_c) if (tp_c + fp_c) > 0 else 1.0
        r_c = tp_c / (tp_c + fn_c) if (tp_c + fn_c) > 0 else 0.0
        f_c = 2 * p_c * r_c / (p_c + r_c) if (p_c + r_c) > 0 else 0.0
        p_curve.append(p_c)
        r_curve.append(r_c)
        f1_curve.append(f_c)

    # Plot 1: PR Curve
    plt.figure(figsize=(7, 6), dpi=150)
    plt.plot(r_curve, p_curve, color="#8b5cf6", linewidth=2.5, label="YOLO26n UAVVaste")
    plt.scatter([recall], [precision], color="#ef4444", s=60, zorder=5, label=f"Op Point @ {conf_thresh}")
    plt.title(f"Precision-Recall Curve (Validation Split, N={len(image_paths)})", fontsize=12, fontweight="bold")
    plt.xlabel("Recall", fontsize=11)
    plt.ylabel("Precision", fontsize=11)
    plt.xlim([0.0, 1.02])
    plt.ylim([0.0, 1.05])
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(loc="lower left")
    plt.tight_layout()
    plt.savefig(curves_path / "pr_curve_detailed.png")
    plt.close()

    # Plot 2: P, R, F1 vs Confidence
    plt.figure(figsize=(9, 5.5), dpi=150)
    plt.plot(confs_sweep, p_curve, color="#10b981", linewidth=2.0, label="Precision")
    plt.plot(confs_sweep, r_curve, color="#3b82f6", linewidth=2.0, label="Recall")
    plt.plot(confs_sweep, f1_curve, color="#f59e0b", linewidth=2.0, label="F1 Score")
    plt.axvline(conf_thresh, color="#6b7280", linestyle=":", label=f"Default ({conf_thresh})")
    plt.axvline(0.50, color="#8b5cf6", linestyle="--", label="Balanced (0.50)")
    plt.title("Metrics vs Confidence Threshold", fontsize=12, fontweight="bold")
    plt.xlabel("Confidence", fontsize=11)
    plt.ylabel("Metric Score", fontsize=11)
    plt.ylim([0.0, 1.05])
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(loc="lower left")
    plt.tight_layout()
    plt.savefig(curves_path / "metrics_vs_confidence.png")
    plt.close()

    # Plot 3: Confidence Histogram (TP vs FP)
    plt.figure(figsize=(8, 5), dpi=150)
    bins = np.linspace(0.1, 1.0, 19)
    plt.hist(tp_confidences, bins=bins, alpha=0.7, color="#10b981", label=f"True Positives (N={len(tp_confidences)})")
    plt.hist(fp_confidences, bins=bins, alpha=0.7, color="#ef4444", label=f"False Positives (N={len(fp_confidences)})")
    plt.title("Confidence Distribution: True vs False Detections", fontsize=12, fontweight="bold")
    plt.xlabel("Prediction Confidence", fontsize=11)
    plt.ylabel("Detection Count", fontsize=11)
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(curves_path / "confidence_histogram.png")
    plt.close()

    # Save summary report JSON
    report = {
        "split": split,
        "num_images": len(image_paths),
        "total_ground_truth": total_gt,
        "total_predictions": total_pred,
        "confidence_threshold": conf_thresh,
        "iou_threshold": iou_thresh,
        "box_matching": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "ultralytics_metrics": {k: float(v) for k, v in results_dict.items()},
        "per_image_stats": per_image_stats,
    }

    report_file = out_path / "validation_metrics.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n[OK] Validation metrics and curves saved to {out_path}")
    return report


if __name__ == "__main__":
    evaluate_dataset()

