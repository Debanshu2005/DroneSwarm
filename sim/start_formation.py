"""
Quick utility to takeoff all 4 AirSim drones and command a 4-drone V-formation.
Usage:
    python sim/start_formation.py
"""
import json
import socket
import sys
import time

def main():
    print("======================================================================")
    print("Initiating Swarm Takeoff & V-Formation Deployment...")
    print("======================================================================")

    # 1. Takeoff via AirSim API if available
    try:
        import airsim
        print("Connecting to AirSim client...")
        client = airsim.MultirotorClient()
        client.confirmConnection()

        vehicles = ["Drone1", "Drone2", "Drone3", "Drone4"]
        print("Enabling API control, arming, and taking off all 4 drones in AirSim...")
        for v in vehicles:
            client.enableApiControl(True, vehicle_name=v)
            client.armDisarm(True, vehicle_name=v)

        tasks = [client.takeoffAsync(vehicle_name=v, timeout_sec=15) for v in vehicles]
        for t in tasks:
            t.join()
        print("Takeoff complete! Waiting 2s to settle...")
        time.sleep(2.0)
    except ImportError:
        print("[Notice] AirSim python module not found; skipping AirSim takeoff call.")
    except Exception as e:
        print(f"[Notice] AirSim takeoff error (or vehicles already in air): {e}")

    # 2. Dispatch FORMATION_UPDATE via UDP to all 4 DroneOS instances
    ports = {
        "drone1": 14550,
        "drone2": 14551,
        "drone3": 14552,
        "drone4": 14553,
    }
    slot_assignments = {
        "drone1": 0,
        "drone2": 1,
        "drone3": 2,
        "drone4": 3,
    }

    print("Sending FORMATION_UPDATE command to all 4 DroneOS nodes...")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for did, port in ports.items():
            payload = json.dumps({
                "msg_type": "control",
                "action": "formation_update",
                "target_id": did,
                "sender_id": "sim_cli",
                "timestamp": time.time(),
                "params": {
                    "type": "V",
                    "spacing": 15.0,
                    "speed": 5.0,
                    "slot_assignments": slot_assignments,
                    "members": ["drone2", "drone3", "drone4"],
                },
            }).encode("utf-8")
            sock.sendto(payload, ("127.0.0.1", port))
            print(f" -> Dispatched FORMATION_UPDATE to {did} on UDP 127.0.0.1:{port}")

    print("======================================================================")
    print("SUCCESS: 4-Drone V-Formation has been commanded!")
    print("CoordinationManager on Drone 1 will transition to: state=QUORUM healthy=4/4")
    print("")
    print("To test follower-loss healing with ANY drone of your choice:")
    print("  Kill Drone 3:   sim\\kill_drone.bat 3   (or press Ctrl+C in Drone 3's terminal)")
    print("  Kill Drone 4:   sim\\kill_drone.bat 4   (or press Ctrl+C in Drone 4's terminal)")
    print("  Kill Drone 2:   sim\\kill_drone.bat 2   (or press Ctrl+C in Drone 2's terminal)")
    print("======================================================================")

if __name__ == "__main__":
    main()
