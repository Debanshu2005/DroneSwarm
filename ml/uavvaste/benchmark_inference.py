#!/usr/bin/env python3
"""
GPU and CPU inference performance benchmark for YOLO26n GarbageDetector.
Measures model load time, first inference latency, warm-up latency, steady-state latency
(mean, median, P95), throughput (FPS), and peak memory usage across 100 images.
"""

import gc
import json
from pathlib import Path
import time
from typing import Dict, List

import cv2
import numpy as np
import psutil
import torch
from ultralytics import YOLO


def run_benchmark_on_device(
    device: str,
    imgsz: int = 640,
    num_images: int = 100,
    weights_path: str = "models/garbage.pt",
) -> Dict:
    print(f"\n{'='*60}")
    print(f"BENCHMARKING DEVICE: {device} | Resolution: {imgsz}x{imgsz}")
    print(f"{'='*60}")

    process = psutil.Process()
    gc.collect()
    if "cuda" in device and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    initial_ram_mb = process.memory_info().rss / (1024 * 1024)

    # 1. Model Loading Time
    t_load_start = time.perf_counter()
    model = YOLO(weights_path)
    # Trigger model architecture build
    _ = model.model
    t_load_end = time.perf_counter()
    load_time_ms = (t_load_end - t_load_start) * 1000.0
    print(f"Model Load Time: {load_time_ms:.1f} ms")

    # Load 100 images from val.txt
    val_txt = Path("ml/data/uavvaste_yolo/val.txt")
    image_paths = [Path(l.strip()) for l in val_txt.read_text().splitlines() if l.strip()]
    test_images = []
    for rel_p in image_paths[:num_images]:
        p = Path("ml/data/uavvaste_yolo") / rel_p
        if not p.exists():
            p = Path(rel_p)
        img = cv2.imread(str(p))
        if img is not None:
            test_images.append(img)

    actual_n = len(test_images)
    print(f"Loaded {actual_n} real test images into memory.")

    # 2. First Inference Latency (Cold Start)
    if "cuda" in device:
        torch.cuda.synchronize()
    t_first_start = time.perf_counter()
    _ = model.predict(source=test_images[0], imgsz=imgsz, conf=0.35, iou=0.5, device=device, verbose=False)
    if "cuda" in device:
        torch.cuda.synchronize()
    first_latency_ms = (time.perf_counter() - t_first_start) * 1000.0
    print(f"First Inference Latency (Cold Start): {first_latency_ms:.1f} ms")

    # 3. Warm-up Phase (Images 1 to 5)
    warmup_latencies = []
    for i in range(1, min(6, actual_n)):
        if "cuda" in device:
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        _ = model.predict(source=test_images[i], imgsz=imgsz, conf=0.35, iou=0.5, device=device, verbose=False)
        if "cuda" in device:
            torch.cuda.synchronize()
        warmup_latencies.append((time.perf_counter() - t0) * 1000.0)
    avg_warmup_ms = float(np.mean(warmup_latencies)) if warmup_latencies else 0.0
    print(f"Average Warm-up Latency: {avg_warmup_ms:.1f} ms")

    # 4. Steady-State Inference Phase (Images 6 to N)
    steady_latencies = []
    with torch.no_grad():
        for i in range(6, actual_n):
            img = test_images[i]
            if "cuda" in device:
                torch.cuda.synchronize()
            t0 = time.perf_counter()
            _ = model.predict(source=img, imgsz=imgsz, conf=0.35, iou=0.5, device=device, verbose=False)
            if "cuda" in device:
                torch.cuda.synchronize()
            t1 = time.perf_counter()
            steady_latencies.append((t1 - t0) * 1000.0)

    # 5. Metrics Calculation
    avg_lat = float(np.mean(steady_latencies))
    median_lat = float(np.median(steady_latencies))
    p95_lat = float(np.percentile(steady_latencies, 95))
    fps = 1000.0 / avg_lat if avg_lat > 0 else 0.0

    peak_gpu_alloc_mb = 0.0
    peak_gpu_reserved_mb = 0.0
    if "cuda" in device and torch.cuda.is_available():
        peak_gpu_alloc_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
        peak_gpu_reserved_mb = torch.cuda.max_memory_reserved() / (1024 * 1024)

    final_ram_mb = process.memory_info().rss / (1024 * 1024)
    ram_usage_mb = final_ram_mb - initial_ram_mb

    print(f"Steady-State Average Latency: {avg_lat:.2f} ms")
    print(f"Median Latency:               {median_lat:.2f} ms")
    print(f"P95 Latency:                  {p95_lat:.2f} ms")
    print(f"Throughput:                   {fps:.1f} FPS")
    if "cuda" in device:
        print(f"Peak GPU VRAM Allocated:      {peak_gpu_alloc_mb:.1f} MB")
        print(f"Peak GPU VRAM Reserved:       {peak_gpu_reserved_mb:.1f} MB")
    print(f"Process RAM Delta:            {ram_usage_mb:.1f} MB (Total: {final_ram_mb:.1f} MB)")

    return {
        "device": device,
        "resolution": imgsz,
        "sample_size": len(steady_latencies),
        "model_load_ms": round(load_time_ms, 2),
        "first_inference_ms": round(first_latency_ms, 2),
        "warmup_avg_ms": round(avg_warmup_ms, 2),
        "avg_latency_ms": round(avg_lat, 2),
        "median_latency_ms": round(median_lat, 2),
        "p95_latency_ms": round(p95_lat, 2),
        "fps": round(fps, 2),
        "peak_gpu_vram_alloc_mb": round(peak_gpu_alloc_mb, 2),
        "peak_gpu_vram_reserved_mb": round(peak_gpu_reserved_mb, 2),
        "process_ram_mb": round(final_ram_mb, 2),
    }


def main():
    out_dir = Path("ml/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    # Benchmark GPU at 640 (conservative edge deployment)
    if torch.cuda.is_available():
        res_gpu_640 = run_benchmark_on_device("cuda:0", imgsz=640, num_images=100)
        results.append(res_gpu_640)

        # Benchmark GPU at 1280 (high-resolution training scale)
        res_gpu_1280 = run_benchmark_on_device("cuda:0", imgsz=1280, num_images=100)
        results.append(res_gpu_1280)

    # Benchmark CPU at 640 (CPU fallback & companion computer proxy)
    res_cpu_640 = run_benchmark_on_device("cpu", imgsz=640, num_images=50)
    results.append(res_cpu_640)

    out_json = out_dir / "benchmark_results.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n[OK] Benchmark results saved to {out_json}")


if __name__ == "__main__":
    main()

