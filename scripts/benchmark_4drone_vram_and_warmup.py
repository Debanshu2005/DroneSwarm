"""
Benchmark 4-drone multi-process VRAM and startup concurrency on GTX 1650.
Tests start_stagger_s = 0.25 vs 1.0 with AirSim running.
Logs memory_allocated/reserved per process, total GPU VRAM used, warm-up seconds per drone,
and whether any CUDA->CPU fallback fires.
"""

import json
from pathlib import Path
import subprocess
import time
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_GPU = REPO_ROOT / ".venv-gpu" / "Scripts" / "python.exe"

WORKER_CODE = """
import sys
import time
import json
from pathlib import Path

drone_idx = int(sys.argv[1])
stagger_s = float(sys.argv[2])
drone_id = f"drone{drone_idx + 1}"

# Apply stagger
time.sleep(drone_idx * stagger_s)

t_start = time.perf_counter()

import torch
from DroneOS.perception.detector import GarbageDetector

t_init = time.perf_counter()

detector = GarbageDetector(
    weights_path="models/garbage.pt",
    device="cuda",
    confidence=0.55,
    imgsz=640,
)

t_load_start = time.perf_counter()
loaded = detector.load()
t_load_end = time.perf_counter()

t_warmup_start = time.perf_counter()
detector.warmup(warmup_frames=3)
t_warmup_end = time.perf_counter()

alloc_mb = torch.cuda.memory_allocated() / (1024 * 1024) if torch.cuda.is_available() else 0.0
reserved_mb = torch.cuda.memory_reserved() / (1024 * 1024) if torch.cuda.is_available() else 0.0

fallback_fired = (detector.resolved_device == "cpu")

res = {
    "drone_id": drone_id,
    "drone_idx": drone_idx,
    "stagger_s": stagger_s,
    "load_time_s": round(t_load_end - t_load_start, 3),
    "warmup_time_s": round(t_warmup_end - t_warmup_start, 3),
    "total_startup_s": round(time.perf_counter() - t_start, 3),
    "cuda_allocated_mb": round(alloc_mb, 2),
    "cuda_reserved_mb": round(reserved_mb, 2),
    "resolved_device": detector.resolved_device,
    "fallback_fired": fallback_fired,
}

# Keep process alive briefly so parent can sample collective VRAM
sys.stdout.write(f"JSON_RES: {json.dumps(res)}\\n")
sys.stdout.flush()
time.sleep(4.0)
detector.close()
"""


def get_nvidia_smi_vram() -> Dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            encoding="utf-8",
        )
        total, used, free = map(float, out.strip().split(","))
        return {"total_vram_mb": total, "used_vram_mb": used, "free_vram_mb": free}
    except Exception as e:
        return {"error": str(e)}


def run_stagger_trial(stagger_s: float) -> Dict[str, Any]:
    print("\n=======================================================")
    print(f" TESTING 4-DRONE CONCURRENT STARTUP (stagger = {stagger_s:.2f}s)")
    print("=======================================================")

    vram_before = get_nvidia_smi_vram()
    print(f"VRAM Before Startup: {vram_before.get('used_vram_mb', 'N/A')} MB used / {vram_before.get('total_vram_mb', 'N/A')} MB total")

    processes = []
    t_global_start = time.perf_counter()

    for idx in range(4):
        p = subprocess.Popen(
            [str(PYTHON_GPU), "-c", WORKER_CODE, str(idx), str(stagger_s)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(REPO_ROOT),
        )
        processes.append(p)

    results_per_drone = []
    # Collect outputs
    for p in processes:
        for line in p.stdout:
            if line.startswith("JSON_RES: "):
                try:
                    payload = line[len("JSON_RES: "):].strip()
                    data = json.loads(payload)
                    results_per_drone.append(data)
                    print(
                        f"  [{data['drone_id']}] Load: {data['load_time_s']}s | Warmup: {data['warmup_time_s']}s | "
                        f"Total: {data['total_startup_s']}s | VRAM: alloc={data['cuda_allocated_mb']}MB, "
                        f"res={data['cuda_reserved_mb']}MB | Device: {data['resolved_device']} (Fallback: {data['fallback_fired']})"
                    )
                    break
                except Exception as e:
                    print(f"  Failed parsing worker payload: {line.strip()} ({e})")

    # Sample peak VRAM with all 4 processes active
    vram_peak = get_nvidia_smi_vram()
    print(f"VRAM at Peak (all 4 drones ready): {vram_peak.get('used_vram_mb', 'N/A')} MB used")

    # Wait for processes to exit
    for p in processes:
        p.wait()

    t_global_end = time.perf_counter()
    vram_after = get_nvidia_smi_vram()

    total_alloc_all_drones = sum(d["cuda_allocated_mb"] for d in results_per_drone)
    total_res_all_drones = sum(d["cuda_reserved_mb"] for d in results_per_drone)
    any_fallback = any(d["fallback_fired"] for d in results_per_drone)

    trial_summary = {
        "stagger_s": stagger_s,
        "global_elapsed_s": round(t_global_end - t_global_start, 2),
        "vram_before": vram_before,
        "vram_peak": vram_peak,
        "vram_after": vram_after,
        "total_allocated_mb_sum": round(total_alloc_all_drones, 2),
        "total_reserved_mb_sum": round(total_res_all_drones, 2),
        "any_cuda_fallback": any_fallback,
        "drone_metrics": results_per_drone,
    }
    return trial_summary


def main():
    print("=" * 70)
    print("4-DRONE VRAM AND STARTUP BENCHMARK ON GTX 1650 (WITH AIRSIM RUNNING)")
    print("=" * 70)

    summary_025 = run_stagger_trial(0.25)
    time.sleep(2.0)
    summary_100 = run_stagger_trial(1.00)

    report_path = REPO_ROOT / "ml" / "reports" / "vram_and_startup_benchmark.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"stagger_025": summary_025, "stagger_100": summary_100}, f, indent=2)

    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"{'Metric':<30} | {'stagger_s = 0.25':<18} | {'stagger_s = 1.00':<18}")
    print("-" * 70)
    print(f"{'Global Startup Elapsed':<30} | {summary_025['global_elapsed_s']} s{'':<14} | {summary_100['global_elapsed_s']} s")
    print(f"{'VRAM Peak Used (nvidia-smi)':<30} | {summary_025['vram_peak'].get('used_vram_mb')} MB{'':<10} | {summary_100['vram_peak'].get('used_vram_mb')} MB")
    print(f"{'VRAM Allocated Sum (4 drones)':<30} | {summary_025['total_allocated_mb_sum']} MB{'':<10} | {summary_100['total_allocated_mb_sum']} MB")
    print(f"{'VRAM Reserved Sum (4 drones)':<30} | {summary_025['total_reserved_mb_sum']} MB{'':<10} | {summary_100['total_reserved_mb_sum']} MB")
    print(f"{'CUDA->CPU Fallback Fired':<30} | {str(summary_025['any_cuda_fallback']):<18} | {str(summary_100['any_cuda_fallback']):<18}")
    print("=" * 70)


if __name__ == "__main__":
    main()
