"""Prepare UAVVaste dataset for YOLO training with flight-group splitting."""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import random
import re
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Lazy import PIL / cv2 for image verification
try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import cv2
except ImportError:
    cv2 = None


def extract_flight_group(filename: str) -> str:
    r"""
    Extract flight group key from image filename.
    group key = regex ^(BATCH_[A-Za-z]\d+) else the alphabetic prefix before the digits
    """
    m = re.match(r"^(BATCH_[A-Za-z]\d+)", filename, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.match(r"^([A-Za-z_]+?)(?=\d)", filename)
    if m:
        return m.group(1).rstrip("_")
    return filename


def convert_coco_bbox(
    bbox: List[float],
    img_width: int,
    img_height: int,
    category_idx: int = 0,
    min_size: float = 1.0,
) -> Optional[Tuple[int, float, float, float, float]]:
    """
    Convert COCO bbox [x, y, w, h] in pixels to YOLO normalized [cx, cy, w, h].
    Clips coordinates to image bounds [0, 1] and drops boxes with width or height < min_size px.
    Returns (category_idx, cx, cy, nw, nh) or None if dropped.
    """
    if img_width <= 0 or img_height <= 0:
        return None

    x, y, w, h = bbox
    # Clip coordinates to image pixel rectangle [0, W] x [0, H]
    x1 = max(0.0, float(x))
    y1 = max(0.0, float(y))
    x2 = min(float(img_width), float(x + w))
    y2 = min(float(img_height), float(y + h))

    cw = x2 - x1
    ch = y2 - y1

    if cw < min_size or ch < min_size:
        return None

    cx = (x1 + cw / 2.0) / float(img_width)
    cy = (y1 + ch / 2.0) / float(img_height)
    nw = cw / float(img_width)
    nh = ch / float(img_height)

    # Clamp normalized values to [0.0, 1.0]
    cx = max(0.0, min(1.0, cx))
    cy = max(0.0, min(1.0, cy))
    nw = max(0.0, min(1.0, nw))
    nh = max(0.0, min(1.0, nh))

    return (category_idx, cx, cy, nw, nh)


def greedy_group_split(
    groups: Dict[str, List[Dict[str, Any]]],
    split_ratios: Tuple[float, float, float] = (0.70, 0.15, 0.15),
    seed: int = 0,
) -> Tuple[List[List[Tuple[str, List[Dict[str, Any]]]]], List[int], List[float]]:
    """
    Greedy assignment of whole flight groups to hit target image ratios.
    Deterministic by seed, ensuring every split is non-empty if possible.
    """
    rng = random.Random(seed)
    # Sort groups by name first for reproducibility across environments
    group_items = sorted(groups.items(), key=lambda x: x[0])
    rng.shuffle(group_items)

    total_imgs = sum(len(v) for _, v in group_items)
    targets = [total_imgs * r for r in split_ratios]
    splits: List[List[Tuple[str, List[Dict[str, Any]]]]] = [[] for _ in split_ratios]
    counts = [0 for _ in split_ratios]

    for group_key, imgs in group_items:
        # If any split is empty, prioritize filling it
        empty_splits = [i for i, c in enumerate(counts) if c == 0]
        if empty_splits:
            best_idx = max(empty_splits, key=lambda i: targets[i])
        else:
            # Pick the split that is furthest below its target proportion
            best_idx = max(range(len(splits)), key=lambda i: (targets[i] - counts[i]) / max(targets[i], 1e-6))

        splits[best_idx].append((group_key, imgs))
        counts[best_idx] += len(imgs)

    return splits, counts, targets


def verify_image_dimensions(image_path: Path, expected_w: int, expected_h: int) -> bool:
    """Verify that the image file can be decoded and matches expected dimensions."""
    if not image_path.exists() or image_path.stat().st_size == 0:
        return False
    if Image is not None:
        try:
            with Image.open(image_path) as im:
                w, h = im.size
                return w == expected_w and h == expected_h
        except Exception:
            return False
    elif cv2 is not None:
        try:
            img = cv2.imread(str(image_path))
            if img is None:
                return False
            h, w = img.shape[:2]
            return w == expected_w and h == expected_h
        except Exception:
            return False
    return image_path.stat().st_size > 0


def download_single_image(
    img: Dict[str, Any],
    images_dir: Path,
    retries: int = 3,
    backoff: float = 1.0,
) -> Tuple[str, bool, str]:
    """
    Download a single image with retries, atomic write via .part, and size verification.
    Returns (file_name, success, error_message).
    """
    file_name = img["file_name"]
    target_path = images_dir / file_name
    part_path = images_dir / f"{file_name}.part"
    expected_w = img.get("width", 0)
    expected_h = img.get("height", 0)

    # Check if already present and valid
    if target_path.exists() and verify_image_dimensions(target_path, expected_w, expected_h):
        return (file_name, True, "cached")

    url = img.get("flickr_url") or img.get("flickr_640_url")
    if not url:
        return (file_name, False, "No URL available in annotation")

    headers = {"User-Agent": "DroneSwarm-UAVVaste-Preparer/1.0"}
    last_err = ""

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                with open(part_path, "wb") as f:
                    f.write(data)

            # Atomic rename
            if part_path.exists():
                if target_path.exists():
                    target_path.unlink()
                part_path.rename(target_path)

            if verify_image_dimensions(target_path, expected_w, expected_h):
                return (file_name, True, "downloaded")
            else:
                last_err = f"Dimension mismatch (expected {expected_w}x{expected_h})"
                if target_path.exists():
                    target_path.unlink()
        except Exception as e:
            last_err = str(e)
            if part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass

        if attempt < retries - 1:
            time.sleep(backoff * (2 ** attempt))

    return (file_name, False, f"Failed after {retries} retries: {last_err}")


def print_dataset_report(
    split_names: List[str],
    splits: List[List[Tuple[str, List[Dict[str, Any]]]]],
    annotations_by_img: Dict[int, List[Dict[str, Any]]],
    dropped_boxes: int,
    all_kept_boxes: List[Tuple[float, float]],
) -> None:
    """Print summary statistics and box-size histogram."""
    print("\n" + "=" * 65)
    print("UAVVaste Dataset Split Report (Grouped by Flight)")
    print("=" * 65)
    print(f"{'Split':<10} {'Flights':<10} {'Images':<10} {'Instances':<12}")
    print("-" * 65)

    for name, split_groups in zip(split_names, splits):
        n_flights = len(split_groups)
        n_images = sum(len(imgs) for _, imgs in split_groups)
        n_instances = sum(
            len(annotations_by_img.get(img["id"], []))
            for _, imgs in split_groups
            for img in imgs
        )
        print(f"{name:<10} {n_flights:<10} {n_images:<10} {n_instances:<12}")

    print("-" * 65)
    total_imgs = sum(len(imgs) for sp in splits for _, imgs in sp)
    total_inst = len(all_kept_boxes)
    print(f"{'Total':<10} {sum(len(sp) for sp in splits):<10} {total_imgs:<10} {total_inst:<12}")
    print(f"Dropped Degenerate Boxes (<1px): {dropped_boxes}")
    print("=" * 65)

    # Box-size histogram
    if all_kept_boxes:
        print("\nObject Relative Dimensions Distribution (width x height as % of image):")
        widths = sorted(w * 100 for w, _ in all_kept_boxes)
        heights = sorted(h * 100 for _, h in all_kept_boxes)
        n = len(widths)

        def pct(arr, p):
            idx = int(p * (n - 1))
            return arr[idx]

        print(f"  Width:  p10={pct(widths, 0.10):.2f}% | p50 (median)={pct(widths, 0.50):.2f}% | p90={pct(widths, 0.90):.2f}%")
        print(f"  Height: p10={pct(heights, 0.10):.2f}% | p50 (median)={pct(heights, 0.50):.2f}% | p90={pct(heights, 0.90):.2f}%")

        # Histogram bins by max dimension
        max_dims = [max(w * 100, h * 100) for w, h in all_kept_boxes]
        bins = [(0, 1), (1, 2), (2, 5), (5, 10), (10, 100)]
        print("\nMax Dimension Histogram (% of image):")
        for low, high in bins:
            count = sum(1 for d in max_dims if low <= d < high)
            bar = "#" * int(count / max(1, len(max_dims)) * 40)
            print(f"  [{low:2d}% - {high:3d}%): {count:5d} ({count / len(max_dims) * 100:5.1f}%) | {bar}")
    print("=" * 65 + "\n")


def prepare_dataset(
    annotations_path: Path,
    out_dir: Path,
    workers: int = 8,
    skip_download: bool = False,
    seed: int = 0,
    split_ratios: Tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> None:
    if not annotations_path.exists():
        print(f"ERROR: Annotations file not found: {annotations_path}")
        print("To obtain the UAVVaste dataset, run:")
        print("    git clone https://github.com/UAVVaste/UAVVaste ml/data/UAVVaste")
        sys.exit(1)

    print(f"Loading annotations from: {annotations_path}")
    with open(annotations_path, "r", encoding="utf-8") as f:
        coco_data = json.load(f)

    images = coco_data.get("images", [])
    annotations = coco_data.get("annotations", [])
    categories = coco_data.get("categories", [])

    print(f"Found {len(images)} images, {len(annotations)} annotations, {len(categories)} categories.")

    # Read categories from file
    category_map: Dict[int, int] = {}
    names_dict: Dict[int, str] = {}
    for idx, cat in enumerate(sorted(categories, key=lambda c: c["id"])):
        category_map[cat["id"]] = idx
        names_dict[idx] = cat["name"]

    # Map annotations to image_id
    ann_by_image: Dict[int, List[Dict[str, Any]]] = {}
    for ann in annotations:
        ann_by_image.setdefault(ann["image_id"], []).append(ann)

    # Group images by flight
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for img in images:
        grp = extract_flight_group(img["file_name"])
        groups.setdefault(grp, []).append(img)

    splits, counts, targets = greedy_group_split(groups, split_ratios, seed=seed)
    split_names = ["train", "val", "test"]

    # Setup directories
    images_dir = out_dir / "images"
    labels_dir = out_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    # Convert annotations to YOLO labels
    dropped_boxes = 0
    all_kept_boxes: List[Tuple[float, float]] = []

    for img in images:
        img_id = img["id"]
        w = img["width"]
        h = img["height"]
        stem = Path(img["file_name"]).stem
        label_file = labels_dir / f"{stem}.txt"

        lines = []
        for ann in ann_by_image.get(img_id, []):
            cat_id = ann.get("category_id", 0)
            cat_idx = category_map.get(cat_id, 0)
            res = convert_coco_bbox(ann["bbox"], w, h, category_idx=cat_idx, min_size=1.0)
            if res is None:
                dropped_boxes += 1
                continue
            c_idx, cx, cy, nw, nh = res
            lines.append(f"{c_idx} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            all_kept_boxes.append((nw, nh))

        with open(label_file, "w", encoding="utf-8") as f:
            if lines:
                f.write("\n".join(lines) + "\n")
            else:
                f.write("")

    # Write train.txt, val.txt, test.txt
    for name, split_groups in zip(split_names, splits):
        txt_path = out_dir / f"{name}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            for _, imgs in split_groups:
                for img in imgs:
                    f.write(f"./images/{img['file_name']}\n")

    # Write dataset.yaml
    dataset_yaml = {
        "path": str(out_dir.as_posix()),
        "train": "train.txt",
        "val": "val.txt",
        "test": "test.txt",
        "names": names_dict if names_dict else {0: "rubbish"},
    }
    with open(out_dir / "dataset.yaml", "w", encoding="utf-8") as f:
        yaml.dump(dataset_yaml, f, sort_keys=False)

    print_dataset_report(split_names, splits, ann_by_image, dropped_boxes, all_kept_boxes)

    if skip_download:
        print("Skipping image download (--skip-download specified). Labels and splits generated.")
        return

    # Image download
    print(f"\nDownloading images to {images_dir} (workers={workers})...")
    failed_downloads: List[Tuple[str, str]] = []
    success_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_single_image, img, images_dir): img["file_name"] for img in images}
        for future in concurrent.futures.as_completed(futures):
            fname, ok, msg = future.result()
            if ok:
                success_count += 1
            else:
                failed_downloads.append((fname, msg))

    print(f"Download complete: {success_count}/{len(images)} succeeded.")
    if failed_downloads:
        print(f"WARNING: {len(failed_downloads)} images failed to download:")
        for fname, err in failed_downloads[:10]:
            print(f"  {fname}: {err}")
        if len(failed_downloads) > 10:
            print(f"  ... and {len(failed_downloads) - 10} more.")


def main():
    parser = argparse.ArgumentParser(description="Prepare UAVVaste dataset for YOLO fine-tuning.")
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path("ml/data/UAVVaste/annotations/annotations.json"),
        help="Path to UAVVaste annotations.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("ml/data/uavvaste_yolo"),
        help="Output directory for YOLO formatted dataset",
    )
    parser.add_argument("--workers", type=int, default=8, help="Download worker threads")
    parser.add_argument("--skip-download", action="store_true", help="Skip image downloading")
    parser.add_argument("--seed", type=int, default=0, help="Random seed for group split")
    parser.add_argument(
        "--split",
        type=float,
        nargs=3,
        default=[0.70, 0.15, 0.15],
        help="Train/val/test split ratios (default: 0.70 0.15 0.15)",
    )

    args = parser.parse_args()
    prepare_dataset(
        annotations_path=args.annotations,
        out_dir=args.out,
        workers=args.workers,
        skip_download=args.skip_download,
        seed=args.seed,
        split_ratios=tuple(args.split),
    )


if __name__ == "__main__":
    main()
