import json
import cv2
import numpy as np

from DroneOS.perception.types import Detection
from scripts import eval_custom_dataset as ecd


def _make_synthetic_dataset(root):
    img_dir = root / "images"
    lbl_dir = root / "labels"
    img_dir.mkdir(parents=True)
    lbl_dir.mkdir(parents=True)

    # Positive image (640x640) with 1 large GT (>40px) and 1 tiny GT (<10px)
    cv2.imwrite(str(img_dir / "pos.jpg"), np.zeros((640, 640, 3), dtype=np.uint8))
    (lbl_dir / "pos.txt").write_text(
        "0 0.5 0.5 0.1 0.1\n"       # 64x64 px -> >40
        "0 0.1 0.1 0.01 0.01\n",    # 6.4x6.4 px -> <10
        encoding="utf-8",
    )

    # Negative image (no label file -> 0 GT boxes)
    cv2.imwrite(str(img_dir / "neg.jpg"), np.zeros((640, 640, 3), dtype=np.uint8))

    # Optional meta.csv
    (root / "meta.csv").write_text(
        "image,altitude_m,object_type\n"
        "pos.jpg,10,bottle\n"
        "neg.jpg,10,none\n",
        encoding="utf-8",
    )


def test_empty_folder_returns_error(tmp_path):
    empty_dir = tmp_path / "empty_ds"
    empty_dir.mkdir()
    assert ecd.main(["--data", str(empty_dir)]) == 2

    (empty_dir / "images").mkdir()
    assert ecd.main(["--data", str(empty_dir)]) == 2


def test_eval_custom_dataset_synthetic_side_by_side(tmp_path, monkeypatch):
    ds_dir = tmp_path / "ds"
    _make_synthetic_dataset(ds_dir)

    pt_w = tmp_path / "m.pt"
    onnx_w = tmp_path / "m.onnx"
    pt_w.write_bytes(b"pt")
    onnx_w.write_bytes(b"onnx")
    out_json = tmp_path / "out.json"

    class FakeDetector:
        def __init__(self, weights_path, **kwargs):
            self.is_onnx = str(weights_path).endswith(".onnx")
            self.last_timing = {"max_raw_conf": 0.80}
            self._call = 0

        def load(self):
            return True

        def _sync_predict(self, image):
            self._call += 1
            # First call is neg.jpg (alphabetical), second call is pos.jpg
            if self._call == 1:
                # 1 false positive on neg.jpg at conf 0.40 for pt only
                if not self.is_onnx:
                    return [Detection(bbox_xyxy=(10.0, 10.0, 40.0, 40.0), confidence=0.40, label="0")]
                return []
            # pos.jpg: matches the 64x64 GT at (288,288,352,352), misses the tiny 6.4x6.4 GT
            return [Detection(bbox_xyxy=(288.0, 288.0, 352.0, 352.0), confidence=0.80, label="0")]

        def close(self):
            pass

    monkeypatch.setattr(ecd, "GarbageDetector", FakeDetector)
    rc = ecd.main(
        [
            "--data",
            str(ds_dir),
            "--weights",
            str(pt_w),
            str(onnx_w),
            "--conf-list",
            "0.25,0.55",
            "--output",
            str(out_json),
        ]
    )
    assert rc == 0
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["num_images"] == 2
    assert rep["num_negatives"] == 1
    assert rep["num_gt"] == 2
    assert len(rep["results"]) == 2

    # At conf=0.25: PT has 1 TP, 1 FP (on neg.jpg), 1 FN (tiny GT) -> P=0.5, R=0.5
    pt_025 = rep["results"][0]["by_conf"]["0.25"]
    assert (pt_025["tp"], pt_025["fp"], pt_025["fn"]) == (1, 1, 1)
    assert pt_025["recall_by_size"][">40"]["recall"] == 1.0
    assert pt_025["recall_by_size"]["<10"]["recall"] == 0.0
    assert pt_025["recall_by_altitude"]["10"]["recall"] == 0.5
    assert pt_025["recall_by_object_type"]["bottle"]["recall"] == 0.5
    assert len(pt_025["worst_misses"]) == 1
    assert pt_025["worst_misses"][0]["image"] == "pos.jpg"

    # At conf=0.55: PT FP (0.40) is filtered out -> P=1.0, R=0.5
    pt_055 = rep["results"][0]["by_conf"]["0.55"]
    assert (pt_055["tp"], pt_055["fp"], pt_055["fn"]) == (1, 0, 1)
