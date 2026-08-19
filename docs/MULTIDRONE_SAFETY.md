# PhoneOS Multi-Drone Safety

## PX4 Authority
The Android App NEVER bypasses PX4 safety checks. 
- It does not generate fake telemetry.
- It dynamically reads `telemetry.gps_valid`, `telemetry.battery_level`, etc.
- Arming and Takeoff are requested via `ControlMessage`. DroneOS and PX4 are the final authorities that accept or reject the command.

## Multi-Drone Safety Gates
Before a Group ARM or Group TAKEOFF command is dispatched, the UI iterates over the `selectedDrones` array and verifies:
1. PX4 Connection is active.
2. Telemetry link is healthy (age < 2000ms).
3. Battery is >= 20%.
4. GPS Fix is 3D.
5. Mode is valid.

If **ANY** selected drone fails these checks, the group command button is disabled and the operator is presented with the specific reason for rejection.

## Failsafe Isolation
- A failure (e.g., GPS loss) on Drone 2 will only trigger a Failsafe UI state on Drone 2's card.
- Drone 1 and Drone 3 remain fully controllable.
- If an Emergency Stop is triggered, it is ONLY dispatched to the drones currently selected in the UI. A massive red alert prevents accidental triggering by requiring a double-tap.
