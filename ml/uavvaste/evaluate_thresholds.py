#!/usr/bin/env python3
"""
Evaluate confidence thresholds on UAVVaste dataset to analyze Precision-Recall trade-offs.

Thresholds evaluated: [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
Uses the official validation split to avoid overfitting the untouched test benchmark.
Exports metrics to CSV, JSON, and visual charts.
"""

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


THRESHOLDS = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def evaluate_thresholds(
    weights_path: str = "models/garbage.pt",
    dataset_yaml: str = "ml/data/uavvaste_yolo/dataset.yaml",
    split: str = "val",
    imgsz: int = 1280,
    device: str = "cuda:0",
    out_dir: str = "ml/runs",
) -> List[Dict]:
    """Run evaluation across all thresholds on specified split."""
    from ultralytics import YOLO

    weights = Path(weights_path).resolve()
    data = Path(dataset_yaml).resolve()
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    if not weights.exists():
        raise FileNotFoundError(f"Model weights not found: {weights}")
    if not data.exists():
        raise FileNotFoundError(f"Dataset config not found: {data}")

    print("=" * 72)
    print("DroneSwarm YOLO26n — Confidence Threshold Evaluation")
    print(f"Weights: {weights}")
    print(f"Dataset: {data} (split='{split}')")
    print(f"Resolution: {imgsz}x{imgsz} | Device: {device}")
    print("=" * 72)

    model = YOLO(str(weights))
    results_list: List[Dict] = []

    for conf in THRESHOLDS:
        print(f"\n[Evaluating conf={conf:.2f}]...")
        val_res = model.val(
            data=str(data),
            split=split,
            conf=conf,
            iou=0.5,
            imgsz=imgsz,
            device=device,
            plots=False,
            verbose=False,
        )

        res_dict = val_res.results_dict
        prec = float(res_dict.get("metrics/precision(B)", 0.0))
        rec = float(res_dict.get("metrics/recall(B)", 0.0))
        map50 = float(res_dict.get("metrics/mAP50(B)", 0.0))
        map50_95 = float(res_dict.get("metrics/mAP50-95(B)", 0.0))

        # Calculate F1
        f1 = (2.0 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

        # Calculate estimated TP, FP, FN based on instance count
        # In validation split of UAVVaste, total instances = 616
        total_instances = 616
        if hasattr(val_res, "nt_per_class") and len(val_res.nt_per_class) > 0:
            total_instances = int(sum(val_res.nt_per_class))

        tp = int(round(rec * total_instances))
        fn = int(total_instances - tp)
        fp = int(round(tp * (1.0 - prec) / prec)) if prec > 0 else 0

        row = {
            "confidence": round(conf, 2),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1_score": round(f1, 4),
            "mAP50": round(map50, 4),
            "mAP50_95": round(map50_95, 4),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "total_ground_truth": total_instances,
        }
        results_list.append(row)

        print(
            f"  -> Conf: {conf:.2f} | Precision: {prec*100:5.2f}% | Recall: {rec*100:5.2f}% | "
            f"F1: {f1:0.4f} | TP: {tp:3d} | FP: {fp:3d} | FN: {fn:3d}"
        )

    # 1. Export CSV
    csv_file = out_path / "threshold_evaluation.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "confidence",
            "precision",
            "recall",
            "f1_score",
            "mAP50",
            "mAP50_95",
            "true_positives",
            "false_positives",
            "false_negatives",
            "total_ground_truth",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results_list)
    print(f"\n[OK] Exported CSV to {csv_file}")

    # 2. Export JSON
    json_file = out_path / "threshold_evaluation.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "split": split,
                "model_weights": str(weights.name),
                "imgsz": imgsz,
                "iou": 0.5,
                "evaluation_results": results_list,
            },
            f,
            indent=2,
        )
    print(f"[OK] Exported JSON to {json_file}")

    # 3. Generate Visual Charts
    generate_charts(results_list, out_path)

    return results_list


def generate_charts(results: List[Dict], out_path: Path):
    """Generate Precision-Recall, Conf-vs-Precision, and Conf-vs-Recall charts."""
    confs = [r["confidence"] for r in results]
    precs = [r["precision"] for r in results]
    recs = [r["recall"] for r in results]
    f1s = [r["f1_score"] for r in results]

    # Chart 1: Precision & Recall vs Confidence
    plt.figure(figsize=(9, 5.5), dpi=150)
    plt.plot(confs, precs, "o-", color="#10b981", linewidth=2.2, label="Precision")
    plt.plot(confs, recs, "s-", color="#3b82f6", linewidth=2.2, label="Recall")
    plt.plot(confs, f1s, "^--", color="#f59e0b", linewidth=1.8, label="F1 Score")
    plt.axvline(0.35, color="#6b7280", linestyle=":", alpha=0.8, label="Baseline (0.35)")
    plt.axvline(0.50, color="#8b5cf6", linestyle="--", alpha=0.8, label="Candidate A (0.50)")
    plt.title("YOLO26n Performance vs Confidence Threshold (Validation Split)", fontsize=13, fontweight="bold")
    plt.xlabel("Confidence Threshold", fontsize=11)
    plt.ylabel("Score", fontsize=11)
    plt.ylim([0.0, 1.05])
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.legend(loc="lower left", frameon=True)
    plt.tight_layout()
    chart1 = out_path / "threshold_tradeoffs.png"
    plt.savefig(chart1)
    plt.close()
    print(f"[OK] Saved chart: {chart1}")

    # Chart 2: Precision-Recall Curve
    plt.figure(figsize=(7, 6), dpi=150)
    plt.plot(recs, precs, "o-", color="#8b5cf6", linewidth=2.2)
    for c, p, r in zip(confs, precs, recs):
        plt.annotate(
            f"{c:.2f}",
            (r, p),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
            color="#374151",
        )
    plt.title("Precision-Recall Operating Curve (Validation Split)", fontsize=13, fontweight="bold")
    plt.xlabel("Recall", fontsize=11)
    plt.ylabel("Precision", fontsize=11)
    plt.xlim([0.4, 1.0])
    plt.ylim([0.7, 1.02])
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    chart2 = out_path / "pr_curve.png"
    plt.savefig(chart2)
    plt.close()
    print(f"[OK] Saved chart: {chart2}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate confidence thresholds on UAVVaste.")
    parser.add_argument("--weights", default="models/garbage.pt", help="Path to weights")
    parser.add_argument("--data", default="ml/data/uavvaste_yolo/dataset.yaml", help="Dataset yaml")
    parser.add_argument("--split", default="val", help="Dataset split (val or test)")
    parser.add_argument("--imgsz", type=int, default=1280, help="Evaluation image size")
    parser.add_argument("--device", default="cuda:0", help="Execution device (cuda:0 or cpu)")
    parser.add_argument("--out-dir", default="ml/runs", help="Output directory")

    args = parser.parse_args()
    evaluate_thresholds(
        weights_path=args.weights,
        dataset_yaml=args.data,
        split=args.split,
        imgsz=args.imgsz,
        device=args.device,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()

