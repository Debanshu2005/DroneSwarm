# DroneOS Shutdown Manual Verification

Run this procedure for `DroneOS`, `DroneOS1`, and `DroneOS2`.

1. Start the node disarmed. Press Ctrl+C once. Expected: the node exits through the normal shutdown sequence without sending a flight-stop command.
2. Start the node armed in the indoor profile. Press Ctrl+C once. Expected: the node logs the armed shutdown request, calls the configured connection-lost failsafe, and lands before shutdown.
3. Start the node armed in the outdoor profile with a valid home position. Press Ctrl+C once. Expected: the node calls RTL before shutdown.
4. Start the node armed in the outdoor profile without a valid home position. Press Ctrl+C once. Expected: the node lands before shutdown.
5. Press Ctrl+C twice within three seconds. Expected: the second request logs a hard-abort request and skips the graceful safety wait.
