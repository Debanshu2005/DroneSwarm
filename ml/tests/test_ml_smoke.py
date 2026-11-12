"""End-to-end ML smoke test on CPU with a synthetic dataset and from-scratch model."""

import asyncio
from pathlib import Path
import cv2
import numpy as np
import pytest
import yaml

from DroneOS.perception.detector import GarbageDetector
from ml.uavvaste.train import train


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_ml_pipeline_cpu_smoke(tmp_path):
    """
    Train 1 epoch on CPU with a from-scratch YAML model and tiny synthetic dataset.
    Validates dataset.yaml, training loop, validation on test split, and GarbageDetector loading.
    """
    data_dir = tmp_path / "smoke_data"
    images_dir = data_dir / "images"
    labels_dir = data_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    # Create 3 tiny synthetic images with synthetic rubbish boxes
    for i in range(3):
        img_name = f"synth_{i}.jpg"
        img_path = images_dir / img_name
        label_path = labels_dir / f"synth_{i}.txt"

        # Generate a small image with a bright patch
        arr = np.zeros((320, 320, 3), dtype=np.uint8)
        arr[100:150, 100:150] = 255
        cv2.imwrite(str(img_path), arr)

        # Write label: class 0, cx=0.39, cy=0.39, w=0.15, h=0.15
        label_path.write_text("0 0.390625 0.390625 0.156250 0.156250\n", encoding="utf-8")

    # Create train.txt, val.txt, test.txt
    for split in ["train", "val", "test"]:
        split_txt = data_dir / f"{split}.txt"
        split_txt.write_text(
            f"./images/synth_0.jpg\n./images/synth_1.jpg\n./images/synth_2.jpg\n",
            encoding="utf-8",
        )

    # Write dataset.yaml
    dataset_yaml = {
        "path": str(data_dir.as_posix()),
        "train": "train.txt",
        "val": "val.txt",
        "test": "test.txt",
        "names": {0: "rubbish"},
    }
    dataset_yaml_path = data_dir / "dataset.yaml"
    with open(dataset_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(dataset_yaml, f)

    runs_dir = tmp_path / "runs"

    # Train 1 epoch on CPU with from-scratch yolo11n.yaml
    model, val_results = train(
        data=dataset_yaml_path,
        model_name="yolo11n.yaml",
        imgsz=320,
        epochs=1,
        batch=2,
        patience=5,
        device="cpu",
        project=str(runs_dir),
        name="smoke_run",
        install=False,
    )

    assert val_results is not None

    # Verify best.pt or last.pt exists
    weights_path = runs_dir / "smoke_run" / "weights" / "last.pt"
    if not weights_path.exists():
        weights_path = runs_dir / "smoke_run" / "weights" / "best.pt"
    assert weights_path.exists(), "Trained weights file not produced"

    # Test GarbageDetector loading and inference with the trained weights
    detector = GarbageDetector(
        weights_path=str(weights_path),
        imgsz=320,
        confidence=0.01,
        device="cpu",
    )
    assert detector.load() is True

    test_img = np.zeros((320, 320, 3), dtype=np.uint8)
    test_img[100:150, 100:150] = 255
    dets = asyncio.run(detector.detect(test_img))
    assert isinstance(dets, list)
    detector.close()

