# Multi-Drone Test Matrix

| Test ID | Category | Description | Status |
|---|---|---|---|
| MD-01 | Rendering | 1 drone connects and displays telemetry | PASS |
| MD-02 | Rendering | 2 drones connect and display separate telemetry | PASS |
| MD-03 | Rendering | 5 drones connect and display separate telemetry | PASS |
| MD-04 | Rendering | 10 drones connect and display separate telemetry | PASS |
| MD-05 | Lifecycle | Drone joins mid-session | PASS |
| MD-06 | Lifecycle | Drone leaves/times out | PASS |
| MD-07 | Robustness | Duplicate heartbeat handled without crash | PASS |
| MD-08 | Robustness | Malformed JSON packet handled gracefully | PASS |
| MD-09 | Robustness | Unknown packet type ignored | PASS |
| MD-10 | Robustness | Missing telemetry field handled safely | PASS |
| MD-11 | Network | WebSocket disconnect shows error | PASS |
| MD-12 | Network | WebSocket reconnect recovers session | PASS |
| MD-13 | Network | Relay restart handled gracefully | PASS |
| MD-14 | Command | Command timeout transitions to REJECTED | PASS |
| MD-15 | Command | Command rejection handled cleanly | PASS |
| MD-16 | Command | Command acknowledgement matched via `command_id` | PASS |
| MD-17 | Command | Simultaneous commands to different drones work | PASS |
| MD-18 | Command | Duplicate ARM prevented (button disabled while SENDING) | PASS |
| MD-19 | Group Cmd | Group ARM applied only to selected drones | PASS |
| MD-20 | Group Cmd | Group TAKEOFF applied only to selected drones | PASS |
| MD-21 | Group Cmd | Group LAND applied only to selected drones | PASS |
| MD-22 | Group Cmd | Group RTL applied only to selected drones | PASS |
| MD-23 | Group Cmd | One drone fails safety gate during group command (prevented) | PASS |
| MD-24 | Lifecycle | All drones offline | PASS |
| MD-25 | Network | Reconnect after all drones disappear | PASS |
| MD-26 | Performance | Rapid telemetry updates do not lock UI | PASS |
| MD-27 | Performance | 10+ drone rendering stability | PASS |
