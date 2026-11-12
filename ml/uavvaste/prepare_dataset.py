"""Prepare UAVVaste dataset for YOLO training with official train/val/test split."""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import random
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
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
    group_items = sorted(groups.items(), key=lambda x: x[0])
    rng.shuffle(group_items)

    total_imgs = sum(len(v) for _, v in group_items)
    targets = [total_imgs * r for r in split_ratios]
    splits: List[List[Tuple[str, List[Dict[str, Any]]]]] = [[] for _ in split_ratios]
    counts = [0 for _ in split_ratios]

    for group_key, imgs in group_items:
        empty_splits = [i for i, c in enumerate(counts) if c == 0]
        if empty_splits:
            best_idx = max(empty_splits, key=lambda i: targets[i])
        else:
            best_idx = max(range(len(splits)), key=lambda i: (targets[i] - counts[i]) / max(targets[i], 1e-6))

        splits[best_idx].append((group_key, imgs))
        counts[best_idx] += len(imgs)

    return splits, counts, targets


def verify_image_file(image_path: Path, expected_w: int, expected_h: int) -> Tuple[bool, str, Optional[Tuple[int, int]]]:
    """
    Verify image existence and check dimensions against expected.
    Returns (is_valid, message, (actual_w, actual_h)).
    """
    if not image_path.exists():
        return False, "File does not exist", None
    if image_path.stat().st_size == 0:
        return False, "File is empty (0 bytes)", None

    if Image is not None:
        try:
            with Image.open(image_path) as im:
                w, h = im.size
                if w != expected_w or h != expected_h:
                    return False, f"Dimension mismatch: expected {expected_w}x{expected_h}, got {w}x{h}", (w, h)
                return True, "OK", (w, h)
        except Exception as e:
            return False, f"Corrupted image (PIL decode error: {e})", None
    elif cv2 is not None:
        try:
            img = cv2.imread(str(image_path))
            if img is None:
                return False, "cv2.imread failed to decode", None
            h, w = img.shape[:2]
            if w != expected_w or h != expected_h:
                return False, f"Dimension mismatch: expected {expected_w}x{expected_h}, got {w}x{h}", (w, h)
            return True, "OK", (w, h)
        except Exception as e:
            return False, f"Corrupted image (cv2 decode error: {e})", None

    return True, "OK (unverified decode)", (expected_w, expected_h)


def process_and_copy_image(
    src_path: Path,
    dst_path: Path,
    expected_w: int,
    expected_h: int,
) -> Tuple[str, bool, str]:
    """
    Validate source image, copy to destination if needed, and verify.
    Returns (file_name, success, error_message).
    """
    file_name = src_path.name
    if not src_path.exists():
        return file_name, False, f"Source image missing: {src_path}"

    valid, msg, _ = verify_image_file(src_path, expected_w, expected_h)
    if not valid:
        return file_name, False, msg

    try:
        if not dst_path.exists() or dst_path.stat().st_size != src_path.stat().st_size:
            shutil.copy2(src_path, dst_path)
        return file_name, True, "OK"
    except Exception as e:
        return file_name, False, f"Copy error: {e}"


def load_official_split(split_file: Path) -> Dict[str, List[str]]:
    """Load and validate official train/val/test split JSON."""
    if not split_file.exists():
        raise FileNotFoundError(f"Official split file not found: {split_file}")

    with open(split_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid split file format: expected JSON object, got {type(data).__name__}")

    for key in ["train", "val", "test"]:
        if key not in data:
            raise KeyError(f"Missing required split '{key}' in split file: {split_file}")
        if not isinstance(data[key], list):
            raise TypeError(f"Split '{key}' must be a list of filenames")

    return data


def print_preparation_report(
    annotations_path: Path,
    images_dir: Optional[Path],
    split_file: Optional[Path],
    out_dir: Path,
    total_coco_images: int,
    total_coco_annotations: int,
    categories: Dict[int, str],
    duplicate_filenames: List[str],
    missing_images: List[str],
    dimension_mismatches: List[Tuple[str, str]],
    corrupted_images: List[str],
    empty_annotation_images: List[str],
    valid_boxes: int,
    invalid_boxes: int,
    split_stats: Dict[str, Dict[str, Any]],
    all_kept_boxes: List[Tuple[float, float]],
) -> None:
    """Print comprehensive dataset preparation and validation report."""
    print("\n" + "=" * 70)
    print("UAVVaste Dataset Preparation Report")
    print("=" * 70)
    print(f"Annotations File:    {annotations_path}")
    print(f"Images Directory:    {images_dir}")
    print(f"Split Distribution:  {split_file if split_file else 'Computed Flight-Group Split'}")
    print(f"Output Directory:    {out_dir}")
    print("-" * 70)
    print("Validation Summary:")
    print(f"  Total COCO Images:         {total_coco_images:,}")
    print(f"  Total COCO Annotations:    {total_coco_annotations:,}")
    cats_str = ", ".join(f"{k}: {v}" for k, v in categories.items())
    print(f"  Categories:                {len(categories)} ({cats_str})")
    print(f"  Duplicate Filenames:       {len(duplicate_filenames)}")
    if duplicate_filenames:
        for fn in duplicate_filenames[:5]:
            print(f"    - {fn}")
    print(f"  Missing Images on Disk:    {len(missing_images)}")
    if missing_images:
        for fn in missing_images[:5]:
            print(f"    - {fn}")
    print(f"  Dimension Mismatches:      {len(dimension_mismatches)}")
    if dimension_mismatches:
        for fn, err in dimension_mismatches[:5]:
            print(f"    - {fn}: {err}")
    print(f"  Corrupted Images:          {len(corrupted_images)}")
    if corrupted_images:
        for fn in corrupted_images[:5]:
            print(f"    - {fn}")
    print(f"  Images Without Boxes:      {len(empty_annotation_images)} (empty label files created)")
    print(f"  Valid Boxes Converted:     {valid_boxes:,}")
    print(f"  Invalid / Dropped Boxes:   {invalid_boxes}")
    print("-" * 70)
    print("Split Statistics:")
    print(f"  {'Split':<10} {'Images':<10} {'Instances':<12} {'Avg Boxes/Img':<16} {'Split Ratio':<12}")
    print("  " + "-" * 62)

    total_split_imgs = sum(s["images"] for s in split_stats.values())
    total_split_inst = sum(s["instances"] for s in split_stats.values())

    for name, s in split_stats.items():
        n_img = s["images"]
        n_inst = s["instances"]
        avg_boxes = (n_inst / n_img) if n_img > 0 else 0.0
        pct = (n_img / total_split_imgs * 100.0) if total_split_imgs > 0 else 0.0
        print(f"  {name:<10} {n_img:<10} {n_inst:<12} {avg_boxes:<16.2f} {pct:>6.1f}%")

    print("  " + "-" * 62)
    overall_avg = (total_split_inst / total_split_imgs) if total_split_imgs > 0 else 0.0
    print(f"  {'Total':<10} {total_split_imgs:<10} {total_split_inst:<12} {overall_avg:<16.2f} {100.0:>6.1f}%")
    print("-" * 70)

    # Box-size histogram
    if all_kept_boxes:
        print("Object Relative Dimensions Distribution (% of image width & height):")
        widths = sorted(w * 100 for w, _ in all_kept_boxes)
        heights = sorted(h * 100 for _, h in all_kept_boxes)
        n = len(widths)

        def pct(arr, p):
            idx = int(p * (n - 1))
            return arr[idx]

        print(f"  Width:  p10={pct(widths, 0.10):.2f}% | p50 (median)={pct(widths, 0.50):.2f}% | p90={pct(widths, 0.90):.2f}%")
        print(f"  Height: p10={pct(heights, 0.10):.2f}% | p50 (median)={pct(heights, 0.50):.2f}% | p90={pct(heights, 0.90):.2f}%")

        max_dims = [max(w * 100, h * 100) for w, h in all_kept_boxes]
        bins = [(0, 1), (1, 2), (2, 5), (5, 10), (10, 100)]
        print("\nMax Dimension Histogram (% of image):")
        for low, high in bins:
            count = sum(1 for d in max_dims if low <= d < high)
            bar = "#" * int(count / max(1, len(max_dims)) * 35)
            print(f"  [{low:2d}% - {high:3d}%): {count:5d} ({count / len(max_dims) * 100:5.1f}%) | {bar}")
    print("=" * 70 + "\n")


def prepare_dataset(
    annotations_path: Path = Path("ml/data/uavvaste_official_extracted/annotations/annotations.json"),
    out_dir: Path = Path("ml/data/uavvaste_yolo"),
    images_dir: Optional[Path] = None,
    split_file: Optional[Path] = None,
    workers: int = 8,
    skip_download: bool = True,
    seed: int = 0,
    split_ratios: Tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> Dict[str, Any]:
    """
    Prepare UAVVaste dataset for YOLO fine-tuning.
    Reads local extracted images, converts COCO annotations, applies the official
    train/val/test split, validates all images and coordinates, and writes output files.
    """
    annotations_path = Path(annotations_path)
    out_dir = Path(out_dir)

    # 1. Resolve annotations path
    if not annotations_path.exists():
        fallback = Path("ml/data/UAVVaste/annotations/annotations.json")
        if fallback.exists():
            annotations_path = fallback
        else:
            raise FileNotFoundError(f"Annotations file not found: {annotations_path}")

    # 2. Resolve images directory
    if images_dir is None:
        candidate_img_dir = annotations_path.parent.parent / "images"
        if candidate_img_dir.exists():
            images_dir = candidate_img_dir
        else:
            default_cand = Path("ml/data/uavvaste_official_extracted/images")
            if default_cand.exists():
                images_dir = default_cand
            else:
                images_dir = annotations_path.parent / "images"
    images_dir = Path(images_dir)

    # 3. Resolve split file
    if split_file is None:
        candidate_split = annotations_path.parent / "train_val_test_distribution_file.json"
        if candidate_split.exists():
            split_file = candidate_split
        else:
            default_split = Path("ml/data/uavvaste_official_extracted/annotations/train_val_test_distribution_file.json")
            if default_split.exists():
                split_file = default_split
    if split_file is not None:
        split_file = Path(split_file)
        if not split_file.exists():
            split_file = None

    print(f"Loading annotations from: {annotations_path}")
    with open(annotations_path, "r", encoding="utf-8") as f:
        coco_data = json.load(f)

    images = coco_data.get("images", [])
    annotations = coco_data.get("annotations", [])
    categories = coco_data.get("categories", [])

    # Check for duplicate filenames
    seen_fnames: Set[str] = set()
    duplicate_filenames: List[str] = []
    for img in images:
        fn = img.get("file_name", "")
        if fn in seen_fnames:
            duplicate_filenames.append(fn)
        seen_fnames.add(fn)

    # Read categories
    category_map: Dict[int, int] = {}
    names_dict: Dict[int, str] = {}
    for idx, cat in enumerate(sorted(categories, key=lambda c: c["id"])):
        category_map[cat["id"]] = idx
        names_dict[idx] = cat["name"]

    if not names_dict:
        names_dict = {0: "rubbish"}

    # Map annotations to image_id
    ann_by_image: Dict[int, List[Dict[str, Any]]] = {}
    for ann in annotations:
        ann_by_image.setdefault(ann["image_id"], []).append(ann)

    image_by_filename: Dict[str, Dict[str, Any]] = {img["file_name"]: img for img in images}

    # Setup output directories
    out_images_dir = out_dir / "images"
    out_labels_dir = out_dir / "labels"
    out_images_dir.mkdir(parents=True, exist_ok=True)
    out_labels_dir.mkdir(parents=True, exist_ok=True)

    # Validate and copy images
    print(f"Validating and copying images from {images_dir} to {out_images_dir} (workers={workers})...")
    missing_images: List[str] = []
    dimension_mismatches: List[Tuple[str, str]] = []
    corrupted_images: List[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                process_and_copy_image,
                images_dir / img["file_name"],
                out_images_dir / img["file_name"],
                img.get("width", 0),
                img.get("height", 0),
            ): img["file_name"]
            for img in images
        }
        for future in concurrent.futures.as_completed(futures):
            fn, ok, msg = future.result()
            if not ok:
                if "missing" in msg.lower() or "exist" in msg.lower():
                    missing_images.append(fn)
                elif "mismatch" in msg.lower():
                    dimension_mismatches.append((fn, msg))
                else:
                    corrupted_images.append(fn)

    # Convert annotations to YOLO labels
    dropped_boxes = 0
    valid_boxes = 0
    all_kept_boxes: List[Tuple[float, float]] = []
    empty_annotation_images: List[str] = []

    for img in images:
        img_id = img["id"]
        w = img["width"]
        h = img["height"]
        stem = Path(img["file_name"]).stem
        label_file = out_labels_dir / f"{stem}.txt"

        img_anns = ann_by_image.get(img_id, [])
        if not img_anns:
            empty_annotation_images.append(img["file_name"])
            label_file.write_text("", encoding="utf-8")
            continue

        lines = []
        for ann in img_anns:
            cat_id = ann.get("category_id", 0)
            cat_idx = category_map.get(cat_id, 0)
            res = convert_coco_bbox(ann["bbox"], w, h, category_idx=cat_idx, min_size=1.0)
            if res is None:
                dropped_boxes += 1
                continue
            c_idx, cx, cy, nw, nh = res
            lines.append(f"{c_idx} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            valid_boxes += 1
            all_kept_boxes.append((nw, nh))

        with open(label_file, "w", encoding="utf-8") as f:
            if lines:
                f.write("\n".join(lines) + "\n")
            else:
                f.write("")

    # Determine splits: official split if file provided, else greedy group split
    split_stats: Dict[str, Dict[str, Any]] = {}

    if split_file is not None and split_file.exists():
        print(f"Using official train/val/test split from: {split_file}")
        official_splits = load_official_split(split_file)
        split_names = ["train", "val", "test"]

        for name in split_names:
            file_list = official_splits.get(name, [])
            txt_path = out_dir / f"{name}.txt"
            inst_count = 0
            with open(txt_path, "w", encoding="utf-8") as f:
                for fn in file_list:
                    f.write(f"./images/{fn}\n")
                    if fn in image_by_filename:
                        inst_count += len(ann_by_image.get(image_by_filename[fn]["id"], []))

            split_stats[name] = {
                "images": len(file_list),
                "instances": inst_count,
            }
    else:
        print("No official split file provided; computing flight-group split...")
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for img in images:
            grp = extract_flight_group(img["file_name"])
            groups.setdefault(grp, []).append(img)

        splits, counts, targets = greedy_group_split(groups, split_ratios, seed=seed)
        split_names = ["train", "val", "test"]

        for name, split_groups in zip(split_names, splits):
            txt_path = out_dir / f"{name}.txt"
            inst_count = 0
            img_count = 0
            with open(txt_path, "w", encoding="utf-8") as f:
                for _, grp_imgs in split_groups:
                    for img in grp_imgs:
                        img_count += 1
                        f.write(f"./images/{img['file_name']}\n")
                        inst_count += len(ann_by_image.get(img["id"], []))

            split_stats[name] = {
                "images": img_count,
                "instances": inst_count,
            }

    # Write dataset.yaml
    dataset_yaml = {
        "path": str(out_dir.as_posix()),
        "train": "train.txt",
        "val": "val.txt",
        "test": "test.txt",
        "names": names_dict,
    }
    with open(out_dir / "dataset.yaml", "w", encoding="utf-8") as f:
        yaml.dump(dataset_yaml, f, sort_keys=False)

    print_preparation_report(
        annotations_path=annotations_path,
        images_dir=images_dir,
        split_file=split_file,
        out_dir=out_dir,
        total_coco_images=len(images),
        total_coco_annotations=len(annotations),
        categories=names_dict,
        duplicate_filenames=duplicate_filenames,
        missing_images=missing_images,
        dimension_mismatches=dimension_mismatches,
        corrupted_images=corrupted_images,
        empty_annotation_images=empty_annotation_images,
        valid_boxes=valid_boxes,
        invalid_boxes=dropped_boxes,
        split_stats=split_stats,
        all_kept_boxes=all_kept_boxes,
    )

    return {
        "total_images": len(images),
        "total_annotations": len(annotations),
        "valid_boxes": valid_boxes,
        "dropped_boxes": dropped_boxes,
        "missing_images": len(missing_images),
        "dimension_mismatches": len(dimension_mismatches),
        "empty_annotation_images": len(empty_annotation_images),
        "split_stats": split_stats,
        "dataset_yaml": out_dir / "dataset.yaml",
    }


def main():
    parser = argparse.ArgumentParser(description="Prepare UAVVaste dataset for YOLO fine-tuning.")
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path("ml/data/uavvaste_official_extracted/annotations/annotations.json"),
        help="Path to UAVVaste annotations.json",
    )
    parser.add_argument(
        "--images-dir",
        "--images",
        type=Path,
        default=Path("ml/data/uavvaste_official_extracted/images"),
        help="Path to directory containing extracted source images",
    )
    parser.add_argument(
        "--split-file",
        "--splits",
        type=Path,
        default=Path("ml/data/uavvaste_official_extracted/annotations/train_val_test_distribution_file.json"),
        help="Path to official train/val/test split distribution JSON",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("ml/data/uavvaste_yolo"),
        help="Output directory for YOLO formatted dataset",
    )
    parser.add_argument("--workers", type=int, default=8, help="Worker threads for copying and verification")
    parser.add_argument("--skip-download", action="store_true", default=True, help="Skip downloading (local images used)")
    parser.add_argument("--seed", type=int, default=0, help="Random seed (used if official split file is omitted)")
    parser.add_argument(
        "--split",
        type=float,
        nargs=3,
        default=[0.70, 0.15, 0.15],
        help="Train/val/test split ratios (used if official split file is omitted)",
    )

    args = parser.parse_args()
    prepare_dataset(
        annotations_path=args.annotations,
        images_dir=args.images_dir,
        split_file=args.split_file,
        out_dir=args.out,
        workers=args.workers,
        skip_download=args.skip_download,
        seed=args.seed,
        split_ratios=tuple(args.split),
    )


if __name__ == "__main__":
    main()
