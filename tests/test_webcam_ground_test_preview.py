import cv2
import numpy as np
import pytest

from DroneOS.perception.types import Detection
from scripts import webcam_ground_test as wgt


def test_draw_preview_overlay_boxes_weak_tier_and_stale():
    base = np.zeros((240, 320, 3), dtype=np.uint8)
    strong = [Detection(bbox_xyxy=(40.0, 110.0, 90.0, 160.0), confidence=0.72, label="rubbish")]
    weak = [Detection(bbox_xyxy=(150.0, 120.0, 200.0, 170.0), confidence=0.22, label="rubbish")]

    # Fresh overlay with both strong (green) and weak (orange)
    fresh = wgt.draw_preview_overlay(
        base,
        strong,
        weak,
        capture_fps=30.0,
        inference_ms=42.0,
        frame_age_ms=12.0,
        gate_status="OK",
        altitude_m=6.0,
        tilt_deg=0.0,
        num_tracks=1,
        num_alerts=0,
        det_age_s=0.2,
        show_weak=True,
    )
    assert base.sum() == 0  # input image is not mutated
    assert fresh.shape == base.shape
    # Strong green box border pixel at top-left edge (x=40, y=110) -> BGR (0, 255, 0)
    assert tuple(int(v) for v in fresh[110, 40]) == (0, 255, 0)
    # Weak orange dashed box pixel at (x=150, y=120) -> BGR (0, 165, 255)
    assert tuple(int(v) for v in fresh[120, 150]) == (0, 165, 255)

    # Weak tier toggled off
    no_weak = wgt.draw_preview_overlay(base, strong, weak, det_age_s=0.2, show_weak=False)
    assert tuple(int(v) for v in no_weak[120, 150]) == (0, 0, 0)

    # Stale (>1.5s) fades strong box color to muted green (120, 180, 120)
    stale = wgt.draw_preview_overlay(base, strong, weak, det_age_s=2.0, show_weak=True)
    assert tuple(int(v) for v in stale[110, 40]) == (120, 180, 120)


def test_supports_imshow_returns_false_on_headless_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise cv2.error("The function is not implemented")

    monkeypatch.setattr(cv2, "namedWindow", _raise)
    assert wgt.supports_imshow() is False


@pytest.mark.asyncio
async def test_preview_headless_fallback_saves_annotated_and_isolates_weak_tier(tmp_path, monkeypatch):
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    cv2.imwrite(str(img_dir / "sample.jpg"), np.full((120, 160, 3), 80, dtype=np.uint8))
    save_dir = tmp_path / "saved"
    report_path = tmp_path / "report.json"

    class FakeDetector:
        def __init__(self, **kwargs):
            self.device = "cpu"
            self.confidence = kwargs.get("confidence", 0.55)
            self.last_timing = {"preprocess_ms": 1.0, "inference_ms": 5.0, "postprocess_filter_ms": 1.0}

        def load(self):
            return True

        def warmup(self, n):
            pass

        async def detect(self, img):
            # Only weak-tier detection (0.25 < 0.55): must NOT reach aggregator/alerts
            return [Detection(bbox_xyxy=(20.0, 20.0, 50.0, 50.0), confidence=0.25, label="rubbish")]

        def close(self):
            pass

    monkeypatch.setattr(wgt, "GarbageDetector", FakeDetector)
    monkeypatch.setattr(wgt, "run_perception_preflight", lambda cfg, repo_root=None: ("OK", ""))
    monkeypatch.setattr(wgt, "supports_imshow", lambda: False)

    args = wgt.parse_args(
        [
            "--source",
            "folder",
            "--folder",
            str(img_dir),
            "--preview",
            "--preview-min-conf",
            "0.15",
            "--conf",
            "0.55",
            "--duration",
            "0.6",
            "--max-fps",
            "5.0",
            "--save-frames",
            str(save_dir),
            "--report",
            str(report_path),
        ]
    )
    rc = await wgt.run_ground_test(args)
    assert rc == 0
    saved_files = list(save_dir.glob("*.jpg"))
    assert len(saved_files) >= 1

    import json
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    assert rep["confirmed_alerts"] == []
    assert all(row["num_detections"] == 0 for row in rep["detections_per_frame"])
