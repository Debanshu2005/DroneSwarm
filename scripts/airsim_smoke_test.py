import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description="AirSim Smoke Test")
    parser.add_argument("--vehicle", default="Drone1")
    parser.add_argument("--pkg", default="DroneOS")
    args = parser.parse_args()

    try:
        import airsim
    except ImportError:
        print("AirSim module not found. Smoke test exiting cleanly.")
        sys.exit(0)

    try:
        client = airsim.MultirotorClient(port=41451)
        client.confirmConnection()
        client.enableApiControl(True, args.vehicle)
    except Exception as e:
        print(f"AirSim not running or failed to connect: {e}")
        sys.exit(0)

    print(f"Connected to AirSim for {args.vehicle} using package {args.pkg}")

    try:
        home = client.getHomeGeoPoint(args.vehicle)
        print(f"Home GeoPoint: {home.latitude}, {home.longitude}, {home.altitude}")
        
        gps = client.getGpsData("", args.vehicle)
        print(f"Current GPS: {gps.gnss.geo_point.latitude}, {gps.gnss.geo_point.longitude}, {gps.gnss.geo_point.altitude}")

        print("Arming...")
        client.armDisarm(True, args.vehicle)

        print("Taking off (5m)...")
        client.takeoffAsync(10.0, args.vehicle).join()
        client.moveToZAsync(-5.0, 5.0, vehicle_name=args.vehicle).join()

        print("Moving forward (3m)...")
        yaw_mode = airsim.YawMode(is_rate=True, yaw_or_rate=0.0)
        client.moveByVelocityBodyFrameAsync(3.0, 0.0, 0.0, 1.0, airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode, args.vehicle).join()

        print("Goto 10m north of home...")
        client.moveToPositionAsync(10.0, 0.0, -5.0, 5.0, vehicle_name=args.vehicle).join()

        print("RTL...")
        client.goHomeAsync(30.0, args.vehicle).join()
        
        print("Done.")

    except Exception as e:
        print(f"Smoke test failed: {e}")
    finally:
        try:
            client.enableApiControl(False, args.vehicle)
        except:
            pass

if __name__ == "__main__":
    main()
