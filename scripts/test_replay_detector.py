#!/usr/bin/env python3
"""
Offline replay smoke test for DroneSwarm GarbageDetector.

Tests model loading, frame processing, detection formatting, and inference latency
on a folder of real UAVVaste images without requiring AirSim or a live drone.

Usage:
    python scripts/test_replay_detector.py
    python scripts/test_replay_detector.py --images-dir ml/data/uavvaste_yolo/images --num-images 5 --save-annotated
"""

import argparse
import asyncio
from pathlib import Path
import time
from typing import List

import cv2
import numpy as np

# Ensure repository root is on sys.path
import sys
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.detector import GarbageDetector
from DroneOS.perception.frame_sources import ImageFolderSource


async def run_replay_smoke_test(
    images_dir: str,
    weights_path: str = "models/garbage.pt",
    num_images: int = 5,
    conf: float = 0.35,
    iou: float = 0.5,
    imgsz: int = 640,
    device: str = "cuda:0",
    save_annotated: bool = True,
    out_dir: str = "ml/reports/smoke_test_output",
):
    print("=" * 72)
    print("DroneSwarm GarbageDetector — Offline Replay Smoke Test")
    print("=" * 72)

    weights = Path(weights_path).resolve()
    img_folder = Path(images_dir).resolve()
    out_path = Path(out_dir).resolve()

    if not weights.exists():
        print(f"[ERROR] Model weights not found at: {weights}")
        return False
    if not img_folder.exists():
        print(f"[ERROR] Image folder not found at: {img_folder}")
        return False

    if save_annotated:
        out_path.mkdir(parents=True, exist_ok=True)

    print(f"Weights: {weights}")
    print(f"Image Source: {img_folder}")
    print(f"Resolution: {imgsz} | Conf: {conf} | IoU: {iou} | Device: {device}")
    print(f"Testing on up to {num_images} images...\n")

    # 1. Initialize Detector
    detector = GarbageDetector(
        weights_path=str(weights),
        imgsz=imgsz,
        confidence=conf,
        iou=iou,
        device=device,
    )

    loaded = await detector.ensure_loaded()
    if not loaded:
        print("[ERROR] Failed to load detector weights.")
        return False
    print("[OK] Detector initialized and model loaded successfully.\n")

    # 2. Initialize Frame Source
    source = ImageFolderSource(str(img_folder))
    if not source.image_files:
        print("[ERROR] No valid images found in folder.")
        return False

    total_detections = 0
    latencies: List[float] = []

    for i in range(min(num_images, len(source.image_files))):
        img_file = source.image_files[i]
        frame = await source.get_frame()
        if frame is None or frame.image is None:
            print(f"  [{i+1}/{num_images}] Failed to acquire frame for {img_file.name}")
            continue

        t0 = time.perf_counter()
        detections = await detector.detect(frame.image)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(latency_ms)
        total_detections += len(detections)

        print(
            f"  Image {i+1:02d}: {img_file.name:28s} | "
            f"Detections: {len(detections):2d} | "
            f"Latency: {latency_ms:6.1f} ms"
        )

        for d_idx, det in enumerate(detections):
            x1, y1, x2, y2 = det.bbox_xyxy
            print(
                f"    -> Det #{d_idx+1}: [{det.label}] conf={det.confidence:.3f} "
                f"bbox=({x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f})"
            )

        if save_annotated and len(detections) > 0:
            annotated = frame.image.copy()
            for det in detections:
                x1, y1, x2, y2 = [int(v) for v in det.bbox_xyxy]
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
                tag = f"{det.label} {det.confidence:.2f}"
                cv2.putText(
                    annotated,
                    tag,
                    (x1, max(20, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 255),
                    2,
                )
            out_file = out_path / f"annotated_{img_file.name}"
            cv2.imwrite(str(out_file), annotated)

    detector.close()
    await source.close()

    print("\n" + "=" * 72)
    print("Smoke Test Summary:")
    print(f"  Total Images Processed: {len(latencies)}")
    print(f"  Total Garbage Detections: {total_detections}")
    avg_latency = np.mean(latencies) if latencies else 0.0
    print(f"  Average Inference Latency: {avg_latency:.1f} ms ({1000.0/max(avg_latency, 1e-3):.1f} FPS)")
    if save_annotated:
        print(f"  Annotated visual outputs saved to: {out_path}")
    print("=" * 72)
    print("[PASS] Smoke test completed successfully.")
    return True


def main():
    parser = argparse.ArgumentParser(description="DroneSwarm GarbageDetector Replay Smoke Test.")
    parser.add_argument(
        "--images-dir",
        default="ml/data/uavvaste_yolo/images",
        help="Path to folder containing UAV images",
    )
    parser.add_argument(
        "--weights",
        default="models/garbage.pt",
        help="Path to trained YOLO model weights",
    )
    parser.add_argument(
        "--num-images",
        type=int,
        default=5,
        help="Number of images to evaluate",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.35,
        help="Confidence threshold",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=0.5,
        help="NMS IoU threshold",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Inference image resolution",
    )
    parser.add_argument(
        "--device",
        default="cuda:0",
        help="Execution device (e.g. cuda:0 or cpu)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save annotated output images",
    )
    parser.add_argument(
        "--out-dir",
        default="ml/reports/smoke_test_output",
        help="Output directory for annotated images",
    )

    args = parser.parse_args()
    asyncio.run(
        run_replay_smoke_test(
            images_dir=args.images_dir,
            weights_path=args.weights,
            num_images=args.num_images,
            conf=args.conf,
            iou=args.iou,
            imgsz=args.imgsz,
            device=args.device,
            save_annotated=not args.no_save,
            out_dir=args.out_dir,
        )
    )


if __name__ == "__main__":
    main()

