"""
Evaluate FP16 vs FP32 on CUDA across the real UAVVaste test split.
Measures Precision, Recall, F1 at conf 0.55 and 0.35, and max per-box confidence difference.
"""

from pathlib import Path
import sys
import time
from typing import Dict, List, Tuple
import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.types import Detection

TEST_TXT = REPO_ROOT / "ml" / "data" / "uavvaste_yolo" / "test.txt"


def load_test_data() -> List[Tuple[Path, List[Tuple[float, float, float, float]]]]:
    base_dir = TEST_TXT.parent
    samples = []
    with open(TEST_TXT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            img_p = (base_dir / line).resolve()
            if not img_p.exists():
                continue
            lbl_p = Path(str(img_p).replace("images", "labels")).with_suffix(".txt")
            gt_boxes = []
            if lbl_p.exists():
                img = cv2.imread(str(img_p))
                if img is None:
                    continue
                h, w = img.shape[:2]
                with open(lbl_p, "r", encoding="utf-8") as lf:
                    for lline in lf:
                        parts = lline.strip().split()
                        if len(parts) >= 5:
                            xc, yc, bw, bh = map(float, parts[1:5])
                            x1 = (xc - bw / 2.0) * w
                            y1 = (yc - bh / 2.0) * h
                            x2 = (xc + bw / 2.0) * w
                            y2 = (yc + bh / 2.0) * h
                            gt_boxes.append((x1, y1, x2, y2))
            samples.append((img_p, gt_boxes))
    return samples


def compute_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    inter = max(0.0, xB - xA) * max(0.0, yB - yA)
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    union = areaA + areaB - inter
    return inter / union if union > 0 else 0.0


def evaluate_detections(
    all_detections: List[List[Detection]],
    all_gt: List[List[Tuple[float, float, float, float]]],
    iou_thresh: float = 0.5,
) -> Dict[str, float]:
    total_tp = 0
    total_fp = 0
    total_fn = 0

    for dets, gts in zip(all_detections, all_gt):
        gt_matched = [False] * len(gts)
        # Sort dets by confidence descending
        sorted_dets = sorted(dets, key=lambda d: d.confidence, reverse=True)

        for d in sorted_dets:
            best_iou = 0.0
            best_idx = -1
            for g_idx, g_box in enumerate(gts):
                if gt_matched[g_idx]:
                    continue
                iou = compute_iou(d.bbox_xyxy, g_box)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = g_idx

            if best_iou >= iou_thresh and best_idx >= 0:
                total_tp += 1
                gt_matched[best_idx] = True
            else:
                total_fp += 1

        total_fn += sum(1 for m in gt_matched if not m)

    p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": total_tp, "fp": total_fp, "fn": total_fn}


def compare_per_box_confidences(
    dets_fp16: List[List[Detection]], dets_fp32: List[List[Detection]], iou_thresh: float = 0.5
) -> Dict[str, float]:
    diffs = []
    matched_pairs = 0

    for d16_list, d32_list in zip(dets_fp16, dets_fp32):
        matched_32 = [False] * len(d32_list)
        for d16 in d16_list:
            best_iou = 0.0
            best_idx = -1
            for idx, d32 in enumerate(d32_list):
                if matched_32[idx]:
                    continue
                iou = compute_iou(d16.bbox_xyxy, d32.bbox_xyxy)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = idx

            if best_iou >= iou_thresh and best_idx >= 0:
                d32 = d32_list[best_idx]
                matched_32[best_idx] = True
                diff = abs(d16.confidence - d32.confidence)
                diffs.append(diff)
                matched_pairs += 1

    if not diffs:
        return {"max_diff": 0.0, "mean_diff": 0.0, "matched_pairs": 0}

    return {
        "max_diff": float(np.max(diffs)),
        "mean_diff": float(np.mean(diffs)),
        "matched_pairs": matched_pairs,
    }


def run_benchmark():
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"Device: {torch.cuda.get_device_name(0)}")

    samples = load_test_data()
    print(f"Loaded {len(samples)} test images.")
    all_gt = [gts for _, gts in samples]
    total_gt_objects = sum(len(g) for g in all_gt)
    print(f"Total ground truth objects in test split: {total_gt_objects}")

    results = {}

    for conf in [0.55, 0.35]:
        print("\n==========================================")
        print(f" EVALUATING AT CONFIDENCE = {conf}")
        print("==========================================")

        for use_fp16 in [True, False]:
            label = "FP16" if use_fp16 else "FP32"
            print(f"\n--- Running GarbageDetector CUDA ({label}, conf={conf}) ---")
            detector = GarbageDetector(
                weights_path=str(REPO_ROOT / "models" / "garbage.pt"),
                device="cuda",
                confidence=conf,
                imgsz=640,
            )
            detector.load()
            detector.fp16 = use_fp16
            detector.warmup(warmup_frames=3)

            all_dets = []
            latencies_ms = []

            for img_p, _ in samples:
                img = cv2.imread(str(img_p))
                t0 = time.perf_counter()
                dets = detector._sync_predict(img)
                t1 = time.perf_counter()
                latencies_ms.append((t1 - t0) * 1000.0)
                all_dets.append(dets)

            detector.close()

            metrics = evaluate_detections(all_dets, all_gt, iou_thresh=0.5)
            metrics["p50_latency_ms"] = float(np.percentile(latencies_ms, 50))
            metrics["mean_latency_ms"] = float(np.mean(latencies_ms))
            results[f"{label}_conf_{conf}"] = {
                "metrics": metrics,
                "detections": all_dets,
            }

            print(
                f"  {label} (conf={conf}): Precision={metrics['precision']:.4f}, "
                f"Recall={metrics['recall']:.4f}, F1={metrics['f1']:.4f} "
                f"(TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}) | "
                f"Latency: p50={metrics['p50_latency_ms']:.2f}ms, mean={metrics['mean_latency_ms']:.2f}ms"
            )

        # Per-box confidence comparison between FP16 and FP32 at this conf
        diff_info = compare_per_box_confidences(
            results[f"FP16_conf_{conf}"]["detections"],
            results[f"FP32_conf_{conf}"]["detections"],
            iou_thresh=0.5,
        )
        print(f"\n  [Conf Diff Analysis @ conf={conf}]:")
        print(f"    Matched Bounding Box Pairs: {diff_info['matched_pairs']}")
        print(f"    Max Per-Box Confidence Diff:  {diff_info['max_diff']:.6f}")
        print(f"    Mean Per-Box Confidence Diff: {diff_info['mean_diff']:.6f}")
        results[f"diff_conf_{conf}"] = diff_info

    return results


if __name__ == "__main__":
    run_benchmark()
