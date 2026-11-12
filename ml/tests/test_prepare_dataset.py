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
