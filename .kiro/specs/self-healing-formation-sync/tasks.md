# Implementation Plan: Self-Healing Formation Sync

## Overview

Implementation proceeds in six logical phases:

1. **Merge stage-b** — bring the completed `DroneOS/core/coordination/` modules, `DroneOS/main.py` wiring, and existing unit tests from the `stage-b` branch into `main`
2. **FlightConfig extension** — add the `coordination` dict field to all four `shared/config/models.py` files
3. **Sync tooling** — write the PowerShell and Bash sync scripts and execute them to populate `DroneOS1/2/3/core/coordination/`
4. **main.py verification / wiring** — verify `DroneOS/main.py` wiring (already done on `stage-b`), then add equivalent wiring to `DroneOS1/2/3/main.py`
5. **Test configuration files** — create `configs/flight.test.yaml` in all four instances
6. **Tests** — property-based tests for the six correctness invariants, plus the AirSim integration test

Each phase builds directly on the previous one; no orphaned code exists between steps.

---

## Tasks

- [ ] 1. Switch to `stage-b` and verify existing coordination modules
  - [ ] 1.1 Check out the `stage-b` branch
    - Run `git checkout stage-b` from `d:\CityGrid\my-project\PhoneOS_Swarm`
    - All subsequent tasks execute on `stage-b`. Do NOT merge to `main` — the branch stays isolated until fully verified and tested.
    - _Requirements: 1.1, 3.2, 3.3, 3.4_

  - [ ] 1.2 Verify all existing coordination tests pass on `stage-b`
    - Run `python -m pytest DroneOS/tests/test_coordination.py DroneOS/tests/test_healing.py DroneOS/tests/test_manager.py DroneOS/tests/test_stage_b.py -x -q` from the workspace root with `PYTHONPATH=d:\CityGrid\my-project\PhoneOS_Swarm`
    - Fix any import errors or test failures before proceeding
    - _Requirements: 1.1, 6.1–6.5, 7.1–7.5, 8.1–8.4, 9.1–9.3, 11.1–11.4_

- [ ] 2. Add `coordination` field to `FlightConfig` in all four instances
  - [ ] 2.1 Modify `DroneOS/shared/config/models.py`
    - Add `from typing import Dict, Any` import if not present
    - Add `coordination: Optional[Dict[str, Any]] = None` field to `FlightConfig` class after the existing optional fields
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ] 2.2 Modify `DroneOS1/shared/config/models.py`, `DroneOS2/shared/config/models.py`, `DroneOS3/shared/config/models.py`
    - Apply the identical `coordination: Optional[Dict[str, Any]] = None` addition to all three files
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ] 3. Checkpoint — ensure coordination modules import cleanly after merge
  - Run the following in PowerShell from the workspace root:
    ```powershell
    $env:PYTHONPATH = "d:\CityGrid\my-project\PhoneOS_Swarm"
    python -c "from DroneOS.core.coordination import quorum, membership, anchor, healing, manager; print('OK')"
    ```
  - Fix any import errors before continuing

- [ ] 4. Write and run the sync scripts
  - [ ] 4.1 Create `scripts/sync_coordination.ps1`
    - Accept `-DryRun` switch, `-Source` (default `DroneOS`) and `-Targets` (default `DroneOS1,DroneOS2,DroneOS3`) parameters
    - For each target: exit non-zero with stderr message if source `core/coordination/` is missing (Req 1.7); create target `core/coordination/` including intermediate dirs if needed (Req 1.8); for each `.py` file in source recursively, replace `"from DroneOS."` → `"from DroneOS{N}."` and `"import DroneOS."` → `"import DroneOS{N}."` then write to target (Req 1.1, 1.2, 1.3, 1.4)
    - In `-DryRun` mode print `[DRY RUN] <operation>: <source_path> -> <dest_path>` for every file without writing (Req 1.5, 1.6)
    - After copying files, insert/replace the coordination wiring block in each target `main.py` using the `DroneOS{N}.*` namespace (Req 3.1)
    - Create or overwrite `configs/flight.test.yaml` in each target (Req 2.1); create `configs/` dir if absent (Req 2.1)
    - On success print per-file summary: files written vs skipped (Req 5.3); exit 0 (Req 5.5)
    - _Requirements: 1.1–1.8, 2.1, 3.1, 5.1, 5.2, 5.3, 5.5_

  - [ ] 4.2 Create `scripts/sync_coordination.sh`
    - Bash equivalent of `sync_coordination.ps1` supporting `--dry-run`, `--source`, `--targets` flags
    - Same logic: namespace substitution via `sed`, main.py wiring insertion, test config creation, summary output
    - _Requirements: 5.1, 5.2_

  - [ ] 4.3 Execute `scripts/sync_coordination.ps1` to populate `DroneOS1/2/3/core/coordination/`
    - Run `powershell -ExecutionPolicy Bypass -File scripts/sync_coordination.ps1` from the workspace root
    - Verify that `DroneOS1/core/coordination/`, `DroneOS2/core/coordination/`, `DroneOS3/core/coordination/` each contain `__init__.py`, `quorum.py`, `membership.py`, `anchor.py`, `healing.py`, `manager.py`
    - Verify no file in those directories contains the string `"from DroneOS."` (only `"from DroneOS1."` etc.)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [ ] 5. Write `configs/flight.test.yaml` for `DroneOS` (source instance)
  - Create `DroneOS/configs/flight.test.yaml` with `coordination.enabled: true`, `coordination.mode: "active"`, `coordination.armed: true`, `coordination.suspect_missed_beats: 2`, `coordination.dead_missed_beats: 4`, `coordination.heartbeat_interval: 1.0`, `coordination.reslot_cooldown_s: 5`, `coordination.rejoin_stable_s: 5`; all other fields inherited from `flight.sim.yaml`
  - The sync script (task 4.3) will propagate this file to DroneOS1/2/3; this task creates the source copy
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ] 6. Verify `DroneOS/main.py` coordination wiring (already done on `stage-b`)
  - [ ] 6.1 Confirm `create_formation_update_sender` exists in `DroneOS/main.py` after the merge
    - Check that the closure is present, that it calls `CommandHandler.handle_command()` first, unicasts `ControlMessage{FORMATION_UPDATE}` to each target, returns `True` iff all succeed, and returns `False` without re-raising on any exception
    - _Requirements: 3.2, 3.3_

  - [ ] 6.2 Confirm `CoordinationManager` is instantiated and dispatched in `DroneOSApp.__init__` / `run()`
    - Check that `formation_provider`, `self_status_provider`, and `CoordinationManager` are constructed after existing component construction
    - Check that `self.coordination_manager.run()` is launched as an asyncio task in `DroneOSApp.run()` when `coordination.enabled`, `mode == "active"`, and `armed == True`
    - Check that the entire block is wrapped in a try/except `ImportError` that sets `self.coordination_manager = None` on failure
    - _Requirements: 3.2, 3.3, 3.4_

- [ ] 7. Add coordination wiring to `DroneOS1/main.py`, `DroneOS2/main.py`, `DroneOS3/main.py`
  - [ ] 7.1 Modify `DroneOS1/main.py`
    - Mirror the wiring block from the verified `DroneOS/main.py` (task 6.1–6.2) using `DroneOS1.*` imports throughout
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 7.2 Modify `DroneOS2/main.py`
    - Mirror the wiring block using `DroneOS2.*` imports throughout
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ] 7.3 Modify `DroneOS3/main.py`
    - Mirror the wiring block using `DroneOS3.*` imports throughout
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 8. Checkpoint — end-to-end smoke import for all four instances
  - Run the following in PowerShell from the workspace root:
    ```powershell
    $env:PYTHONPATH = "d:\CityGrid\my-project\PhoneOS_Swarm"
    python -c "from DroneOS.core.coordination.manager import CoordinationManager; from DroneOS1.core.coordination.manager import CoordinationManager as CM1; from DroneOS2.core.coordination.manager import CoordinationManager as CM2; from DroneOS3.core.coordination.manager import CoordinationManager as CM3; print('all OK')"
    ```
  - Fix any remaining namespace errors before continuing

- [ ] 9. Write property-based tests in `DroneOS/tests/test_coordination_properties.py`
  - [ ] 9.1 Write property test for Property 1: Quorum Invariant
    - Use `hypothesis` `@given` with `formation_slot_dicts()` strategy and `st.frozensets(st.text())`
    - Assert: `alive_count > len(universe)/2 ⟺ QuorumState.QUORUM` for all valid inputs
    - **Property 1: Quorum Invariant**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**

  - [ ] 9.2 Write property test for Property 2: Slot Bijection
    - Use `hypothesis` `@given` with `healthy` and `dead` lists; `assume(set(healthy).isdisjoint(set(dead)))`
    - Assert: if `plan.accepted` then `set(plan.slot_assignments.keys()) == set(healthy)` and `set(plan.slot_assignments.values()) == set(range(len(healthy)))` and no duplicate values
    - **Property 2: Slot Bijection**
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [ ] 9.3 Write property test for Property 3: Anchor Invariant
    - Derive `proposed_anchor` from formation params (slot-0 if in healthy, else lex-first)
    - Assert: if `plan.accepted` then `plan.slot_assignments[proposed_anchor] == 0`
    - **Property 3: Anchor Invariant**
    - **Validates: Requirements 8.1, 8.2, 8.3**

  - [ ] 9.4 Write property test for Property 4: Separation Invariant
    - Use `valid_flight_cfgs()` strategy that generates configs with varying `min_formation_separation_m`
    - Assert: if `plan.accepted` then `plan.min_separation >= flight_cfg.formation.min_formation_separation_m`
    - **Property 4: Separation Invariant**
    - **Validates: Requirements 9.1, 9.2, 9.3**

  - [ ] 9.5 Write property test for Property 5: Session Cap
    - Construct a `CoordinationManager` with a mock `formation_update_sender` that always returns `True`
    - Inject up to 20 heal-trigger events; assert `manager._heal_count <= max_heals_per_session` at every step; assert `manager._heals_disabled == True` once cap is reached
    - **Property 5: Session Cap**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

  - [ ] 9.6 Write property test for Property 6: No Dead Drone in Healed Slots
    - Same `@given` harness as 9.2; `assume(set(healthy).isdisjoint(set(dead)))`
    - Assert: if `plan.accepted` then `set(plan.slot_assignments.keys()) & set(dead) == set()`
    - **Property 6: No Dead Drone in Healed Slots**
    - **Validates: Requirements 7.4**

- [ ] 10. Write AirSim integration test `tests/test_heal_airsim.py`
  - [ ] 10.1 Create `tests/airsim_settings_4drone.json`
    - Write AirSim `settings.json` defining 4 vehicles (`Drone1`–`Drone4`), each on a distinct RPC port (41451–41454), arranged in a loose V-grid so initial takeoff has sufficient separation
    - _Requirements: 4.1_

  - [ ] 10.2 Implement test setup helpers in `tests/test_heal_airsim.py`
    - `@pytest.fixture` that skips if `AIRSIM_HOST` env var is not set (Req 4.8); attempts `airsim.MultirotorClient().confirmConnection()` and raises `pytest.skip` if connection refused (Req 4.9)
    - `send_formation_command(client, type, spacing, slot_assignments)` helper that sends the formation command over the DroneOS command channel
    - `wait_formation_converged(client, slot_assignments, target_positions, tolerance_m, timeout_s)` polling helper with 100 ms intervals; raises `AssertionError` with per-drone diagnostics on timeout
    - `get_ned_position(client, vehicle_name)` returning `(north, east, down)` from AirSim state
    - _Requirements: 4.1, 4.2, 4.3, 4.8, 4.9_

  - [ ] 10.3 Implement `test_heal_on_drone4_failure` in `tests/test_heal_airsim.py`
    - Mark `@pytest.mark.integration` and `@pytest.mark.asyncio`
    - Phase 1 — formation setup: arm + takeoff all 4 drones; command V-formation with `spacing=15.0`, `slot_assignments={drone1:0,drone2:1,drone3:2,drone4:3}`; wait convergence within `3.0 m / 30 s` (Req 4.2, 4.3)
    - Phase 2 — record pre-heal positions of Drone1, Drone2, Drone3
    - Phase 3 — kill Drone4: `client.armDisarm(False, "Drone4")` + terminate DroneOS3 process (Req 4.4)
    - Phase 4 — wait heal: poll for `FORMATION_UPDATE` broadcast within 20 s (Req 4.4); wait full convergence to `{drone1:0,drone2:1,drone3:2}` within `4.0 m` and 60 s total (Req 4.5)
    - Phase 5 — assertions: anchor displacement < 2.0 m (Req 4.6); all pairwise distances ≥ `min_formation_separation_m` (Req 4.7)
    - Phase 6 — cleanup: land and disarm Drone1–Drone3
    - _Requirements: 4.1–4.9_

- [ ] 11. Final checkpoint — run full unit test suite
  - Run `python -m pytest DroneOS/tests/ -x -q` and `python -m pytest DroneOS/tests/test_coordination_properties.py -v` to confirm all coordination property tests pass; fix any failures before closing the feature

---

## Notes

- **All work is done on the `stage-b` branch. Do NOT merge to `main` until all tasks are complete, all tests pass, and the AirSim integration test has been manually verified.**
- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Property tests (tasks 9.1–9.6) use `hypothesis` which is already listed in `DroneOS/requirements.txt`
- The AirSim integration test (task 10) requires a live AirSim environment; it is automatically skipped by pytest when `AIRSIM_HOST` is not set, so it does not block CI unit runs
- Task 1.1 merges the completed coordination modules from `stage-b`; tasks 4.1–4.2 create the sync scripts; task 4.3 **runs** them — the result of 4.3 is the populated `DroneOS1/2/3/core/coordination/` subtrees
- Tasks 7.1–7.3 (main.py wiring for the three replicas) can also be done via a second sync-script pass after task 4.3 completes

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "4.1", "4.2", "5"] },
    { "id": 3, "tasks": ["4.3"] },
    { "id": 4, "tasks": ["6.1", "6.2", "7.1", "7.2", "7.3"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3", "9.4", "9.5", "9.6", "10.1", "10.2"] },
    { "id": 6, "tasks": ["10.3"] }
  ]
}
```
