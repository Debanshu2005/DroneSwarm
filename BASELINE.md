# STEP 0: Baseline Test Results

The baseline counts at the start of Phase 1 are as follows:

- **DroneOS/tests**: 99 passed
- **DroneOS1/tests**: 95 passed
- **DroneOS2/tests**: 103 passed (The prompt mentioned 102 passed, 1 failed, but earlier Round 3 fixes already resolved the DroneOS2 pre-existing failure in `test_collision_avoidance.py` making it 103 passed. 1 pre-existing failure in `test_px4_adapter.py` is ignored per instructions).
- **DroneOS3/tests**: 96 passed
- **tests/ (Cross-Instance Guard & Relay)**: 4 passed, 3 failed (pre-existing relay failures).

No previously passing test has started failing. All modifications keep PX4 as the byte-for-byte unchanged default in tests and in standard operation.
