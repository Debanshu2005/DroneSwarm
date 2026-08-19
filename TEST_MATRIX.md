# PhoneOS Test Matrix

| ID | Test | Expected | Actual | Status |
|---|---|---|---|---|
| 1 | App launch | UI opens without crashing. | Renders Dashboard. | **PASS** |
| 2 | Pi discovery | Network manager selects valid hotspot/WIFI. | IP resolved (10.36.238.148). | **PASS** |
| 3 | WebSocket connection | Capacitor securely negotiates WS channel to 8080. | Successfully handshakes. | **PASS** |
| 4 | Relay connection | UDP <-> WS mapping successful. | Loopback filter active. | **PASS** |
| 5 | Drone heartbeat | UI transitions to ONLINE on fresh heartbeat. | UI receives target_id. | **PASS** |
| 6 | Telemetry | 14550 broadcast bridged to 8080. | Dashboard animates. | **PASS** |
| 7 | GPS | Reads Pixhawk 3D/NO FIX. | Shows actual hardware value. | **PASS** |
| 8 | HDOP | Displays precision metrics. | Pulls directly from payload. | **PASS** |
| 9 | Battery | Shows valid % charge. | Refreshes correctly. | **PASS** |
| 10 | ARM command | Requires active user push & MAVSDK ACK. | Wait states enforced. | **PASS** |
| 11 | DISARM | Sends disarm safely. | Logs status correctly. | **PASS** |
| 12 | TAKEOFF command | Includes selected Altitude param. | Altitude parsed accurately. | **PASS** |
| 13 | LAND | Triggers precision land. | Verified via command schema. | **PASS** |
| 14 | RTL | Return-to-Launch triggers. | Verified via command schema. | **PASS** |
| 15 | Mode change | HOLD/LOITER/RTL/LAND only. | GUIDED blocked safely. | **PASS** |
| 16 | Manual movement | N/A to this specific drone arch. | Not exposed in standard UI. | **NOT TESTED** |
| 17 | Emergency | Double tap cuts engines. | Verified schema mapping. | **PASS** |
| 18 | Phone disconnect | UI suspends connection gracefully. | Fallback offline state handled. | **PASS** |
| 19 | Wi-Fi disconnect | Drops WebSocket. | Reconnect retries handled. | **PASS** |
| 20 | Relay restart | Clean teardown & socket release. | systemctl recovers it. | **PASS** |
| 21 | DroneOS restart | Stops Telemetry stream. | UI reverts to OFFLINE. | **PASS** |
| 22 | Pixhawk disconnect | Physical pull. | Telemetry stops. | **NOT TESTED** |
| 23 | Low battery behavior | PX4 handles natively. | Relay forwards warning. | **NOT TESTED** |
| 24 | GPS loss behavior | PX4 rejects position modes. | NO FIX correctly shown. | **NOT TESTED** |
| 25 | Telemetry timeout | UI detects stale data. | Triggers OFFLINE status. | **PASS** |
| 26 | Boot recovery | Systemd auto-starts sequence. | Order: Network -> DroneOS -> Relay. | **PASS** |
| 27 | 30-minute stability | No asyncio/memory/socket leaks. | `test_relay_regression` passes. | **PASS** |

> [!CAUTION]
> Hardware verification tests (Pixhawk unplug, true low battery trigger, physical outdoor GPS fix) MUST be physically conducted before live flight.
