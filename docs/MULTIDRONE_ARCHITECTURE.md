# PhoneOS Multi-Drone Architecture

## System Pipeline
1. **DroneOS (Python/MAVSDK)**: Runs on the companion computer of each drone. Interacts with the Pixhawk via serial/USB. Broadcasts telemetry via UDP on port `14550`. Listens for commands on UDP.
2. **PhoneOS Relay (Python)**: Runs on a central Raspberry Pi. Listens for UDP broadcasts on port `14550`. Opens a WebSocket server on port `8080`. Forwards UDP telemetry to all connected WebSocket clients. Receives commands via WebSocket, unpacks the `target_id`, and routes them via UDP Unicast to the specific drone.
3. **PhoneOS Android App (React/Capacitor)**: Connects to the Relay via WebSocket.

## Data Model (App.jsx)
The core frontend state is a dictionary mapped by `drone_id`:
```javascript
drones = {
  [drone_id]: {
    id: string,
    status: string,
    lastSeen: number,
    telemetry: object,
    commandState: { action, state, cmd_id }
  }
}
```

## Protocol Additions
- `ControlMessage` now supports a `command_id` parameter to uniquely identify an action. When the drone (or relay) ACKs the message via `STATUS` or `ERROR`, it can include the `command_id` so the frontend knows exactly which drone's command state to transition from `SENDING` to `ACCEPTED` or `REJECTED`.

## Isolation
- A malformed packet from one drone will only fail parsing for that specific packet. The React error boundary and `try/catch` in `WebSocketManager` ensures the app does not crash.
- Stale telemetry affects only the `lastSeen` timestamp of the specific `drone_id`, transitioning only that drone to `OFFLINE`.
