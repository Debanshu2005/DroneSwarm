# ARM and TAKEOFF UX Flow

## 1. Objective
Replace standard intrusive browser `window.confirm` dialogs with a formalized, failsafe-integrated checklist and a 'Press-and-Hold' deliberate execution flow.

## 2. ARM Flow

### 2.1 State Verification (The Safety Gate)
Before the user can trigger the ARM sequence, the Android UI evaluates 7 telemetry conditions:
1. Drone Selected (`!= null`)
2. PX4 Connected (`flight_mode != "disconnected"`)
3. Telemetry Link Healthy (`age < 2000ms`)
4. Battery > 20%
5. GPS 3D Fix (`gps_valid === true`)
6. Valid Flight Mode (`!= "UNKNOWN"`)
7. Heartbeat Active

### 2.2 Rejection State
If any above condition evaluates to false:
- The Safety Modal displays a red `✗` next to the failing check.
- The ARM button is strictly disabled.
- The UI forces the user to resolve the physical hardware issue.

### 2.3 Deliberate Execution
If all checks pass:
- A `HOLD TO ARM` button renders.
- The user must press and hold the button for exactly 1.0 second (`onTouchStart` + `setInterval`).
- Releasing early (`onTouchEnd`) instantly aborts the progress bar.
- Upon completion, state locks to `armState = "SENDING"` and dispatches the WebSocket `CommandAction.ARM`.

## 3. TAKEOFF Flow

### 3.1 Pre-Flight Checks
Takeoff evaluates the same 7 ARM conditions + 1 additional requirement:
8. **Drone Armed State** must explicitly report `ARMED` via live incoming telemetry.

### 3.2 Slider Confirmation
The Takeoff modal lists the explicitly selected **Target Altitude** (derived from the slider input) and compares it to the **Current Altitude**.

### 3.3 Deliberate Execution
Identical press-and-hold execution flow as ARM, shifting state to `takeoffState = "SENDING"` to prevent double-tap launches.
