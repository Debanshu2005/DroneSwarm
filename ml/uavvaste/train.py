"""Train and evaluate YOLO model on UAVVaste dataset."""

import argparse
from pathlib import Path
import shutil
import sys
from typing import Optional


def get_yolo_model(model_name: str):
    """Load YOLO model with graceful fallback for unimplemented model versions."""
    from ultralytics import YOLO

    try:
        return YOLO(model_name), model_name
    except Exception as e:
        if "yolo26" in model_name.lower():
            fallback = "yolo11n.pt"
            print(
                f"[INFO] Notice: Ultralytics currently lacks '{model_name}' ({e}).\n"
                f"       Falling back to '{fallback}' with fine-tuning on pretrained weights."
            )
            return YOLO(fallback), fallback
        raise


def train(
    data: Path,
    model_name: str = "yolo26n.pt",
    imgsz: int = 1280,
    epochs: int = 100,
    batch: int = -1,
    patience: int = 30,
    device: Optional[str] = None,
    project: str = "ml/runs",
    name: str = "uavvaste",
    export_format: Optional[str] = None,
    install: bool = False,
):
    """Fine-tune YOLO model on UAVVaste dataset and evaluate on test split."""
    data_path = Path(data).resolve()
    if not data_path.exists():
        print(f"ERROR: Dataset YAML not found: {data_path}")
        sys.exit(1)

    print(f"Initializing YOLO model: {model_name}")
    model, resolved_model_name = get_yolo_model(model_name)

    train_kwargs = {
        "data": str(data_path),
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "patience": patience,
        "flipud": 0.5,
        "fliplr": 0.5,
        "degrees": 10.0,
        "project": project,
        "name": name,
    }
    if device is not None:
        train_kwargs["device"] = device

    print("Beginning fine-tuning with parameters:")
    for k, v in train_kwargs.items():
        print(f"  {k}: {v}")

    train_results = model.train(**train_kwargs)

    # Evaluate on the TEST split
    print("\n" + "=" * 60)
    print("Evaluating trained model on TEST split...")
    print("=" * 60)
    val_results = model.val(data=str(data_path), split="test")

    # Print metrics
    box_metrics = getattr(val_results, "box", None)
    if box_metrics is not None:
        p = getattr(box_metrics, "mp", 0.0)
        r = getattr(box_metrics, "mr", 0.0)
        map50 = getattr(box_metrics, "map50", 0.0)
        map50_95 = getattr(box_metrics, "map", 0.0)
        print("\nTest Split Metrics:")
        print(f"  Precision:   {p:.4f}")
        print(f"  Recall:      {r:.4f}")
        print(f"  mAP@50:      {map50:.4f}")
        print(f"  mAP@50-95:   {map50_95:.4f}")
    else:
        results_dict = getattr(val_results, "results_dict", {})
        print("\nTest Split Metrics (from results_dict):")
        for k, v in results_dict.items():
            print(f"  {k}: {v}")

    # Optional export
    if export_format:
        print(f"\nExporting model to {export_format.upper()}...")
        model.export(format=export_format)

    # Optional install to models/garbage.pt
    if install:
        best_pt: Optional[Path] = None
        trainer = getattr(model, "trainer", None)
        if trainer and getattr(trainer, "best", None):
            best_candidate = Path(trainer.best)
            if best_candidate.exists():
                best_pt = best_candidate

        if not best_pt:
            run_best = Path(project) / name / "weights" / "best.pt"
            if run_best.exists():
                best_pt = run_best

        if best_pt and best_pt.exists():
            target_pt = Path("models/garbage.pt")
            target_pt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(best_pt, target_pt)
            print(f"\nSuccessfully installed best model weights to: {target_pt}")
        else:
            print("\nWARNING: Could not locate best.pt to install into models/garbage.pt")

    return model, val_results


def main():
    parser = argparse.ArgumentParser(description="Train YOLO detector on UAVVaste dataset.")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("ml/data/uavvaste_yolo/dataset.yaml"),
        help="Path to dataset.yaml",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="yolo26n.pt",
        help="Base pretrained model name (default: yolo26n.pt; falls back to yolo11n.pt if unavailable)",
    )
    parser.add_argument("--imgsz", type=int, default=1280, help="Training image size (default: 1280)")
    parser.add_argument("--epochs", type=int, default=100, help="Number of training epochs (default: 100)")
    parser.add_argument("--batch", type=int, default=-1, help="Batch size (-1 for auto)")
    parser.add_argument("--patience", type=int, default=30, help="Early stopping patience")
    parser.add_argument("--device", type=str, default=None, help="Device (cpu, cuda:0, etc.)")
    parser.add_argument("--project", type=str, default="ml/runs", help="Project save directory")
    parser.add_argument("--name", type=str, default="uavvaste", help="Experiment name")
    parser.add_argument(
        "--export",
        type=str,
        default=None,
        choices=["onnx", "ncnn", "torchscript", "tflite"],
        help="Export format after training",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install best.pt weights to models/garbage.pt upon completion",
    )

    args = parser.parse_args()
    train(
        data=args.data,
        model_name=args.model,
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch=args.batch,
        patience=args.patience,
        device=args.device,
        project=args.project,
        name=args.name,
        export_format=args.export,
        install=args.install,
    )


if __name__ == "__main__":
    main()
