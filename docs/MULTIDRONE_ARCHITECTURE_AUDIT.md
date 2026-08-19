# PhoneOS Multi-Drone Architecture Audit

## 1. Current Architecture
- **Frontend**: React-based Single Page App (App.jsx, App.css) using a custom `WebSocketManager.js` wrapper.
- **Relay**: Python script (`relay.py`) converting UDP (from/to DroneOS) to WebSockets (from/to Android).
- **Backend (DroneOS)**: Python-based MAVSDK flight manager. Communicates via UDP telemetry and control commands using JSON schemas.

## 2. Current Message Protocol
Defined in `mobile/src/protocol/messages.js`.
- `HeartbeatMessage`
- `ControlMessage`
- `EmergencyMessage`
- Telemetry format (received as JSON over WebSocket).
All messages inherit `sender_id`, `target_id`, and `msg_type`.

## 3. Current Drone Identification Mechanism
- Drone ID is inferred from the `sender_id` field in incoming UDP packets at the Relay, and WS packets at the frontend.
- `relay.py` tracks endpoints via `self.known_endpoints[sender_id] = addr` to correctly target unicast commands.

## 4. Current Telemetry Flow
- MAVSDK -> DroneOS -> UDP Broadcast -> Relay -> WS Broadcast -> Android.
- Android handles in `App.jsx` via `manager.subscribe(MessageType.TELEMETRY, ...)` and stores in a global `drones` object.

## 5. Current Command Flow
- Android UI -> `wsManager.send(ControlMessage)` -> Relay WS -> Relay UDP (Unicast or Broadcast) -> DroneOS -> MAVSDK.
- Commands currently have global UI states (`armState`, `takeoffState`).

## 6. Current WebSocket Lifecycle
- Managed by `WebSocketManager.js`.
- Maintains a `drones` mapping. Heartbeats > 5000ms mark a drone as `LOST`.

## 7. Current Reconnect Behavior
- `WebSocketManager.js` has exponential backoff reconnection.
- Android UI has manual retry triggers.

## 8. Current Race Conditions & Crash Risks
- The command UI has global locks instead of per-drone locks. Sending a command to Drone 1 locks out Drone 2.
- UI expects an ACK (`CommandStatus`) which is just matched loosely, not by `command_id` or explicit `target_id` validation.

## 9. Current Multi-Drone Limitations
- The UI forces selection of ONE drone at a time via a dropdown (or ALL).
- Cannot view fleet status in a grid.
- Cannot select arbitrary sub-groups of drones (e.g., Drone 1 and 3 only).
- Safety gates and modal popups (from our previous UX overhaul) check a single `activeDrone`.
- No per-drone command state (e.g., `command_id` queuing).

## 10. Files That Must Change
- `PhoneOS/mobile/src/App.jsx` (Complete refactor for multi-drone state and UI)
- `PhoneOS/mobile/src/App.css` (Grid styling for fleet, multi-selection)
- `PhoneOS/mobile/src/protocol/messages.js` (Add `command_id` and better tracking)
- `PhoneOS/docs/MULTIDRONE_USER_GUIDE.md` (New)
- `PhoneOS/docs/MULTIDRONE_ARCHITECTURE.md` (New)
- `PhoneOS/docs/MULTIDRONE_SAFETY.md` (New)
- `PhoneOS/docs/MULTIDRONE_TEST_REPORT.md` (New)
- `PhoneOS/docs/FINAL_UI_REPORT.md` (Update)
- `PhoneOS/tests/test_multi_drone.py` (New tests)
- `PhoneOS/tests/TEST_MATRIX.md` (Update)

## 11. Files That Must NOT Change
- `~/Projects/SwarmOS/*`
- `PhoneOS/relay/relay.py` (It is already capable of handling multi-drone routing cleanly!)
- `PhoneOS/DroneOS/*` (DroneOS instances act autonomously; no changes needed there unless a breaking protocol flaw is discovered).
