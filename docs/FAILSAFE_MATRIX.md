# PhoneOS Failsafe Matrix

The Android Application **NEVER** acts as the primary safety governor; it strictly delegates to native PX4 behavior unless otherwise noted.

| Trigger | Detection | Action | PX4 / Native Safety Handling | User Notification | Recovery Condition |
|---|---|---|---|---|---|
| **Heartbeat Timeout** | Relay UDP silence > 2s | UI drops states | DroneOS marks peer LOST | `STALE` red indicator | Reception of heartbeat packet |
| **Telemetry Timeout** | No telemetry > 5s | UI marks `OFFLINE` | PX4 ignores Ground Station loss (by default) | Drone status UI turns gray/offline | Fresh telemetry arrives |
| **Phone Connection Loss** | WS Disconnect | App halts commands | Native PX4 datalink failsafe (RTL) | Reconnecting banner | WS reconnects to Pi |
| **WiFi Loss** | WS Drops | Reconnection retry | Native PX4 datalink failsafe (RTL) | `CONNECTION LOST` screen | WiFi reassociates |
| **Relay Loss** | WS drops cleanly | Connection aborts | PX4 unaware | `CONNECTION LOST` | Relay systemd restarts |
| **DroneOS Failure** | UDP halts | UI sees timeout | PX4 continues failsafe | `STALE` heartbeat/telemetry | DroneOS systemd restarts |
| **Pixhawk Connection Loss** | Serial timeout | DroneOS throws error | PX4 potentially falls from sky / unknown | `PX4 Disconnected` status | Serial repower / reboot |
| **GPS Loss** | EKF flags false | UI disables Takoff | PX4 rejects Position mode | `NO FIX` red indicator | 3D Fix reacquired |
| **Low Battery** | V < threshold | UI marks yellow | PX4 triggers warning | Yellow Battery % | Plug in / Replace |
| **Critical Battery** | V < critical | UI marks red | PX4 triggers RTL/LAND | Red Battery % | Replace battery |
| **Command ACK Timeout** | WS > 5s no resp | Reset UI lock | Command implicitly rejected | `TIMEOUT` toast | Resubmit command |
| **Command Duplication** | React state `SENDING` | Block button | N/A | Grayed out buttons | State returns to `IDLE` |

**Verification Status**: All UI notifications are [SOFTWARE VERIFIED]. All PX4 Native Actions [REQUIRE PHYSICAL VALIDATION].
