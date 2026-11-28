"""Offline evaluation tool for custom YOLO-formatted datasets.

Evaluates one or more detector weights (.pt / .onnx) on a folder containing
images/ and labels/ (YOLO normalized format) plus optional meta.csv
(columns: image,altitude_m,object_type).
"""
import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.detector import GarbageDetector

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SIZE_BUCKETS = ("<10", "10-20", "20-40", ">40")


def box_iou_xyxy(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    denom = area_a + area_b - inter
    return inter / denom if denom > 0.0 else 0.0


def parse_class_map(raw: Optional[str]) -> Optional[Dict[int, str]]:
    if not raw:
        return None
    p = Path(raw)
    if p.exists() and p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        return {int(k): str(v) for k, v in data.items()}
    mapping: Dict[int, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        k, v = item.split(":", 1)
        mapping[int(k.strip())] = v.strip()
    return mapping or None


def size_bucket(px_size: float) -> str:
    if px_size < 10.0:
        return "<10"
    if px_size < 20.0:
        return "10-20"
    if px_size <= 40.0:
        return "20-40"
    return ">40"


def load_meta_csv(meta_path: Path) -> Dict[str, Dict[str, str]]:
    if not meta_path.exists():
        return {}
    meta: Dict[str, Dict[str, str]] = {}
    with open(meta_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("image") or "").strip()
            if key:
                meta[key] = {
                    "altitude_m": (row.get("altitude_m") or "").strip(),
                    "object_type": (row.get("object_type") or "").strip(),
                }
                meta[Path(key).stem] = meta[key]
    return meta


def load_dataset(
    data_dir: Path,
    imgsz: int,
    class_map: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
    images_dir = data_dir / "images"
    labels_dir = data_dir / "labels"
    if not images_dir.exists() or not images_dir.is_dir():
        raise FileNotFoundError(f"images/ directory not found in '{data_dir}'")

    img_paths = sorted(p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if not img_paths:
        raise ValueError(f"No images found in '{images_dir}'")

    meta_map = load_meta_csv(data_dir / "meta.csv")
    items: List[Dict[str, Any]] = []
    for img_path in img_paths:
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        scale = min(float(imgsz) / max(1, h), float(imgsz) / max(1, w))
        lbl_path = labels_dir / f"{img_path.stem}.txt"
        gts: List[Dict[str, Any]] = []
        if lbl_path.exists():
            for line in lbl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.replace(",", " ").split()
                if len(parts) < 5:
                    continue
                cls_id = int(float(parts[0]))
                xc, yc, bw, bh = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                x1 = (xc - bw / 2.0) * w
                y1 = (yc - bh / 2.0) * h
                x2 = (xc + bw / 2.0) * w
                y2 = (yc + bh / 2.0) * h
                lb_px = max(bw * w, bh * h) * scale
                cls_name = class_map.get(cls_id, str(cls_id)) if class_map else "garbage"
                gts.append(
                    {
                        "box": (x1, y1, x2, y2),
                        "class_name": cls_name,
                        "lb_size_px": round(lb_px, 2),
                        "size_bucket": size_bucket(lb_px),
                    }
                )
        row_meta = meta_map.get(img_path.name) or meta_map.get(img_path.stem) or {}
        items.append(
            {
                "name": img_path.name,
                "path": str(img_path),
                "image": img,
                "gts": gts,
                "altitude_m": row_meta.get("altitude_m", ""),
                "object_type": row_meta.get("object_type", ""),
            }
        )
    if not items:
        raise ValueError(f"No readable images found in '{images_dir}'")
    return items


def evaluate_weights(
    weights_path: Path,
    items: List[Dict[str, Any]],
    *,
    imgsz: int,
    conf_list: List[float],
    iou_thresh: float,
    device: str,
    class_map: Optional[Dict[int, str]],
) -> Dict[str, Any]:
    min_conf = min(conf_list)
    det = GarbageDetector(
        weights_path=str(weights_path),
        imgsz=imgsz,
        confidence=min_conf,
        iou=0.5,
        device=device,
    )
    if not det.load():
        raise RuntimeError(f"Failed to load detector weights: {weights_path}")

    preds_per_image: List[Tuple[List[Tuple[Tuple[float, float, float, float], float, str]], float]] = []
    try:
        for item in items:
            raw_dets = det._sync_predict(item["image"]) or []
            max_raw = float(getattr(det, "last_timing", {}).get("max_raw_conf", 0.0) or 0.0)
            parsed = []
            for d in raw_dets:
                lbl = d.label if class_map else "garbage"
                parsed.append((tuple(float(v) for v in d.bbox_xyxy), float(d.confidence), lbl))
            preds_per_image.append((parsed, max_raw))
    finally:
        det.close()

    by_conf: Dict[str, Any] = {}
    for c_thr in sorted(conf_list):
        tp = fp = fn = 0
        bucket_stats = {b: {"tp": 0, "total": 0} for b in SIZE_BUCKETS}
        alt_stats: Dict[str, Dict[str, int]] = {}
        type_stats: Dict[str, Dict[str, int]] = {}
        misses: List[Dict[str, Any]] = []

        for item, (preds, max_raw) in zip(items, preds_per_image):
            gts = item["gts"]
            filt = sorted((p for p in preds if p[1] >= c_thr), key=lambda x: -x[1])
            matched = set()
            for p_box, p_conf, p_lbl in filt:
                best_iou = 0.0
                best_j = -1
                for j, gt in enumerate(gts):
                    if j in matched:
                        continue
                    if class_map and p_lbl != gt["class_name"]:
                        continue
                    iou = box_iou_xyxy(p_box, gt["box"])
                    if iou > best_iou:
                        best_iou = iou
                        best_j = j
                if best_iou >= iou_thresh and best_j >= 0:
                    tp += 1
                    matched.add(best_j)
                else:
                    fp += 1

            for j, gt in enumerate(gts):
                bkt = gt["size_bucket"]
                bucket_stats[bkt]["total"] += 1
                alt_k = item["altitude_m"] or "unknown"
                type_k = item["object_type"] or "unknown"
                if item["altitude_m"]:
                    alt_stats.setdefault(alt_k, {"tp": 0, "total": 0})["total"] += 1
                if item["object_type"]:
                    type_stats.setdefault(type_k, {"tp": 0, "total": 0})["total"] += 1

                if j in matched:
                    bucket_stats[bkt]["tp"] += 1
                    if item["altitude_m"]:
                        alt_stats[alt_k]["tp"] += 1
                    if item["object_type"]:
                        type_stats[type_k]["tp"] += 1
                else:
                    fn += 1
                    best_pred_iou = max((box_iou_xyxy(p[0], gt["box"]) for p in filt), default=0.0)
                    misses.append(
                        {
                            "image": item["name"],
                            "gt_size_px": gt["lb_size_px"],
                            "size_bucket": bkt,
                            "altitude_m": item["altitude_m"] or None,
                            "object_type": item["object_type"] or None,
                            "max_raw_conf": round(max_raw, 4),
                            "best_iou": round(best_pred_iou, 4),
                        }
                    )

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2.0 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        misses.sort(key=lambda m: -m["gt_size_px"])

        by_conf[f"{c_thr:.2f}"] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "recall_by_size": {
                b: {
                    "recall": round(v["tp"] / v["total"], 4) if v["total"] > 0 else None,
                    "tp": v["tp"],
                    "total": v["total"],
                }
                for b, v in bucket_stats.items()
            },
            "recall_by_altitude": {
                k: {"recall": round(v["tp"] / v["total"], 4), "tp": v["tp"], "total": v["total"]}
                for k, v in sorted(alt_stats.items())
            },
            "recall_by_object_type": {
                k: {"recall": round(v["tp"] / v["total"], 4), "tp": v["tp"], "total": v["total"]}
                for k, v in sorted(type_stats.items())
            },
            "worst_misses": misses[:20],
        }

    return {"weights": str(weights_path), "by_conf": by_conf}


def _print_summary(all_results: List[Dict[str, Any]], conf_list: List[float]) -> None:
    print("\n=== CUSTOM DATASET EVALUATION ===")
    header = f"{'Weights':<28} | {'Conf':<5} | {'P':<6} | {'R':<6} | {'F1':<6} | {'TP':<4} | {'FP':<4} | {'FN':<4}"
    print(header)
    print("-" * len(header))
    for res in all_results:
        wname = Path(res["weights"]).name
        for c in sorted(conf_list):
            m = res["by_conf"][f"{c:.2f}"]
            print(
                f"{wname:<28} | {c:<5.2f} | {m['precision']:<6.4f} | {m['recall']:<6.4f} | "
                f"{m['f1']:<6.4f} | {m['tp']:<4d} | {m['fp']:<4d} | {m['fn']:<4d}"
            )
    for res in all_results:
        wname = Path(res["weights"]).name
        top_c = f"{max(conf_list):.2f}"
        m = res["by_conf"][top_c]
        print(f"\n[{wname} @ conf={top_c}] Recall by size (letterboxed px): {m['recall_by_size']}")
        if m["recall_by_altitude"]:
            print(f"[{wname} @ conf={top_c}] Recall by altitude_m: {m['recall_by_altitude']}")
        if m["recall_by_object_type"]:
            print(f"[{wname} @ conf={top_c}] Recall by object_type: {m['recall_by_object_type']}")
        if m["worst_misses"]:
            print(f"[{wname} @ conf={top_c}] Top {len(m['worst_misses'])} worst misses:")
            for miss in m["worst_misses"]:
                print(
                    f"  {miss['image']}: gt_size={miss['gt_size_px']}px ({miss['size_bucket']}) "
                    f"max_raw_conf={miss['max_raw_conf']}"
                )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate detector on a custom YOLO dataset.")
    p.add_argument("--data", required=True, help="Dataset folder containing images/, labels/, and optional meta.csv")
    p.add_argument(
        "--weights",
        nargs="+",
        default=["models/garbage.pt", "models/garbage.onnx"],
        help="One or more model weights (.pt or .onnx) to evaluate side-by-side",
    )
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="Inference device")
    p.add_argument("--imgsz", type=int, default=640, help="Detector input size")
    p.add_argument("--conf-list", default="0.25,0.35,0.55", help="Comma-separated confidence thresholds")
    p.add_argument("--iou", type=float, default=0.5, help="IoU threshold for TP matching")
    p.add_argument("--class-map", default=None, help="Optional class mapping '0:rubbish,1:plastic' or JSON path")
    p.add_argument("--output", default="scratch/eval_custom_dataset.json", help="Output JSON report path")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data)
    conf_list = [float(x.strip()) for x in args.conf_list.split(",") if x.strip()]
    if not conf_list:
        print("ERROR: --conf-list must contain at least one threshold.", file=sys.stderr)
        return 2
    class_map = parse_class_map(args.class_map)

    try:
        items = load_dataset(data_dir, imgsz=args.imgsz, class_map=class_map)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    neg_count = sum(1 for it in items if not it["gts"])
    gt_count = sum(len(it["gts"]) for it in items)
    print(f"Loaded {len(items)} images ({neg_count} negative/unlabelled, {gt_count} GT boxes).", flush=True)

    all_results: List[Dict[str, Any]] = []
    for raw_w in args.weights:
        wp = Path(raw_w)
        if not wp.is_absolute():
            wp = REPO_ROOT / wp
        if not wp.exists():
            print(f"ERROR: Weights not found: {wp}", file=sys.stderr)
            return 2
        all_results.append(
            evaluate_weights(
                wp,
                items,
                imgsz=args.imgsz,
                conf_list=conf_list,
                iou_thresh=args.iou,
                device=args.device,
                class_map=class_map,
            )
        )

    _print_summary(all_results, conf_list)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": str(data_dir),
        "num_images": len(items),
        "num_negatives": neg_count,
        "num_gt": gt_count,
        "imgsz": args.imgsz,
        "iou": args.iou,
        "conf_list": conf_list,
        "results": all_results,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nReport written to {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
