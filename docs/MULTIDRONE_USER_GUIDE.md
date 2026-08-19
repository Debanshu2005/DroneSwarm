# PhoneOS Multi-Drone User Guide

## Connecting Multiple Drones
1. Power on the **PhoneOS Relay**.
2. Connect your Android device to the Relay's Wi-Fi.
3. Open the **PhoneOS Ground Control App**.
4. The app will automatically listen for all drone heartbeats. Drones are discovered automatically when they send telemetry.
5. As drones connect, they will appear in the **Fleet Overview** panel.

## Fleet Overview
- Each drone has a dedicated card displaying its ID, status, battery, GPS fix, altitude, and armed state.
- **Select Drones**: Tap a drone card to select it. The card will glow blue with a checkbox. 
- You can select multiple drones at once to perform group commands.
- Use **Select All** or **Clear** buttons for rapid selection.

## Individual Commands
1. Select a **single** drone card.
2. Scroll down to the **Group Controls** panel (which adapts to your selection).
3. The selected drone will appear in the safety gate.
4. Issue commands (ARM, TAKEOFF, etc.). Only the selected drone will respond.

## Group Commands
1. Select multiple drone cards.
2. Scroll to **Group Controls**. 
3. The safety gate will evaluate **all selected drones**. If even one fails pre-flight checks (e.g., low battery, no GPS), group commands like ARM or TAKEOFF will be rejected.
4. Press and hold the command button (e.g., **HOLD TO TAKEOFF SELECTED**). The command will be dispatched to all selected drones simultaneously.

## Telemetry and Failsafes
- Telemetry is isolated per-drone.
- If a drone disconnects, its card turns gray (OFFLINE).
- If the phone disconnects from the Relay, a "Connection Lost" screen appears. Once reconnected, the UI automatically recovers state.
- If the Wi-Fi drops or the relay restarts, simply wait for the connection pill to turn green again. No drone data is corrupted.
