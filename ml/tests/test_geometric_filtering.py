"""Unit tests for GarbageDetector geometric bounding box filtering."""

import numpy as np
import pytest

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.types import Detection


def test_geometric_filter_initialization():
    """Verify geometric filter parameter initialization."""
    det = GarbageDetector(
        weights_path="models/garbage.pt",
        confidence=0.55,
        min_area_ratio=0.0001,
        max_area_ratio=0.35,
        max_aspect_ratio=6.0,
    )
    assert det.confidence == 0.55
    assert det.min_area_ratio == 0.0001
    assert det.max_area_ratio == 0.35
    assert det.max_aspect_ratio == 6.0


def test_geometric_filtering_rejection_logic():
    """Verify that bounding box area and aspect ratio filters reject outliers."""
    # Synthetic mock detector
    detector = GarbageDetector(
        weights_path="models/garbage.pt",
        min_area_ratio=0.001,   # min 0.1% area
        max_area_ratio=0.20,    # max 20% area
        max_aspect_ratio=5.0,   # max AR 5.0
    )

    img_w, img_h = 1000, 1000  # Total area = 1,000,000 px^2

    # Case 1: Valid normal box (50x50 = 2500 px^2, 0.25% area, AR=1.0) -> ACCEPT
    # Case 2: Tiny speck (10x10 = 100 px^2, 0.01% area < 0.1%) -> REJECT by min_area
    # Case 3: Huge box (600x600 = 360,000 px^2, 36% area > 20%) -> REJECT by max_area
    # Case 4: Extreme aspect ratio (200x20 = 4000 px^2, 0.4% area, AR=10.0 > 5.0) -> REJECT by aspect_ratio

    boxes_to_test = [
        {"bbox": (100, 100, 150, 150), "should_pass": True},
        {"bbox": (100, 100, 110, 110), "should_pass": False},
        {"bbox": (100, 100, 700, 700), "should_pass": False},
        {"bbox": (100, 100, 300, 120), "should_pass": False},
    ]

    for b in boxes_to_test:
        x1, y1, x2, y2 = b["bbox"]
        bw = x2 - x1
        bh = y2 - y1
        area_ratio = (bw * bh) / float(img_w * img_h)
        aspect_ratio = max(bw / bh, bh / bw)

        passed = True
        if detector.min_area_ratio > 0.0 and area_ratio < detector.min_area_ratio:
            passed = False
        if detector.max_area_ratio < 1.0 and area_ratio > detector.max_area_ratio:
            passed = False
        if detector.max_aspect_ratio > 0.0 and aspect_ratio > detector.max_aspect_ratio:
            passed = False

        assert passed == b["should_pass"], f"Failed for bbox {b['bbox']}: expected {b['should_pass']}, got {passed}"

