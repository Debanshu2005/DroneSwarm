#!/usr/bin/env python3
"""
CLI utility to spawn, manage, and inspect physical garbage targets in Unreal Engine AirSim.
"""

import argparse
import math
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import airsim
from DroneOS.perception.geolocate import meters_to_lat_lon

DEFAULT_TEXTURE = REPO_ROOT / "ml" / "data" / "optimal_airsim_target.png"


def get_client(host="127.0.0.1", port=41451):
    client = airsim.MultirotorClient(ip=host, port=port)
    client.confirmConnection()
    return client


def get_home_geo_point(client):
    try:
        home_geo = client.getHomeGeoPoint()
        origin_lat = home_geo.latitude if hasattr(home_geo, "latitude") else 47.641468
        origin_lon = home_geo.longitude if hasattr(home_geo, "longitude") else -122.140165
    except Exception:
        origin_lat, origin_lon = 47.641468, -122.140165
    return origin_lat, origin_lon


def spawn_target(client, name: str, x: float, y: float, z: float = 0.0, scale=(0.35, 0.35, 0.02), texture_path=None):
    pose = airsim.Pose(airsim.Vector3r(x, y, z), airsim.to_quaternion(0, 0, 0))
    sc = airsim.Vector3r(scale[0], scale[1], scale[2])

    existing = client.simListSceneObjects(f"^{name}$")
    if name in existing:
        client.simSetObjectPose(name, pose, teleport=True)
        if hasattr(client, "simSetObjectScale"):
            client.simSetObjectScale(name, sc)
    else:
        client.simSpawnObject(name, "EditorCube", pose, sc, physics_enabled=False, is_blueprint=False)

    tex = Path(texture_path or DEFAULT_TEXTURE).resolve().as_posix()
    if Path(tex).exists():
        client.simSetObjectMaterialFromTexture(name, tex, 0)
        client.simSetObjectMaterialFromTexture(name, tex, 1)

    actual_pose = client.simGetObjectPose(name)
    origin_lat, origin_lon = get_home_geo_point(client)
    px, py, pz = actual_pose.position.x_val, actual_pose.position.y_val, actual_pose.position.z_val
    lat, lon = meters_to_lat_lon(px, py, origin_lat, origin_lon)
    print(f"  -> Spawned '{name}': NED=({px:.2f}, {py:.2f}, {pz:.2f}) m | GPS=({lat:.6f}, {lon:.6f})")
    return {"name": name, "x": px, "y": py, "z": pz, "lat": lat, "lon": lon}


def list_targets(client):
    objs = client.simListSceneObjects("^GarbageTarget.*")
    origin_lat, origin_lon = get_home_geo_point(client)
    print(f"\nActive Garbage Targets in AirSim ({len(objs)} found):")
    if not objs:
        print("  (None found. Use 'python scripts/spawn_garbage.py' to spawn targets)")
        return
    for name in objs:
        p = client.simGetObjectPose(name)
        px, py, pz = p.position.x_val, p.position.y_val, p.position.z_val
        lat, lon = meters_to_lat_lon(px, py, origin_lat, origin_lon)
        print(f"  - {name}: NED=({px:6.2f}, {py:6.2f}, {pz:6.2f}) m | GPS=({lat:.6f}, {lon:.6f})")


def clear_targets(client):
    objs = client.simListSceneObjects("^GarbageTarget.*")
    print(f"\nClearing {len(objs)} garbage targets from AirSim...")
    for name in objs:
        client.simDestroyObject(name)
        print(f"  -> Removed '{name}'")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Spawn or manage garbage targets in AirSim.")
    parser.add_argument("--host", default="127.0.0.1", help="AirSim RPC host")
    parser.add_argument("--port", type=int, default=41451, help="AirSim RPC port")
    parser.add_argument("--list", action="store_true", help="List all current garbage targets in the scene")
    parser.add_argument("--clear", action="store_true", help="Remove all garbage targets from the scene")
    parser.add_argument("--x", type=float, default=None, help="Custom target X coordinate (North in meters)")
    parser.add_argument("--y", type=float, default=None, help="Custom target Y coordinate (East in meters)")
    parser.add_argument("--z", type=float, default=0.0, help="Custom target Z coordinate (Down in meters)")
    parser.add_argument("--name", type=str, default=None, help="Custom target object name")
    parser.add_argument("--random", type=int, default=0, help="Spawn N randomly scattered garbage targets")

    args = parser.parse_args()

    try:
        client = get_client(args.host, args.port)
    except Exception as e:
        print(f"Error connecting to AirSim at {args.host}:{args.port}: {e}")
        print("Make sure Unreal Engine 4 / AirSim is running and in Play mode.")
        sys.exit(1)

    if args.list:
        list_targets(client)
        return

    if args.clear:
        clear_targets(client)
        return

    if args.x is not None and args.y is not None:
        target_name = args.name or f"GarbageTarget_custom_{int(args.x)}_{int(args.y)}"
        print(f"\nSpawning custom target '{target_name}' at ({args.x}, {args.y}, {args.z})...")
        spawn_target(client, target_name, args.x, args.y, args.z)
        return

    if args.random > 0:
        print(f"\nSpawning {args.random} randomly scattered garbage targets...")
        for i in range(1, args.random + 1):
            rx = random.uniform(2.0, 22.0)
            ry = random.uniform(2.0, 15.0)
            tname = f"GarbageTarget_rnd_{i}"
            spawn_target(client, tname, rx, ry, 0.0)
        return

    # Default action: Spawn the two standard benchmark targets (15m separation)
    print("\nSpawning standard benchmark garbage targets (GarbageTarget1 and GarbageTarget2, 15m separation)...")
    spawn_target(client, "GarbageTarget1", 3.75, 6.50, 0.0)
    spawn_target(client, "GarbageTarget2", 18.75, 6.50, 0.0)
    print("\nSuccess! Both targets are placed in the flight path and textured.")


if __name__ == "__main__":
    main()

