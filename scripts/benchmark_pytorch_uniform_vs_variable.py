"""
Benchmark PyTorch CPU on uniform same-size frames (1280x720) vs raw variable-size frames
to check whether the high PyTorch mean latency is a variable-image-size artifact.
"""

from pathlib import Path
import time
from typing import List

import cv2
import numpy as np
import torch
from ultralytics import YOLO

TEST_TXT = Path("ml/data/uavvaste_yolo/test.txt")
THREADS = 4
WARMUP_RUNS = 10
IMGSZ = 640


def main():
    torch.set_num_threads(THREADS)
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass

    model = YOLO("models/garbage.pt")

    base_dir = TEST_TXT.parent
    lines = [line.strip() for line in TEST_TXT.read_text().splitlines() if line.strip() and not line.startswith("#")]

    print(f"Loading {len(lines)} images from {TEST_TXT}...")
    raw_images: List[np.ndarray] = []
    uniform_images: List[np.ndarray] = []

    for l in lines:
        p = (base_dir / l).resolve()
        img = cv2.imread(str(p))
        if img is not None:
            raw_images.append(img)
            uniform_images.append(cv2.resize(img, (1280, 720), interpolation=cv2.INTER_LINEAR))

    print(f"Loaded {len(raw_images)} images.")
    raw_shapes = set(im.shape for im in raw_images)
    print(f"Raw image distinct shapes: {raw_shapes}")
    print(f"Uniform image shape: {uniform_images[0].shape}")

    # --- 1. Benchmark on Uniform 1280x720 frames ---
    print(f"\n--- Benchmarking Uniform 1280x720 Frames ({WARMUP_RUNS} warmups discarded) ---")
    dummy_uniform = np.zeros((720, 1280, 3), dtype=np.uint8)
    for _ in range(WARMUP_RUNS):
        model.predict(dummy_uniform, imgsz=IMGSZ, device="cpu", verbose=False)

    inf_times_uniform = []
    e2e_times_uniform = []
    pre_times_uniform = []
    post_times_uniform = []

    for img in uniform_images:
        t0 = time.perf_counter()
        res = model.predict(img, imgsz=IMGSZ, device="cpu", verbose=False)
        t1 = time.perf_counter()
        sp = res[0].speed
        pre_times_uniform.append(float(sp.get("preprocess", 0.0)))
        inf_times_uniform.append(float(sp.get("inference", 0.0)))
        post_times_uniform.append(float(sp.get("postprocess", 0.0)))
        e2e_times_uniform.append((t1 - t0) * 1000.0)

    # --- 2. Benchmark on Raw Variable-Size Frames ---
    print(f"\n--- Benchmarking Raw Variable-Size Frames ({WARMUP_RUNS} warmups discarded) ---")
    dummy_raw = raw_images[0]
    for _ in range(WARMUP_RUNS):
        model.predict(dummy_raw, imgsz=IMGSZ, device="cpu", verbose=False)

    inf_times_raw = []
    e2e_times_raw = []
    pre_times_raw = []
    post_times_raw = []

    for img in raw_images:
        t0 = time.perf_counter()
        res = model.predict(img, imgsz=IMGSZ, device="cpu", verbose=False)
        t1 = time.perf_counter()
        sp = res[0].speed
        pre_times_raw.append(float(sp.get("preprocess", 0.0)))
        inf_times_raw.append(float(sp.get("inference", 0.0)))
        post_times_raw.append(float(sp.get("postprocess", 0.0)))
        e2e_times_raw.append((t1 - t0) * 1000.0)

    def stats(arr: List[float]):
        a = np.array(arr)
        return {
            "p50": round(float(np.percentile(a, 50)), 2),
            "mean": round(float(np.mean(a)), 2),
            "p90": round(float(np.percentile(a, 90)), 2),
            "p95": round(float(np.percentile(a, 95)), 2),
            "max": round(float(np.max(a)), 2),
        }

    u_inf = stats(inf_times_uniform)
    u_e2e = stats(e2e_times_uniform)
    u_pre = stats(pre_times_uniform)

    r_inf = stats(inf_times_raw)
    r_e2e = stats(e2e_times_raw)
    r_pre = stats(pre_times_raw)

    print("\n" + "=" * 90)
    print(" PYTORCH CPU BENCHMARK: UNIFORM (1280x720) vs RAW VARIABLE FRAMES (Threads=4, N=116)")
    print("=" * 90)
    print(f"{'Input Frame Condition':<28} | {'Stage':<12} | {'p50 (ms)':<9} | {'Mean (ms)':<9} | {'p95 (ms)':<9} | {'Max (ms)':<9}")
    print("-" * 90)
    print(f"{'Uniform 1280x720':<28} | {'Inference':<12} | {u_inf['p50']:<9.2f} | {u_inf['mean']:<9.2f} | {u_inf['p95']:<9.2f} | {u_inf['max']:<9.2f}")
    print(f"{'Uniform 1280x720':<28} | {'Preprocess':<12} | {u_pre['p50']:<9.2f} | {u_pre['mean']:<9.2f} | {u_pre['p95']:<9.2f} | {u_pre['max']:<9.2f}")
    print(f"{'Uniform 1280x720':<28} | {'End-to-End':<12} | {u_e2e['p50']:<9.2f} | {u_e2e['mean']:<9.2f} | {u_e2e['p95']:<9.2f} | {u_e2e['max']:<9.2f}")
    print("-" * 90)
    print(f"{'Raw Variable (4K/6M/12M)':<28} | {'Inference':<12} | {r_inf['p50']:<9.2f} | {r_inf['mean']:<9.2f} | {r_inf['p95']:<9.2f} | {r_inf['max']:<9.2f}")
    print(f"{'Raw Variable (4K/6M/12M)':<28} | {'Preprocess':<12} | {r_pre['p50']:<9.2f} | {r_pre['mean']:<9.2f} | {r_pre['p95']:<9.2f} | {r_pre['max']:<9.2f}")
    print(f"{'Raw Variable (4K/6M/12M)':<28} | {'End-to-End':<12} | {r_e2e['p50']:<9.2f} | {r_e2e['mean']:<9.2f} | {r_e2e['p95']:<9.2f} | {r_e2e['max']:<9.2f}")
    print("=" * 90)


if __name__ == "__main__":
    main()
