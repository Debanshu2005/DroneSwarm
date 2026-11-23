"""
Benchmark PyTorch CPU vs ONNX Runtime CPU across imgsz on real UAVVaste test split.
Enforces benchmark fairness:
- Equal thread count (4 threads)
- Discarded warmup (~10 iterations)
- Latency percentiles (p50, mean, p95) measured over >=100 real images
- Accuracy evaluated with explicit confidence thresholds (conf: 0.55 and conf: 0.35)
"""

import json
from pathlib import Path
import time
from typing import Any, Dict, List

import cv2
import numpy as np
from onnxruntime.quantization import QuantType, quantize_dynamic
import torch
from ultralytics import YOLO

DATA_YAML = "ml/data/uavvaste_yolo/dataset.yaml"
TEST_TXT = "ml/data/uavvaste_yolo/test.txt"
OUTPUT_REPORT = Path("ml/reports/real_split_benchmark.json")
THREADS = 4
WARMUP_RUNS = 10


def load_test_images(split_path: Path) -> List[Path]:
    base_dir = split_path.parent
    images: List[Path] = []
    with open(split_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                p = Path(line)
                if not p.is_absolute():
                    p = (base_dir / p).resolve()
                if p.exists():
                    images.append(p)
    return images


def measure_pytorch_latencies(
    model_path: Path, image_paths: List[Path], imgsz: int = 640
) -> Dict[str, float]:
    torch.set_num_threads(THREADS)
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    model = YOLO(str(model_path))

    # Warmup
    dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
    for _ in range(WARMUP_RUNS):
        model.predict(dummy, imgsz=imgsz, device="cpu", verbose=False)

    times_ms: List[float] = []
    for img_p in image_paths:
        img_bgr = cv2.imread(str(img_p))
        if img_bgr is None:
            continue
        res = model.predict(img_bgr, imgsz=imgsz, device="cpu", verbose=False)
        if res and hasattr(res[0], "speed"):
            times_ms.append(float(res[0].speed.get("inference", 0.0)))

    arr = np.array(times_ms)
    return {
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "mean_ms": round(float(np.mean(arr)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "fps": round(1000.0 / float(np.mean(arr)), 2) if np.mean(arr) > 0 else 0.0,
    }


def measure_onnx_latencies(
    model_path: Path, image_paths: List[Path], imgsz: int = 640
) -> Dict[str, float]:
    import onnxruntime as ort

    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = THREADS
    sess_options.inter_op_num_threads = 1
    sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    session = ort.InferenceSession(
        str(model_path), sess_options=sess_options, providers=["CPUExecutionProvider"]
    )
    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape
    target_h = input_shape[2] if len(input_shape) >= 4 and isinstance(input_shape[2], int) else imgsz
    target_w = input_shape[3] if len(input_shape) >= 4 and isinstance(input_shape[3], int) else imgsz

    # Warmup
    dummy = np.zeros((1, 3, target_h, target_w), dtype=np.float32)
    for _ in range(WARMUP_RUNS):
        session.run(None, {input_name: dummy})

    times_ms: List[float] = []
    for img_p in image_paths:
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

        t0 = time.perf_counter()
        session.run(None, {input_name: blob})
        t1 = time.perf_counter()
        times_ms.append((t1 - t0) * 1000.0)

    arr = np.array(times_ms)
    return {
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "mean_ms": round(float(np.mean(arr)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "fps": round(1000.0 / float(np.mean(arr)), 2) if np.mean(arr) > 0 else 0.0,
    }


def evaluate_accuracy(model_path: Path, conf: float, is_onnx: bool = False) -> Dict[str, Any]:
    torch.set_num_threads(THREADS)
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    if is_onnx:
        model = YOLO(str(model_path), task="detect")
    else:
        model = YOLO(str(model_path))

    val_res = model.val(
        data=DATA_YAML,
        split="test",
        imgsz=640,
        device="cpu",
        conf=conf,
        verbose=False,
        plots=False,
    )
    p = float(val_res.box.p[0]) if len(val_res.box.p) > 0 else 0.0
    r = float(val_res.box.r[0]) if len(val_res.box.r) > 0 else 0.0
    f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0
    m_ap50 = float(val_res.box.map50)

    return {
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f1, 4),
        "map50": round(m_ap50, 4),
    }


def main():
    pt_path = Path("models/garbage.pt")
    onnx_640_path = Path("models/garbage.onnx")
    int8_640_path = Path("models/garbage_int8.onnx")

    if not int8_640_path.exists():
        print("Ensuring INT8 quantized ONNX model from models/garbage.onnx (imgsz 640)...")
        quantize_dynamic(str(onnx_640_path), str(int8_640_path), weight_type=QuantType.QUInt8)

    test_images = load_test_images(Path(TEST_TXT))
    print(f"Loaded {len(test_images)} test images from {TEST_TXT}")

    # 1. Fair Latency Measurement (threads=4, warmup=10, >=100 runs)
    print("\n" + "=" * 80)
    print(f" FAIR LATENCY BENCHMARK @ imgsz=640 (Threads={THREADS}, Warmup={WARMUP_RUNS}, N={len(test_images)})")
    print("=" * 80)

    print("Measuring PyTorch CPU latency...")
    lat_pt = measure_pytorch_latencies(pt_path, test_images, imgsz=640)

    print("Measuring ONNX Runtime CPU latency...")
    lat_onnx = measure_onnx_latencies(onnx_640_path, test_images, imgsz=640)

    print("Measuring INT8 ONNX Runtime CPU latency...")
    lat_int8 = measure_onnx_latencies(int8_640_path, test_images, imgsz=640)

    p50_base = lat_pt["p50_ms"]
    speedup_onnx = round(p50_base / lat_onnx["p50_ms"], 2) if lat_onnx["p50_ms"] > 0 else 1.0
    speedup_int8 = round(p50_base / lat_int8["p50_ms"], 2) if lat_int8["p50_ms"] > 0 else 1.0

    latency_table = [
        {"backend": "PyTorch CPU", "model": pt_path.name, **lat_pt, "speedup": 1.0},
        {"backend": "ONNX Runtime CPU", "model": onnx_640_path.name, **lat_onnx, "speedup": speedup_onnx},
        {"backend": "INT8 ONNX Runtime CPU", "model": int8_640_path.name, **lat_int8, "speedup": speedup_int8},
    ]

    # 2. Accuracy Sweeps for conf=0.55 and conf=0.35
    models_meta = [
        ("PyTorch CPU", pt_path, False),
        ("ONNX Runtime CPU", onnx_640_path, True),
        ("INT8 ONNX Runtime CPU", int8_640_path, True),
    ]

    accuracy_results_55: List[Dict[str, Any]] = []
    accuracy_results_35: List[Dict[str, Any]] = []

    print("\n" + "=" * 80)
    print(" EVALUATING REAL-SPLIT ACCURACY @ conf=0.55")
    print("=" * 80)
    for name, mpath, is_onnx in models_meta:
        print(f"Running accuracy eval for {name} ({mpath.name}) @ conf=0.55...")
        acc = evaluate_accuracy(mpath, conf=0.55, is_onnx=is_onnx)
        accuracy_results_55.append({"backend": name, "model": mpath.name, "conf": 0.55, **acc})

    base_r_55 = accuracy_results_55[0]["recall"]
    base_p_55 = accuracy_results_55[0]["precision"]
    for row in accuracy_results_55:
        row["recall_drop_pct"] = round((base_r_55 - row["recall"]) * 100.0, 2)
        row["precision_drop_pct"] = round((base_p_55 - row["precision"]) * 100.0, 2)
        row["meets_gate"] = (row["recall_drop_pct"] <= 3.0 and row["precision_drop_pct"] <= 1.0)

    print("\n" + "=" * 80)
    print(" EVALUATING REAL-SPLIT ACCURACY @ conf=0.35")
    print("=" * 80)
    for name, mpath, is_onnx in models_meta:
        print(f"Running accuracy eval for {name} ({mpath.name}) @ conf=0.35...")
        acc = evaluate_accuracy(mpath, conf=0.35, is_onnx=is_onnx)
        accuracy_results_35.append({"backend": name, "model": mpath.name, "conf": 0.35, **acc})

    base_r_35 = accuracy_results_35[0]["recall"]
    base_p_35 = accuracy_results_35[0]["precision"]
    for row in accuracy_results_35:
        row["recall_drop_pct"] = round((base_r_35 - row["recall"]) * 100.0, 2)
        row["precision_drop_pct"] = round((base_p_35 - row["precision"]) * 100.0, 2)
        row["meets_gate"] = (row["recall_drop_pct"] <= 3.0 and row["precision_drop_pct"] <= 1.0)

    full_report = {
        "benchmark_fairness": {
            "threads": THREADS,
            "warmup_runs": WARMUP_RUNS,
            "iterations": len(test_images),
            "test_split": TEST_TXT,
        },
        "latency_throughput": latency_table,
        "accuracy_conf_0_55": accuracy_results_55,
        "accuracy_conf_0_35": accuracy_results_35,
    }

    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)

    # Print summary tables
    print("\n" + "=" * 90)
    print(" TABLE 1: FAIR LATENCY & THROUGHPUT (imgsz=640, threads=4, warmup=10, N=116)")
    print("=" * 90)
    print(f"{'Backend':<24} | {'p50 (ms)':<9} | {'Mean (ms)':<9} | {'p95 (ms)':<9} | {'FPS':<6} | {'Real Speedup'}")
    print("-" * 90)
    for row in latency_table:
        print(f"{row['backend']:<24} | {row['p50_ms']:<9.2f} | {row['mean_ms']:<9.2f} | {row['p95_ms']:<9.2f} | {row['fps']:<6.1f} | {row['speedup']:.2f}x")
    print("=" * 90)

    print("\n" + "=" * 95)
    print(" TABLE 2: ACCURACY ON REAL UAVVASTE TEST SPLIT (conf: 0.55)")
    print("=" * 95)
    print(f"{'Backend':<24} | {'Precision':<10} | {'Recall':<8} | {'F1':<8} | {'mAP50':<8} | {'R Drop (pt)':<11} | {'Pass Gate'}")
    print("-" * 95)
    for row in accuracy_results_55:
        p_str = "YES" if row["meets_gate"] else "NO"
        print(f"{row['backend']:<24} | {row['precision']:<10.4f} | {row['recall']:<8.4f} | {row['f1']:<8.4f} | {row['map50']:<8.4f} | {row['recall_drop_pct']:<11.2f} | {p_str}")
    print("=" * 95)

    print("\n" + "=" * 95)
    print(" TABLE 3: ACCURACY ON REAL UAVVASTE TEST SPLIT (conf: 0.35)")
    print("=" * 95)
    print(f"{'Backend':<24} | {'Precision':<10} | {'Recall':<8} | {'F1':<8} | {'mAP50':<8} | {'R Drop (pt)':<11} | {'Pass Gate'}")
    print("-" * 95)
    for row in accuracy_results_35:
        p_str = "YES" if row["meets_gate"] else "NO"
        print(f"{row['backend']:<24} | {row['precision']:<10.4f} | {row['recall']:<8.4f} | {row['f1']:<8.4f} | {row['map50']:<8.4f} | {row['recall_drop_pct']:<11.2f} | {p_str}")
    print("=" * 95)


if __name__ == "__main__":
    main()
