"""Unit tests for training CLI arguments, worker configuration, and train_kwargs."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from ml.uavvaste.train import build_parser, train


def test_train_cli_parser_defaults():
    """Verify CLI parser sets workers=0 by default and keeps batch configurable."""
    parser = build_parser()
    args = parser.parse_args([])

    assert args.workers == 0, f"Expected default workers=0 for Windows memory safety, got {args.workers}"
    assert args.batch == -1
    assert args.imgsz == 1280
    assert args.epochs == 100
    assert args.patience == 30
    assert args.model == "yolo26n.pt"
    assert args.install is False


def test_train_cli_parser_overrides():
    """Verify CLI arguments can override workers, batch, and other parameters."""
    parser = build_parser()
    args = parser.parse_args(["--workers", "4", "--batch", "2", "--epochs", "50", "--device", "cuda:0"])

    assert args.workers == 4
    assert args.batch == 2
    assert args.epochs == 50
    assert args.device == "cuda:0"


def test_train_passes_workers_and_cache_to_ultralytics(tmp_path):
    """Verify train() injects workers=0 and cache=False into model.train and model.val kwargs."""
    # Create a dummy dataset.yaml
    fake_yaml = tmp_path / "dataset.yaml"
    fake_yaml.write_text("path: fake\ntrain: train.txt\nval: val.txt\ntest: test.txt\n", encoding="utf-8")

    mock_model = MagicMock()
    mock_model.train.return_value = MagicMock()
    mock_val_results = MagicMock()
    mock_val_results.box = MagicMock(mp=0.8, mr=0.7, map50=0.75, map=0.5)
    mock_model.val.return_value = mock_val_results

    with patch("ml.uavvaste.train.get_yolo_model", return_value=(mock_model, "yolo11n.pt")):
        model, val_results = train(
            data=fake_yaml,
            model_name="yolo11n.pt",
            imgsz=640,
            epochs=1,
            batch=2,
            workers=0,
            patience=5,
            device="cpu",
            project=str(tmp_path / "runs"),
            name="test_run",
            install=False,
        )

    # Verify train was called with workers=0 and cache=False
    mock_model.train.assert_called_once()
    train_kwargs = mock_model.train.call_args.kwargs

    assert train_kwargs["workers"] == 0, "workers must be 0 in model.train kwargs"
    assert train_kwargs["cache"] is False, "cache must be False to prevent RAM memory exhaustion"
    assert train_kwargs["batch"] == 2
    assert train_kwargs["imgsz"] == 640
    assert train_kwargs["epochs"] == 1

    # Verify val was called on the test split with workers=0
    mock_model.val.assert_called_once()
    val_kwargs = mock_model.val.call_args.kwargs
    assert val_kwargs["split"] == "test"
    assert val_kwargs["workers"] == 0


def test_train_custom_workers_forwarding(tmp_path):
    """Verify non-zero workers are properly forwarded if specified by the user."""
    fake_yaml = tmp_path / "dataset.yaml"
    fake_yaml.write_text("path: fake\ntrain: train.txt\nval: val.txt\ntest: test.txt\n", encoding="utf-8")

    mock_model = MagicMock()
    mock_model.train.return_value = MagicMock()
    mock_val_results = MagicMock()
    mock_val_results.box = None
    mock_val_results.results_dict = {"metrics": 0.5}
    mock_model.val.return_value = mock_val_results

    with patch("ml.uavvaste.train.get_yolo_model", return_value=(mock_model, "yolo11n.pt")):
        train(
            data=fake_yaml,
            model_name="yolo11n.pt",
            imgsz=640,
            epochs=1,
            batch=4,
            workers=2,
            project=str(tmp_path / "runs"),
            name="custom_worker_run",
        )

    train_kwargs = mock_model.train.call_args.kwargs
    assert train_kwargs["workers"] == 2
    assert train_kwargs["cache"] is False
    assert train_kwargs["batch"] == 4

    val_kwargs = mock_model.val.call_args.kwargs
    assert val_kwargs["workers"] == 2

