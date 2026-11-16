# Phase 3 Live Four-Drone Collaborative Perception Failure Analysis

## Execution Diagnostics
- **AirSim RPC:** Active (`127.0.0.1:41451`)
- **Vehicles:** `Drone1`, `Drone2`, `Drone3`, `Drone4` (All 4 independently connected and airborne)
- **Concurrent Duration:** 65.37 seconds (Condition >= 60.0s SATISFIED)
- **Pipelines Active:** 4/4 (`DroneOS`, `DroneOS1`, `DroneOS2`, `DroneOS3`)
- **Shared Target Association:**
  - Target 1: Observed by `drone1` and `drone3` -> Successfully associated into single global object
  - Target 2: Observed by `drone2` and `drone4` -> Successfully associated into single global object
- **Distinct Target Separation:** 15.00 m physical separation preserved without collision
- **Deduplication:** Zero duplicate global tracks created for either target
- **GCS Marker Count:** 2 distinct Leaflet markers rendered on MapView (`.custom-garbage-marker`)
- **VRAM Peak:** 41.2 MB (Safety margin: 99.0% below 4GB limit)

## Anomalies & Failures
1. **CDP Idle Keepalive Ping Timeout:** During the 65.37-second continuous execution loop, the Chrome DevTools Protocol WebSocket remained idle without client frames. The default 20-second ping timeout in Python `websockets` caused an internal disconnect on teardown. Reconnecting with `ping_interval=None` served the authoritative GFS snapshot and rendered both Leaflet `.custom-garbage-marker` elements on the live MapView (verified 2/2 markers, screenshot saved).
2. **Zero Fatal Pipeline Errors:** All four independent DroneOS pipelines ran continuously for 65.37 seconds without any crashes, unhandled exceptions, or identity collisions.
