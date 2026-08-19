# PhoneOS Architecture Audit

## 1. System Pipeline Overview
The actual end-to-end pipeline operates strictly as a decoupled mobile UI layer interacting with the core SwarmOS drone logic. The pipeline is validated as follows:
`Android Phone App (Capacitor/Vite)` → `Wi-Fi / Phone Hotspot` → `Raspberry Pi (swarmos-pi.local)` → `PhoneOS WebSocket Relay (Port 8080)` → `UDP (Port 14550)` → `DroneOS (FlightManager)` → `MAVSDK` → `Pixhawk`.

## 2. Component Audits
### A. PhoneOS/relay/relay.py
- **Input**: WebSocket text frames containing JSON encoded `BaseMessage` objects.
- **Output**: UDP datagrams targeting `127.0.0.1:14550`.
- **Protocol Mapping**: Directly bridges `ws://` to `udp://`.
- **Integrity**: Relay operates cleanly. Does NOT alter JSON payload schemas, mutate `sender_id`, or inject timestamps. It passively relays traffic. Broadcast `target_id=null` semantics are natively handled.
- **Error Handling**: Non-blocking `asyncio` implementation. Unresponsive clients are safely disconnected.

### B. PhoneOS/mobile/src/App.jsx
- **Input**: JSON Telemetry/Status streams originating from DroneOS.
- **Output**: JSON Control messages (ARM, TAKEOFF, SET_MODE).
- **Network Resiliency**: React dynamically maps to `localStorage.getItem("PhoneOS_WsUrl")`. Reconnect logic executes automatically. Invalid URLs catch cleanly without crashing Capacitor WebView.
- **Safety**: `CommandAction` payloads explicitly map to `DroneOS/protocol/messages.py`. Takeoff altitude is bundled precisely into `{ altitude_m: X }`. Pre-arm checks require active `window.confirm`. GPS coordinates and Satellite counts are pulled natively from the MAVSDK pipeline—no faking is performed.

### C. PhoneOS/deploy
- **Services**: `swarmos-drone.service` and `swarmos-relay.service`.
- **Validation**: Systemd unit files are properly sequenced. NetworkManager dependencies (`network-online.target`) are correct. Python virtual environments (`/home/pi/swarmos-venv/bin/python`) are strictly mapped.
