"""Sync perception subsystem, protocol, serializer, config, and main files across all DroneOS instances."""

import argparse
from pathlib import Path
import re
import sys
from typing import List, Tuple

TARGET_INSTANCES = [1, 2, 3]


def substitute_namespace(content: str, instance_num: int) -> str:
    """Replace DroneOS package references with DroneOS{N}."""
    pkg = f"DroneOS{instance_num}"
    content = re.sub(r"\bfrom DroneOS\.", f"from {pkg}.", content)
    content = re.sub(r"\bimport DroneOS\.", f"import {pkg}.", content)
    # Also in main.py startup log banner: package=DroneOS
    content = content.replace("package=DroneOS", f"package={pkg}")
    return content


def sync_perception(repo_root: Path, check_only: bool = False) -> bool:
    """
    Sync DroneOS/perception and shared message/serializer/config files into DroneOS1/2/3.
    Preserves UTF-8 BOM on models.py for DroneOS1/2/3.
    Returns True if in sync, False if drift detected (or files updated).
    """
    source_root = repo_root / "DroneOS"
    perception_src_dir = source_root / "perception"

    files_to_sync: List[Path] = []
    if perception_src_dir.exists():
        for py_file in sorted(perception_src_dir.rglob("*.py")):
            files_to_sync.append(py_file)

    shared_files = [
        source_root / "shared" / "protocol" / "messages.py",
        source_root / "shared" / "communication" / "serializers.py",
        source_root / "shared" / "config" / "models.py",
        source_root / "main.py",
    ]
    files_to_sync.extend([p for p in shared_files if p.exists()])

    perception_yaml_src = source_root / "configs" / "perception.yaml"

    drift_found = False
    files_written = 0
    files_checked = 0

    for n in TARGET_INSTANCES:
        target_root = repo_root / f"DroneOS{n}"
        if not target_root.exists():
            continue

        # 1. Sync perception.yaml
        if perception_yaml_src.exists():
            dst_yaml = target_root / "configs" / "perception.yaml"
            dst_yaml.parent.mkdir(parents=True, exist_ok=True)
            yaml_content = perception_yaml_src.read_text(encoding="utf-8")
            if dst_yaml.exists():
                existing_yaml = dst_yaml.read_text(encoding="utf-8")
                if existing_yaml != yaml_content:
                    if check_only:
                        print(f"[DRIFT] {dst_yaml.relative_to(repo_root)} differs from source")
                        drift_found = True
                    else:
                        dst_yaml.write_text(yaml_content, encoding="utf-8")
                        files_written += 1
            else:
                if check_only:
                    print(f"[MISSING] {dst_yaml.relative_to(repo_root)}")
                    drift_found = True
                else:
                    dst_yaml.write_text(yaml_content, encoding="utf-8")
                    files_written += 1

        # 2. Sync python files
        for src_path in files_to_sync:
            rel = src_path.relative_to(source_root)
            dst_path = target_root / rel

            src_text = src_path.read_text(encoding="utf-8")
            expected_text = substitute_namespace(src_text, n)

            # Preserve UTF-8 BOM on models.py
            is_models = rel == Path("shared/config/models.py")
            expected_bytes = (b"\xef\xbb\xbf" if is_models else b"") + expected_text.encode("utf-8")

            files_checked += 1
            if dst_path.exists():
                actual_bytes = dst_path.read_bytes()
                if actual_bytes != expected_bytes:
                    if check_only:
                        print(f"[DRIFT] {dst_path.relative_to(repo_root)} differs from expected")
                        drift_found = True
                    else:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        dst_path.write_bytes(expected_bytes)
                        files_written += 1
            else:
                if check_only:
                    print(f"[MISSING] {dst_path.relative_to(repo_root)}")
                    drift_found = True
                else:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    dst_path.write_bytes(expected_bytes)
                    files_written += 1

    if check_only:
        if drift_found:
            print("Perception parity check FAILED: drift detected across instances.")
            return False
        else:
            print(f"Perception parity check PASSED: {files_checked} files in sync.")
            return True
    else:
        print(f"Sync completed: {files_written} files updated across DroneOS1/2/3.")
        return True


def main():
    parser = argparse.ArgumentParser(description="Sync DroneOS perception and shared files across instances.")
    parser.add_argument("--check", action="store_true", help="Check for drift without modifying files (exits non-zero on drift)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    ok = sync_perception(repo_root, check_only=args.check)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()

