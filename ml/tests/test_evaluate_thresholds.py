"""Unit tests for evaluate_thresholds calculation and file generation."""

from pathlib import Path
import pytest

from ml.uavvaste.evaluate_thresholds import THRESHOLDS, generate_charts


def test_thresholds_list():
    """Verify standard evaluated thresholds."""
    assert THRESHOLDS == [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def test_generate_charts_creates_images(tmp_path):
    """Verify that chart generator outputs png charts."""
    sample_results = [
        {
            "confidence": 0.35,
            "precision": 0.88,
            "recall": 0.78,
            "f1_score": 0.83,
        },
        {
            "confidence": 0.50,
            "precision": 0.92,
            "recall": 0.74,
            "f1_score": 0.82,
        },
    ]

    generate_charts(sample_results, tmp_path)
    assert (tmp_path / "threshold_tradeoffs.png").exists()
    assert (tmp_path / "pr_curve.png").exists()

