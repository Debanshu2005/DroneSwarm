"""
Orchestration script for 4-drone live swarm validation.
Launches the real swarm (Drone1..Drone4) via scripts/run_sim_swarm.py,
flies Drone1 over the target at 5m at 0.5 m/s, and collects First-Detection
summaries, peer discovery, and deferral diagnostics.

Order of operations:
1st: Take off Drone1 and position at (-3.0, 0.0, -5.0)
2nd: Spawn the garbage target at origin (0, 0, 0)
3rd: Launch 4-drone real swarm with DRONEOS_PROFILE=sim and stagger=1.0s
4th: Execute pass over target at 5m at 0.5 m/s
5th: Collect summaries, deferral duration, and GPU latency
"""

import asyncio
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import airsim
from DroneOS.shared.communication.serializers import JsonSerializer
from DroneOS.shared.protocol.messages import CommandAction, ControlMessage
from scripts.spawn_garbage import clear_targets
from scripts.validate_single_drone_airsim import setup_scene_target

TARGET_TEXTURE = REPO_ROOT / "ml" / "data" / "uavvaste_high_conf_target.png"
LOG_DIR = REPO_ROOT / "scratch" / "swarm_logs"


def kill_proc_tree(pid: int):
    """Forcefully kill process and all descendants on Windows."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    except Exception:
        pass


async def main():
    print("=" * 80, flush=True)
    print("FOUR-DRONE REAL SWARM LIVE VALIDATION", flush=True)
    print("=" * 80, flush=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for old_log in LOG_DIR.glob("*.log"):
        try:
            old_log.unlink()
        except Exception:
            pass

    client = airsim.MultirotorClient(ip="127.0.0.1", port=41451)
    client.confirmConnection()
    print("Connected to AirSim on port 41451.", flush=True)

    # 1. Clear any existing targets in AirSim
    clear_targets(client)
    print("Cleared any existing targets in AirSim.", flush=True)

    # 2. STEP 1: TAKE OFF FIRST, then position Drone1 at (-4.0, 0.0, -5.0)
    print("\n--- STEP 1: TAKING OFF DRONE 1 BEFORE SPAWNING TARGET ---", flush=True)
    client.enableApiControl(True, vehicle_name="Drone1")
    client.armDisarm(True, vehicle_name="Drone1")
    print("Commanding Drone1 takeoff...", flush=True)
    client.takeoffAsync(timeout_sec=8, vehicle_name="Drone1").join()
    print("Moving Drone1 to start position (-4.0, 0.0, -5.0)...", flush=True)
    client.moveToPositionAsync(-4.0, 0.0, -5.0, 1.5, timeout_sec=10, vehicle_name="Drone1").join()
    
    # Fine-adjust to ensure drone is centered along y=0.0
    for _ in range(3):
        kin = client.simGetGroundTruthKinematics(vehicle_name="Drone1")
        if abs(kin.position.y_val) > 0.2 or abs(kin.position.z_val - (-5.0)) > 0.3:
            client.moveToPositionAsync(-4.0, 0.0, -5.0, 1.0, timeout_sec=4, vehicle_name="Drone1").join()
        else:
            break
            
    kin = client.simGetGroundTruthKinematics(vehicle_name="Drone1")
    print(f"Drone1 airborne at altitude: {-kin.position.z_val:.2f}m (position: x={kin.position.x_val:.2f}, y={kin.position.y_val:.2f}, z={kin.position.z_val:.2f}).", flush=True)

    # 3. STEP 2: THEN SPAWN THE GARBAGE at origin (0, 0, 0)
    print("\n--- STEP 2: SPAWNING GARBAGE TARGET AT ORIGIN ---", flush=True)
    await setup_scene_target(
        client=client,
        target_name="GarbageTarget_HighConf",
        offset_north_m=0.0,
        offset_east_m=0.0,
        ground_z_m=0.0,
        scale_m=(0.75, 0.75, 0.25),
        mesh_name="Cube",
        unique_name=False,
        texture_path=TARGET_TEXTURE,
    )
    print("Spawned GarbageTarget_HighConf at origin (0, 0, 0) with high-confidence texture.", flush=True)

    # 4. STEP 3: Launch 4-drone real swarm via run_sim_swarm.py
    python_exe = sys.executable
    swarm_env = os.environ.copy()
    swarm_env["DRONEOS_PROFILE"] = "sim"
    swarm_env["PYTHONUNBUFFERED"] = "1"

    print("\n--- STEP 3: LAUNCHING 4-DRONE SIMULATION SWARM (stagger=2.0s) ---", flush=True)
    swarm_proc = subprocess.Popen(
        [python_exe, "-u", "scripts/run_sim_swarm.py", "--stagger", "2.0", "--drones", "4", "--log-dir", str(LOG_DIR)],
        cwd=str(REPO_ROOT),
        env=swarm_env,
    )

    # 5. Wait for swarm startup and peer discovery while sending ARM commands and holding Drone1 position
    print("Waiting 36 seconds for 4-drone startup, PyTorch GPU warmup, and peer discovery...", flush=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    serializer = JsonSerializer()

    for s in range(36, 0, -2):
        print(f"  {s}s remaining...", flush=True)
        # Hold Drone1 at start position (-4.0, 0.0, -5.0)
        try:
            client.moveToPositionAsync(-4.0, 0.0, -5.0, 1.0, vehicle_name="Drone1")
        except Exception:
            pass
        # Send ARM to all 4 drones so safety gate passes
        for d_idx, port in enumerate([14550, 14551, 14552, 14553], 1):
            try:
                arm_cmd = ControlMessage(action=CommandAction.ARM, sender_id="gcs", target_id=f"drone{d_idx}", timestamp=time.time())
                sock.sendto(serializer.serialize(arm_cmd), ("127.0.0.1", port))
            except Exception:
                pass
        await asyncio.sleep(2.0)
    sock.close()

    # Re-verify Drone1 is airborne and armed in AirSim
    client.enableApiControl(True, vehicle_name="Drone1")
    client.armDisarm(True, vehicle_name="Drone1")

    # 6. STEP 4: Fly Drone1 over target at 5m at 0.5 m/s from -4.0m to +4.0m
    print("\n--- STEP 4: FLYING DRONE 1 OVER TARGET AT 5M AT 0.5 M/S (-4.0m -> +4.0m) ---", flush=True)
    client.moveToPositionAsync(4.0, 0.0, -5.0, 0.5, timeout_sec=25, vehicle_name="Drone1")
    for s in range(18, 0, -2):
        print(f"  flight progress ({18-s}/18s)...", flush=True)
        await asyncio.sleep(2.0)
    print(">>> Drone1 completed pass over target.", flush=True)

    # 7. STEP 5: Hover for 12s to observe confirmation, peer propagation, and deferral
    print("\n--- STEP 5: HOVERING AT +4.0M FOR 12S TO OBSERVE PEER SYNC & DEFERRAL ---", flush=True)
    await asyncio.sleep(12.0)

    # 8. Terminate swarm
    print("\nStopping 4-drone swarm cleanly via stop flag...", flush=True)
    stop_flag = LOG_DIR / "stop.flag"
    stop_flag.touch()
    try:
        swarm_proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        kill_proc_tree(swarm_proc.pid)
    if stop_flag.exists():
        stop_flag.unlink()
    print("Swarm processes terminated.", flush=True)

    # Reset AirSim Drone1
    try:
        clear_targets(client)
        client.moveToPositionAsync(0.0, 0.0, -2.0, 2.0, timeout_sec=3, vehicle_name="Drone1")
        await asyncio.sleep(2.0)
        client.landAsync(timeout_sec=3, vehicle_name="Drone1")
        await asyncio.sleep(2.0)
        client.armDisarm(False, vehicle_name="Drone1")
        client.enableApiControl(False, vehicle_name="Drone1")
    except Exception:
        pass

    # 9. Parse and report per-drone First-Detection Summary and diagnostics
    print("\n" + "=" * 80, flush=True)
    print("FOUR-DRONE SWARM VALIDATION RESULTS")
    print("=" * 80, flush=True)

    for i in range(1, 5):
        log_file = LOG_DIR / f"drone{i}.log"
        print(f"\n--- DRONE {i} (drone{i}) ---", flush=True)
        if not log_file.exists():
            print(f"Log file {log_file} does not exist.", flush=True)
            continue

        content = log_file.read_text(encoding="utf-8", errors="replace")

        # Device & inference latency
        dev_match = re.search(r"perception device=(\w+)\s+provider=([^\s]+)", content)
        prof_matches = re.findall(r"inference=([\d\.]+)ms", content)
        device_str = dev_match.group(1) if dev_match else "unknown"
        provider_str = dev_match.group(2) if dev_match else "unknown"
        inf_ms = prof_matches[-1] if prof_matches else "N/A"

        print(f"Perception Device: {device_str} (provider: {provider_str}) | GPU Inference: {inf_ms} ms", flush=True)

        # First-Detection Summary & Timeline Diagnostics
        summary_matches = re.findall(r"\[drone\d+\]\[TIMELINE SUMMARY.*?\] (.*?)(?=\n|$)", content)
        if summary_matches:
            print("First-Detection Summary:", flush=True)
            for sm in summary_matches:
                print(f"  {sm}", flush=True)
        else:
            first_raw_match = re.search(r"\[drone\d+\]\[TIMELINE T\+[\d\.]+s\].*?raw_dets=[1-9].*?(?=\n|$)", content)
            first_hit_match = re.search(r"\[drone\d+\]\[TIMELINE T\+[\d\.]+s\].*?hits=[1-9].*?(?=\n|$)", content)
            peer_disc_match = re.search(r"\[drone\d+\]\[TIMELINE PEER DISCOVERY\].*?(?=\n|$)", content)
            timeline_lines = [l for l in content.splitlines() if "[TIMELINE T+" in l]

            if first_raw_match or first_hit_match:
                print("First Detection Diagnostic:", flush=True)
                if first_raw_match:
                    print(f"  First Raw: {first_raw_match.group(0).strip()}", flush=True)
                if first_hit_match:
                    print(f"  First Hit: {first_hit_match.group(0).strip()}", flush=True)
            if peer_disc_match:
                print(f"  Peers Discovered: {peer_disc_match.group(0).strip()}", flush=True)
            if timeline_lines:
                print(f"  Last Timeline State: {timeline_lines[-1].strip()}", flush=True)
            elif not (first_raw_match or first_hit_match or peer_disc_match):
                print("First-Detection Summary: No timeline log emitted", flush=True)

        # Deferrals
        defer_lines = [l for l in content.splitlines() if "DETECTIONS_DEFERRED" in l or "deferred=True" in l or "Deferring detection" in l]
        if defer_lines:
            print(f"Deferrals Observed: {len(defer_lines)} instances", flush=True)
            print(f"  First: {defer_lines[0].strip()}", flush=True)
            print(f"  Last:  {defer_lines[-1].strip()}", flush=True)
            import datetime
            try:
                t0_str = defer_lines[0][:23]
                t1_str = defer_lines[-1][:23]
                t0 = datetime.datetime.strptime(t0_str, "%Y-%m-%d %H:%M:%S,%f")
                t1 = datetime.datetime.strptime(t1_str, "%Y-%m-%d %H:%M:%S,%f")
                dur_s = (t1 - t0).total_seconds()
                print(f"  Deferral Duration: {dur_s:.2f}s", flush=True)
            except Exception:
                pass
        else:
            print("Deferrals Observed: None (0 instances of DETECTIONS_DEFERRED)", flush=True)

        # Confirmed Alerts
        confirmed_lines = [l for l in content.splitlines() if "[ALERT CONFIRMED]" in l]
        if confirmed_lines:
            print(f"Confirmed Alerts: {len(confirmed_lines)}", flush=True)
            for cl in confirmed_lines[:3]:
                print(f"  {cl.strip()}", flush=True)
        else:
            print("Confirmed Alerts: 0", flush=True)

        # Peer Discovery
        peer_lines = [l for l in content.splitlines() if "peers discovered at" in l or "Discovered peer" in l or "Join" in l]
        if peer_lines:
            print(f"Peer Discovery Events: {len(peer_lines)} (Sample: {peer_lines[0].strip()[:90]})", flush=True)
        else:
            print("Peer Discovery Events: None logged", flush=True)

    print("\n" + "=" * 80, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
