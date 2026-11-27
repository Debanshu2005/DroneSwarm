"""Verify that the active GarbageDetector stack matches golden-frame confidences.

Exits 0 if every golden image's max_raw_conf is within tolerance, non-zero otherwise.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.perception.preflight import verify_golden_frames


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify detector stack against golden frames.")
    parser.add_argument("--weights", default="models/garbage.pt", help="Model weights path (.pt or .onnx)")
    parser.add_argument(
        "--manifest",
        default="assets/perception/golden/manifest.json",
        help="Path to golden-frame manifest JSON",
    )
    parser.add_argument("--device", default="auto", help="Inference device (auto, cpu, cuda:0)")
    args = parser.parse_args(argv)

    weights_path = Path(args.weights)
    if not weights_path.is_absolute():
        weights_path = REPO_ROOT / weights_path
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path

    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    if not weights_path.exists():
        print(f"ERROR: weights not found: {weights_path}", file=sys.stderr)
        return 2

    status, reason, results = verify_golden_frames(
        weights_path=weights_path,
        manifest_path=manifest_path,
        device=args.device,
    )
    for row in results:
        exp = f"{row['expected']:.4f}" if row["expected"] is not None else "N/A"
        act = f"{row['actual']:.4f}" if row["actual"] is not None else "N/A"
        tag = "OK" if row["ok"] else "DRIFT"
        print(f"[{tag}] {row['file']}: expected={exp} actual={act}")

    if status != "OK":
        print(f"FAIL: {reason}", file=sys.stderr)
        return 1
    print("PASS: detector stack matches golden-frame expectations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
