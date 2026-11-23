"""
Standalone Edge Hardware / Raspberry Pi 5 Benchmark Script for DroneOS Perception.

Measures inference latency percentiles (p50, p95), effective FPS, memory,
CPU temperature (/sys/class/thermal/thermal_zone0/temp), and throttle state
(`vcgencmd get_throttled`).

Designed to run on Raspberry Pi 5 (4x Cortex-A76 @ 2.4GHz) and cross-platform.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


def get_cpu_temp() -> Optional[float]:
    """Read CPU temperature in degrees Celsius from Linux sysfs thermal zone."""
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal_path.exists():
        try:
            raw = thermal_path.read_text().strip()
            return float(raw) / 1000.0
        except Exception:
            pass

    # Fallback to vcgencmd measure_temp if available
    vcgencmd = shutil.which("vcgencmd")
    if vcgencmd:
        try:
            out = subprocess.check_output([vcgencmd, "measure_temp"], text=True).strip()
            # Format: temp=48.2'C
            if "temp=" in out:
                temp_str = out.split("=")[1].replace("'C", "")
                return float(temp_str)
        except Exception:
            pass

    return None


def get_vcgencmd_throttled() -> Dict[str, Any]:
    """
    Query Raspberry Pi throttle state via `vcgencmd get_throttled`.
    Decodes standard Pi bitmask flags (undervoltage, frequency capped, thermal throttling).
    """
    vcgencmd = shutil.which("vcgencmd")
    if not vcgencmd:
        return {"available": False, "raw": "N/A (vcgencmd not found)", "flags": {}}

    try:
        out = subprocess.check_output([vcgencmd, "get_throttled"], text=True).strip()
        # Format: throttled=0x0
        raw_hex = out.split("=")[-1]
        val = int(raw_hex, 16)
        flags = {
            "under_voltage_detected": bool(val & 0x1),
            "arm_frequency_capped": bool(val & 0x2),
            "currently_throttled": bool(val & 0x4),
            "soft_temperature_limit_active": bool(val & 0x8),
            "under_voltage_has_occurred": bool(val & 0x10000),
            "arm_frequency_capped_has_occurred": bool(val & 0x20000),
            "throttling_has_occurred": bool(val & 0x40000),
            "soft_temperature_limit_has_occurred": bool(val & 0x80000),
        }
        return {"available": True, "raw": out, "value_hex": raw_hex, "flags": flags}
    except Exception as e:
        return {"available": False, "raw": f"Error: {e}", "flags": {}}


def load_images_from_split(test_txt_path: Path, max_samples: Optional[int] = None) -> List[Path]:
    """Parse YOLO test.txt and resolve image file paths."""
    base_dir = test_txt_path.parent
    if not test_txt_path.exists():
        raise FileNotFoundError(f"Test split file not found: {test_txt_path}")

    image_paths: List[Path] = []
    with open(test_txt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = Path(line)
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            if p.exists():
                image_paths.append(p)
            if max_samples and len(image_paths) >= max_samples:
                break

    if not image_paths:
        raise ValueError(f"No valid images resolved from {test_txt_path}")
    return image_paths


def benchmark_onnx_runtime(
    model_path: Path,
    image_paths: List[Path],
    imgsz: int,
    threads: int,
    iterations: int,
    warmup: int,
) -> Dict[str, Any]:
    """Run pure ONNX Runtime CPU inference benchmark."""
    import onnxruntime as ort

    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = threads
    sess_options.inter_op_num_threads = 1
    sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    session = ort.InferenceSession(
        str(model_path),
        sess_options=sess_options,
        providers=["CPUExecutionProvider"],
    )
    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape

    # Determine input dimensions
    target_h = input_shape[2] if len(input_shape) >= 4 and isinstance(input_shape[2], int) else imgsz
    target_w = input_shape[3] if len(input_shape) >= 4 and isinstance(input_shape[3], int) else imgsz

    # Warmup
    print(f"Running {warmup} warmup iterations (input: {target_w}x{target_h}, threads: {threads})...")
    dummy_input = np.zeros((1, 3, target_h, target_w), dtype=np.float32)
    for _ in range(warmup):
        session.run(None, {input_name: dummy_input})

    # Benchmark loop
    pre_times: List[float] = []
    inf_times: List[float] = []
    e2e_times: List[float] = []

    print(f"Benchmarking {iterations} iterations on real images...")
    n_images = len(image_paths)

    for i in range(iterations):
        img_p = image_paths[i % n_images]
        t_start = time.perf_counter()

        # Preprocessing: load, resize, pad, normalize, transpose
        img_bgr = cv2.imread(str(img_p))
        if img_bgr is None:
            continue
        h, w = img_bgr.shape[:2]
        r = min(target_w / float(w), target_h / float(h))
        nw, nh = int(round(w * r)), int(round(h * r))
        resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        canvas[:nh, :nw] = resized
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]
        t_pre = time.perf_counter()

        # Inference
        session.run(None, {input_name: blob})
        t_inf = time.perf_counter()

        pre_ms = (t_pre - t_start) * 1000.0
        inf_ms = (t_inf - t_pre) * 1000.0
        e2e_ms = (t_inf - t_start) * 1000.0

        pre_times.append(pre_ms)
        inf_times.append(inf_ms)
        e2e_times.append(e2e_ms)

    return {
        "preprocess_ms": pre_times,
        "inference_ms": inf_times,
        "e2e_ms": e2e_times,
    }


def benchmark_pytorch(
    model_path: Path,
    image_paths: List[Path],
    imgsz: int,
    threads: int,
    iterations: int,
    warmup: int,
) -> Dict[str, Any]:
    """Run PyTorch / Ultralytics CPU inference benchmark with explicit thread pinning."""
    import torch
    from ultralytics import YOLO

    # Enforce fair thread count matching ONNX Runtime
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass

    model = YOLO(str(model_path))

    # Warmup
    print(f"Running {warmup} warmup iterations (imgsz: {imgsz}, threads: {threads})...")
    dummy_input = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
    for _ in range(warmup):
        model.predict(dummy_input, device="cpu", verbose=False)

    # Benchmark loop
    pre_times: List[float] = []
    inf_times: List[float] = []
    post_times: List[float] = []
    e2e_times: List[float] = []

    print(f"Benchmarking {iterations} iterations on real images...")
    n_images = len(image_paths)

    for i in range(iterations):
        img_p = image_paths[i % n_images]
        img_bgr = cv2.imread(str(img_p))
        if img_bgr is None:
            continue

        t_start = time.perf_counter()
        results = model.predict(img_bgr, imgsz=imgsz, device="cpu", verbose=False)
        t_end = time.perf_counter()
        e2e_ms = (t_end - t_start) * 1000.0

        if results and hasattr(results[0], "speed"):
            speed = results[0].speed
            pre_times.append(float(speed.get("preprocess", 0.0)))
            inf_times.append(float(speed.get("inference", 0.0)))
            post_times.append(float(speed.get("postprocess", 0.0)))
        else:
            inf_times.append(e2e_ms)

        e2e_times.append(e2e_ms)

    return {
        "preprocess_ms": pre_times,
        "inference_ms": inf_times,
        "postprocess_ms": post_times,
        "e2e_ms": e2e_times,
    }


def compute_percentiles(values: List[float]) -> Dict[str, float]:
    """Compute statistical distribution metrics."""
    if not values:
        return {"min": 0.0, "mean": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
    arr = np.array(values)
    return {
        "min": round(float(np.min(arr)), 2),
        "mean": round(float(np.mean(arr)), 2),
        "p50": round(float(np.percentile(arr, 50)), 2),
        "p90": round(float(np.percentile(arr, 90)), 2),
        "p95": round(float(np.percentile(arr, 95)), 2),
        "max": round(float(np.max(arr)), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="DroneOS Inference Benchmark (Raspberry Pi 5 / Edge)")
    parser.add_argument("--model", type=str, default="models/garbage.onnx", help="Path to .onnx or .pt weights")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image dimension")
    parser.add_argument("--backend", type=str, default="auto", choices=["auto", "onnx", "pt", "quantized"], help="Inference runtime backend")
    parser.add_argument("--split", type=str, default="ml/data/uavvaste_yolo/test.txt", help="Path to test.txt split")
    parser.add_argument("--iterations", type=int, default=116, help="Number of benchmark iterations")
    parser.add_argument("--warmup", type=int, default=10, help="Number of warmup iterations")
    parser.add_argument("--threads", type=int, default=4, help="CPU intra-op threads (Pi 5 has 4 cores)")
    parser.add_argument("--output", type=str, default="ml/reports/pi5_benchmark_results.json", help="Path for JSON results")
    parser.add_argument("--compare-640", action="store_true", help="Run fair PyTorch vs ONNX vs INT8 comparison at imgsz=640 with equal threads")
    args = parser.parse_args()

    split_path = Path(args.split)
    image_paths = load_images_from_split(split_path, max_samples=args.iterations)

    if args.compare_640:
        pt_path = Path("models/garbage.pt")
        onnx_path = Path("models/garbage.onnx")
        int8_path = Path("models/garbage_int8.onnx")

        print("=" * 80)
        print(" FAIR COMPARISON BENCHMARK @ imgsz=640")
        print(f" Threads    : {args.threads}")
        print(f" Warmup     : {args.warmup} runs (discarded)")
        print(f" Iterations : {args.iterations} runs on {split_path}")
        print("=" * 80)

        configs = [
            ("PyTorch CPU", pt_path, "pt"),
            ("ONNX Runtime CPU", onnx_path, "onnx"),
            ("INT8 ONNX Runtime CPU", int8_path, "quantized"),
        ]
        comp_results = []
        p50_pt = 0.0

        for name, mpath, b_type in configs:
            print(f"\n>>> Benchmarking {name} ({mpath})...")
            if b_type in ("onnx", "quantized"):
                raw = benchmark_onnx_runtime(
                    model_path=mpath,
                    image_paths=image_paths,
                    imgsz=640,
                    threads=args.threads,
                    iterations=args.iterations,
                    warmup=args.warmup,
                )
            else:
                raw = benchmark_pytorch(
                    model_path=mpath,
                    image_paths=image_paths,
                    imgsz=640,
                    threads=args.threads,
                    iterations=args.iterations,
                    warmup=args.warmup,
                )
            inf_pct = compute_percentiles(raw.get("inference_ms", []))
            e2e_pct = compute_percentiles(raw.get("e2e_ms", []))
            inf_fps = round(1000.0 / inf_pct["mean"], 2) if inf_pct["mean"] > 0 else 0.0
            if b_type == "pt":
                p50_pt = inf_pct["p50"]
            speedup = round(p50_pt / inf_pct["p50"], 2) if inf_pct["p50"] > 0 and p50_pt > 0 else 1.0

            comp_results.append({
                "backend": name,
                "model": str(mpath),
                "threads": args.threads,
                "warmup": args.warmup,
                "iterations": args.iterations,
                "inf_p50_ms": inf_pct["p50"],
                "inf_mean_ms": inf_pct["mean"],
                "inf_p95_ms": inf_pct["p95"],
                "inf_fps": inf_fps,
                "e2e_p50_ms": e2e_pct["p50"],
                "speedup_vs_pytorch": speedup,
            })

        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(comp_results, f, indent=2)

        print("\n" + "=" * 90)
        print(f"{'Backend':<24} | {'Threads':<7} | {'p50 (ms)':<8} | {'Mean (ms)':<9} | {'p95 (ms)':<8} | {'FPS':<6} | {'Speedup'}")
        print("=" * 90)
        for cr in comp_results:
            print(
                f"{cr['backend']:<24} | {cr['threads']:<7} | {cr['inf_p50_ms']:<8.2f} | {cr['inf_mean_ms']:<9.2f} | "
                f"{cr['inf_p95_ms']:<8.2f} | {cr['inf_fps']:<6.1f} | {cr['speedup_vs_pytorch']:.2f}x"
            )
        print("=" * 90)
        return

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: Model weights not found at: {model_path}", file=sys.stderr)
        sys.exit(1)

    backend = args.backend
    if backend == "auto":
        backend = "onnx" if model_path.suffix.lower() == ".onnx" else "pt"

    temp_before = get_cpu_temp()
    throttle_before = get_vcgencmd_throttled()

    print("=" * 75)
    print(" DRONEOS INFERENCE BENCHMARK")
    print(f" Platform : {platform.system()} {platform.machine()} ({platform.processor() or 'CPU'})")
    print(f" Model    : {model_path} ({backend})")
    print(f" Imgsz    : {args.imgsz}")
    print(f" Threads  : {args.threads}")
    print(f" Images   : {len(image_paths)} samples from {split_path}")
    print(f" Temp In  : {temp_before:.1f}'C" if temp_before is not None else " Temp In  : N/A")
    print(f" Throttle : {throttle_before.get('raw', 'N/A')}")
    print("=" * 75)

    t_bench_start = time.perf_counter()
    if backend in ("onnx", "quantized"):
        raw_metrics = benchmark_onnx_runtime(
            model_path=model_path,
            image_paths=image_paths,
            imgsz=args.imgsz,
            threads=args.threads,
            iterations=args.iterations,
            warmup=args.warmup,
        )
    else:
        raw_metrics = benchmark_pytorch(
            model_path=model_path,
            image_paths=image_paths,
            imgsz=args.imgsz,
            threads=args.threads,
            iterations=args.iterations,
            warmup=args.warmup,
        )
    total_bench_duration_s = time.perf_counter() - t_bench_start

    temp_after = get_cpu_temp()
    throttle_after = get_vcgencmd_throttled()

    inf_stats = compute_percentiles(raw_metrics.get("inference_ms", []))
    e2e_stats = compute_percentiles(raw_metrics.get("e2e_ms", []))
    pre_stats = compute_percentiles(raw_metrics.get("preprocess_ms", []))

    inf_fps = round(1000.0 / inf_stats["mean"], 2) if inf_stats["mean"] > 0 else 0.0
    e2e_fps = round(1000.0 / e2e_stats["mean"], 2) if e2e_stats["mean"] > 0 else 0.0

    report = {
        "metadata": {
            "timestamp": time.time(),
            "platform": platform.platform(),
            "cpu_architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "model_path": str(model_path),
            "backend": backend,
            "imgsz": args.imgsz,
            "threads": args.threads,
            "iterations": args.iterations,
            "warmup": args.warmup,
            "total_benchmark_duration_s": round(total_bench_duration_s, 2),
        },
        "thermals_and_throttling": {
            "temp_before_c": temp_before,
            "temp_after_c": temp_after,
            "temp_rise_c": round(temp_after - temp_before, 2) if (temp_before is not None and temp_after is not None) else None,
            "throttle_before": throttle_before,
            "throttle_after": throttle_after,
        },
        "latency_percentiles_ms": {
            "preprocess": pre_stats,
            "inference": inf_stats,
            "e2e": e2e_stats,
        },
        "throughput": {
            "inference_fps": inf_fps,
            "e2e_fps": e2e_fps,
            "sustainable_max_fps_rec": min(4.0, max(1.0, float(int(inf_fps * 0.8)))),
        },
    }

    out_p = Path(args.output)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 75)
    print(" BENCHMARK RESULTS")
    print("=" * 75)
    print(f" Inference Latency : p50 = {inf_stats['p50']:<6.2f} ms | p95 = {inf_stats['p95']:<6.2f} ms | mean = {inf_stats['mean']:<6.2f} ms")
    print(f" End-to-End Latency: p50 = {e2e_stats['p50']:<6.2f} ms | p95 = {e2e_stats['p95']:<6.2f} ms | mean = {e2e_stats['mean']:<6.2f} ms")
    print(f" Inference FPS     : {inf_fps:<6.2f} fps")
    print(f" End-to-End FPS    : {e2e_fps:<6.2f} fps")
    if temp_after is not None:
        print(f" CPU Temperature   : {temp_after:.1f}'C (Rise: {report['thermals_and_throttling']['temp_rise_c']:.1f}'C)")
    print(f" Throttle State    : {throttle_after.get('raw', 'N/A')}")
    print(f" Detailed Report   : {out_p}")
    print("=" * 75)


if __name__ == "__main__":
    main()
