"""Unit tests for UAVVaste dataset preparation and bounding-box conversion."""

import json
from pathlib import Path
import pytest

from ml.uavvaste.prepare_dataset import (
    convert_coco_bbox,
    extract_flight_group,
    greedy_group_split,
)


def test_convert_coco_bbox_math():
    """Verify normal conversion math [x, y, w, h] px -> normalized [cx, cy, w, h]."""
    bbox = [100.0, 200.0, 50.0, 80.0]
    W, H = 1000, 1000
    res = convert_coco_bbox(bbox, W, H, category_idx=0)
    assert res is not None
    cat_idx, cx, cy, nw, nh = res
    assert cat_idx == 0
    assert pytest.approx(cx, abs=1e-5) == (100.0 + 25.0) / 1000.0
    assert pytest.approx(cy, abs=1e-5) == (200.0 + 40.0) / 1000.0
    assert pytest.approx(nw, abs=1e-5) == 50.0 / 1000.0
    assert pytest.approx(nh, abs=1e-5) == 80.0 / 1000.0


def test_convert_coco_bbox_clipping():
    """Verify clipping of boxes that extend beyond the image borders."""
    # Extends to the left
    bbox = [-20.0, 100.0, 50.0, 40.0]
    W, H = 500, 500
    res = convert_coco_bbox(bbox, W, H, category_idx=0)
    assert res is not None
    cat_idx, cx, cy, nw, nh = res
    # Clipped x1=0, x2=30, cw=30 -> cx=15/500, nw=30/500
    assert pytest.approx(cx, abs=1e-5) == 15.0 / 500.0
    assert pytest.approx(nw, abs=1e-5) == 30.0 / 500.0
    assert 0.0 <= cx <= 1.0
    assert 0.0 <= cy <= 1.0
    assert 0.0 <= nw <= 1.0
    assert 0.0 <= nh <= 1.0

    # Extends past bottom-right
    bbox2 = [480.0, 490.0, 50.0, 50.0]
    res2 = convert_coco_bbox(bbox2, W, H, category_idx=1)
    assert res2 is not None
    cat_idx2, cx2, cy2, nw2, nh2 = res2
    assert cat_idx2 == 1
    # x1=480, x2=500, cw=20 -> cx = 490/500, nw = 20/500
    assert pytest.approx(cx2, abs=1e-5) == 490.0 / 500.0
    assert pytest.approx(nw2, abs=1e-5) == 20.0 / 500.0
    # y1=490, y2=500, ch=10 -> cy = 495/500, nh = 10/500
    assert pytest.approx(cy2, abs=1e-5) == 495.0 / 500.0
    assert pytest.approx(nh2, abs=1e-5) == 10.0 / 500.0


def test_convert_coco_bbox_degenerate_dropped():
    """Verify boxes smaller than 1 px or fully outside the frame are dropped."""
    W, H = 640, 480
    # Width < 1 px
    assert convert_coco_bbox([10.0, 10.0, 0.5, 20.0], W, H) is None
    # Height < 1 px
    assert convert_coco_bbox([10.0, 10.0, 20.0, 0.0], W, H) is None
    # Completely outside image boundary
    assert convert_coco_bbox([700.0, 100.0, 50.0, 50.0], W, H) is None
    assert convert_coco_bbox([-100.0, -100.0, 20.0, 20.0], W, H) is None
    # Non-positive image dimensions
    assert convert_coco_bbox([10.0, 10.0, 10.0, 10.0], 0, 480) is None


def test_extract_flight_group():
    """Verify extraction of flight grouping keys from diverse filename styles."""
    assert extract_flight_group("BATCH_d07_img_6400.jpg") == "BATCH_D07"
    assert extract_flight_group("BATCH_s05_img_1260.jpg") == "BATCH_S05"
    assert extract_flight_group("batch_s02_img_114.jpg") == "BATCH_S02"
    assert extract_flight_group("GOPR0038.JPG") == "GOPR"
    assert extract_flight_group("camera_img_1.jpg") == "camera_img"
    assert extract_flight_group("batch_02_img_1480.jpg") == "batch"
    assert extract_flight_group("DJI_0010.JPG") == "DJI"


def test_greedy_group_split_properties():
    """Verify group splitting invariants: no flight leakage, deterministic, non-empty."""
    # Synthetic groups of varying sizes
    groups = {
        f"flight_{i}": [{"id": f"img_{i}_{j}"} for j in range((i % 7) + 1)]
        for i in range(25)
    }

    # Determinism
    splits_a, counts_a, _ = greedy_group_split(groups, split_ratios=(0.70, 0.15, 0.15), seed=42)
    splits_b, counts_b, _ = greedy_group_split(groups, split_ratios=(0.70, 0.15, 0.15), seed=42)

    assert counts_a == counts_b
    for sa, sb in zip(splits_a, splits_b):
        assert [k for k, _ in sa] == [k for k, _ in sb]

    # No leakage: each flight key must appear in exactly one split
    assigned_flights = []
    for split in splits_a:
        # Every split must be non-empty
        assert len(split) > 0
        for grp_key, _ in split:
            assigned_flights.append(grp_key)

    assert len(assigned_flights) == len(set(assigned_flights))
    assert set(assigned_flights) == set(groups.keys())


def test_converter_against_real_annotations():
    """
    Confirm UAVVaste counts against annotations.json:
    772 images, 3718 boxes preserved minus dropped.
    """
    ann_path = Path("ml/data/UAVVaste/annotations/annotations.json")
    if not ann_path.exists():
        pytest.skip(f"UAVVaste annotations not present at {ann_path}")

    with open(ann_path, "r", encoding="utf-8") as f:
        coco_data = json.load(f)

    images = coco_data["images"]
    annotations = coco_data["annotations"]
    assert len(images) == 772
    assert len(annotations) == 3718

    img_by_id = {img["id"]: img for img in images}
    kept = 0
    dropped = 0

    for ann in annotations:
        img = img_by_id[ann["image_id"]]
        res = convert_coco_bbox(ann["bbox"], img["width"], img["height"], category_idx=0)
        if res is None:
            dropped += 1
        else:
            kept += 1

    assert kept + dropped == 3718
    assert kept == 3718
    assert dropped == 0


def test_official_split_distribution():
    """Verify official train/val/test split counts and zero overlap."""
    split_path = Path("ml/data/uavvaste_official_extracted/annotations/train_val_test_distribution_file.json")
    if not split_path.exists():
        pytest.skip(f"Official split file not present at {split_path}")

    from ml.uavvaste.prepare_dataset import load_official_split

    splits = load_official_split(split_path)
    train_imgs = splits["train"]
    val_imgs = splits["val"]
    test_imgs = splits["test"]

    assert len(train_imgs) == 540
    assert len(val_imgs) == 116
    assert len(test_imgs) == 116
    assert len(set(train_imgs)) == 540
    assert len(set(val_imgs)) == 116
    assert len(set(test_imgs)) == 116

    # No leakage between any split
    assert len(set(train_imgs) & set(val_imgs)) == 0
    assert len(set(train_imgs) & set(test_imgs)) == 0
    assert len(set(val_imgs) & set(test_imgs)) == 0


def test_prepare_dataset_synthetic(tmp_path):
    """
    Test end-to-end dataset preparation with a synthetic dataset:
    - validates local image copy
    - validates COCO bbox conversion
    - creates empty label file for images without annotations
    - writes train.txt, val.txt, test.txt from split file
    - writes valid dataset.yaml
    """
    import numpy as np
    from PIL import Image
    from ml.uavvaste.prepare_dataset import prepare_dataset

    src_dir = tmp_path / "raw"
    images_dir = src_dir / "images"
    ann_dir = src_dir / "annotations"
    images_dir.mkdir(parents=True)
    ann_dir.mkdir(parents=True)

    # Create 3 synthetic images: 2 with annotations, 1 without
    for i in range(3):
        im_arr = np.zeros((200, 300, 3), dtype=np.uint8)
        im = Image.fromarray(im_arr)
        im.save(images_dir / f"test_{i}.jpg")

    coco_data = {
        "images": [
            {"id": 10, "file_name": "test_0.jpg", "width": 300, "height": 200},
            {"id": 20, "file_name": "test_1.jpg", "width": 300, "height": 200},
            {"id": 30, "file_name": "test_2.jpg", "width": 300, "height": 200},  # no annotation
        ],
        "annotations": [
            {"id": 100, "image_id": 10, "category_id": 0, "bbox": [50, 40, 60, 80]},
            {"id": 101, "image_id": 20, "category_id": 0, "bbox": [100, 50, 40, 50]},
        ],
        "categories": [{"id": 0, "name": "rubbish"}],
    }
    ann_file = ann_dir / "annotations.json"
    ann_file.write_text(json.dumps(coco_data), encoding="utf-8")

    split_data = {
        "train": ["test_0.jpg"],
        "val": ["test_1.jpg"],
        "test": ["test_2.jpg"],
    }
    split_file = ann_dir / "train_val_test_distribution_file.json"
    split_file.write_text(json.dumps(split_data), encoding="utf-8")

    out_dir = tmp_path / "yolo_out"

    report = prepare_dataset(
        annotations_path=ann_file,
        images_dir=images_dir,
        split_file=split_file,
        out_dir=out_dir,
        workers=2,
    )

    assert report["total_images"] == 3
    assert report["total_annotations"] == 2
    assert report["valid_boxes"] == 2
    assert report["dropped_boxes"] == 0
    assert report["missing_images"] == 0
    assert report["empty_annotation_images"] == 1

    # Verify images copied
    assert (out_dir / "images" / "test_0.jpg").exists()
    assert (out_dir / "images" / "test_1.jpg").exists()
    assert (out_dir / "images" / "test_2.jpg").exists()

    # Verify labels
    label_0 = (out_dir / "labels" / "test_0.txt").read_text(encoding="utf-8").strip()
    label_1 = (out_dir / "labels" / "test_1.txt").read_text(encoding="utf-8").strip()
    label_2 = (out_dir / "labels" / "test_2.txt").read_text(encoding="utf-8").strip()

    assert len(label_0.splitlines()) == 1
    assert label_0.startswith("0 ")
    assert len(label_1.splitlines()) == 1
    # Empty label file for image without annotations
    assert label_2 == ""

    # Verify split txt files
    assert (out_dir / "train.txt").read_text(encoding="utf-8").strip() == "./images/test_0.jpg"
    assert (out_dir / "val.txt").read_text(encoding="utf-8").strip() == "./images/test_1.jpg"
    assert (out_dir / "test.txt").read_text(encoding="utf-8").strip() == "./images/test_2.jpg"

    # Verify dataset.yaml
    assert (out_dir / "dataset.yaml").exists()


