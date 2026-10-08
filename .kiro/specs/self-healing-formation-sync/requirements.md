# Requirements Document

## Introduction

The self-healing formation sync feature closes three gaps that currently prevent a 4-drone DroneOS swarm
from automatically recovering when a member drone fails:

1. The `coordination/` subtree from the `DroneOS` source instance has never been copied into the `DroneOS1`,
   `DroneOS2`, and `DroneOS3` instances, so those nodes cannot participate in healing.
2. All imports inside the coordination modules hardcode the `DroneOS.` namespace, which is wrong for
   instances 1–3.
3. There is no AirSim integration test that exercises the full detect → plan → send → apply cycle under
   simulation conditions.

These requirements capture the verifiable behaviour needed to close each gap: module sync, configuration,
`main.py` wiring, the AirSim integration test, a sync script to prevent future drift, and the six
correctness invariants that govern the healing logic itself.

---

## Glossary

- **CoordinationManager**: The async polling component in `coordination/manager.py` that drives the full
  heal lifecycle — membership ticking, quorum check, anchor election, plan request, gating, and send.
- **HealPlan**: The data structure produced by `plan_healing()` describing the new slot assignments for all
  surviving drones.
- **MembershipView**: The component in `coordination/membership.py` that tracks the liveness state
  (`ALIVE` / `SUSPECT` / `DEAD`) of every peer drone.
- **Sync_Script**: The PowerShell/Bash script at `scripts/sync_coordination.ps1` (and `.sh`) that propagates
  the coordination subtree from `DroneOS` into `DroneOS1`, `DroneOS2`, and `DroneOS3` with namespace
  substitution.
- **AirSim_Test**: The integration test harness at `tests/test_heal_airsim.py` that verifies the full
  heal cycle against a running AirSim simulation.
- **Formation_Universe**: The set of all drone IDs listed in `slot_assignments` at the time a heal decision
  is made.
- **Proposed_Anchor**: The drone elected to compute and broadcast the `FORMATION_UPDATE` message; always
  the current slot-0 drone if healthy, otherwise the lexicographically-first healthy drone.
- **FORMATION_UPDATE**: A `ControlMessage` broadcast by the anchor drone that carries new `slot_assignments`
  to all surviving peers.
- **min_formation_separation_m**: The minimum allowable pairwise distance (in metres) between any two drone
  positions, as specified in `FlightConfig.formation`.
- **reslot_cooldown_s**: The mandatory quiet period after a slot change before a new heal may be initiated.
- **rejoin_stable_s**: The duration of uninterrupted heartbeats required before a previously dead drone is
  promoted back to `ALIVE`.
- **max_heals_per_session**: The maximum number of `FORMATION_UPDATE` sends permitted across the lifetime
  of a single `CoordinationManager` instance.

---

## Requirements

### Requirement 1: Coordination Module Sync

**User Story:** As a swarm operator, I want the coordination subtree copied into every DroneOS instance with
correct namespace references, so that all four drones can participate in self-healing.

#### Acceptance Criteria

1. THE Sync_Script SHALL copy every `.py` file (including `__init__.py`) from `DroneOS/core/coordination/`
   and all of its subdirectories into the corresponding paths under `DroneOS1/core/coordination/`,
   `DroneOS2/core/coordination/`, and `DroneOS3/core/coordination/`, preserving the original subdirectory
   structure and overwriting any existing destination files.
2. WHEN a coordination module is copied into a target instance, THE Sync_Script SHALL replace every
   occurrence of the string `"from DroneOS."` with `"from DroneOS{N}."` (where N is the target instance
   number) in the copied file.
3. WHEN a coordination module is copied into a target instance, THE Sync_Script SHALL replace every
   occurrence of the string `"import DroneOS."` with `"import DroneOS{N}."` in the copied file.
4. WHEN the source files have not changed in content since the previous run, THE Sync_Script SHALL produce
   output files byte-for-byte identical to the files from the first run.
5. THE Sync_Script SHALL accept a `-DryRun` flag that reports all planned file operations to stdout in the
   format `[DRY RUN] <operation>: <source_path> -> <destination_path>` without writing, modifying, or
   deleting any file.
6. WHEN the `-DryRun` flag is supplied, THE Sync_Script SHALL leave every target file and every target
   directory identical to their pre-invocation state.
7. IF the source directory (`DroneOS/core/coordination/`) is missing or unreadable, THEN THE Sync_Script
   SHALL exit with a non-zero status code and print a descriptive error message to stderr before attempting
   any file writes.
8. WHEN a target instance directory does not exist, THE Sync_Script SHALL create it (including any
   intermediate directories) before copying files into it.

---

### Requirement 2: Per-Instance Test Configuration

**User Story:** As a developer, I want each DroneOS instance to have a test configuration file with
coordination fully enabled, so that integration tests can exercise the active healing path without
modifying the production config.

#### Acceptance Criteria

1. THE Sync_Script SHALL create or update `configs/flight.test.yaml` in each of `DroneOS`,
   `DroneOS1`, `DroneOS2`, and `DroneOS3`; if the `configs/` directory does not exist in a target
   instance, THE Sync_Script SHALL create it before writing the configuration file.
2. THE test configuration SHALL set `coordination.enabled` to `true` in every instance.
3. THE test configuration SHALL set `coordination.mode` to `"active"` in every instance.
4. THE test configuration SHALL set `coordination.armed` to `true` in every instance.
5. THE test configuration SHALL set `coordination.suspect_missed_beats` to `2`,
   `coordination.dead_missed_beats` to `4`, and `coordination.heartbeat_interval` to `1.0` so that
   failure-detection timing calculations are unambiguous in tests.
6. THE test configuration SHALL set `coordination.reslot_cooldown_s` to `5` and
   `coordination.rejoin_stable_s` to `5` to keep integration test durations manageable.
7. WHILE `coordination.enabled` is `false`, THE `CoordinationManager.run()` method SHALL return
   immediately without entering the poll loop, sending zero `FORMATION_UPDATE` messages regardless
   of swarm membership changes.

---

### Requirement 3: Coordination Wiring in main.py

**User Story:** As a developer, I want the coordination components wired into `main.py` of every instance
with the correct package namespace, so that each drone process starts with a functional
`CoordinationManager`.

#### Acceptance Criteria

1. THE Sync_Script SHALL insert or update the coordination wiring block in `DroneOS1/main.py`,
   `DroneOS2/main.py`, and `DroneOS3/main.py`, using the `DroneOS{N}.*` namespace in all import
   statements within that block.
2. THE coordination wiring block SHALL instantiate `CoordinationManager` with a `formation_provider`
   callable, a `self_status_provider` callable, and a `formation_update_sender` callable.
3. IF the coordination import raises an `ImportError` at startup, THEN THE DroneOSApp SHALL set
   `self.coordination_manager` to `None` and continue starting without coordination.
4. WHILE `coordination.enabled` is `true` AND `coordination.mode` is `"active"` AND
   `coordination.armed` is `true`, THE CoordinationManager SHALL begin its polling loop when
   `DroneOSApp` starts.

---

### Requirement 4: AirSim Integration Test — Drone Failure and Auto-Reformat

**User Story:** As a QA engineer, I want an automated end-to-end test that kills one drone in AirSim and
confirms the remaining three automatically reformat, so that I can verify the full heal cycle runs
correctly under real simulation conditions.

#### Acceptance Criteria

1. THE AirSim_Test SHALL connect to a running AirSim instance with four vehicles named `Drone1`,
   `Drone2`, `Drone3`, and `Drone4`, each bound to a separate DroneOS instance.
2. THE AirSim_Test SHALL establish a V-formation with `spacing = 15.0 m` and
   `slot_assignments = {drone1:0, drone2:1, drone3:2, drone4:3}` before the failure is induced.
3. WHEN the V-formation is commanded, THE AirSim_Test SHALL wait until all four drones are within
   `3.0 m` of their target slot positions before proceeding, with a timeout of `30 s`; IF the
   30 s convergence timeout expires, THE AirSim_Test SHALL fail with a descriptive assertion
   message indicating which drone(s) had not converged.
4. WHEN `Drone4` is terminated (motors cut and DroneOS3 process stopped), THE CoordinationManager
   on the anchor drone SHALL broadcast a `FORMATION_UPDATE` to the surviving drones within
   `20 s` of the termination.
5. WITHIN 60 s of the termination of `Drone4`, THE surviving drones SHALL converge to
   `slot_assignments = {drone1:0, drone2:1, drone3:2}` within `4.0 m` of their new target
   positions (allowing up to 30 s after the heal broadcast for convergence).
6. WHEN the heal completes, THE anchor drone (`drone1`) SHALL remain within `2.0 m` of its
   position recorded immediately before the termination command was issued.
7. WHEN the heal completes, THE pairwise distance between any two surviving drones SHALL be at
   least the value of `min_formation_separation_m` read from `FlightConfig` at test setup.
8. WHEN the `AIRSIM_HOST` environment variable is not set, THE AirSim_Test SHALL be skipped
   without failing the test suite.
9. WHEN the AirSim connection attempt fails (host unreachable or connection refused), THE
   AirSim_Test SHALL raise a pytest skip exception with a message indicating that the AirSim
   host was unreachable.

---

### Requirement 5: Sync Script Operational Quality

**User Story:** As a developer, I want the sync script to be reliable and self-documenting, so that I can
safely re-run it after any change to the source coordination modules without worrying about
accidental data loss or divergence.

#### Acceptance Criteria

1. THE Sync_Script SHALL be provided in both PowerShell (`scripts/sync_coordination.ps1`) and
   Bash (`scripts/sync_coordination.sh`) to support Windows and Linux/macOS environments.
2. THE Sync_Script SHALL accept `-Source` and `-Targets` parameters to allow selective sync of
   individual instances.
3. WHEN the Sync_Script completes successfully, THE Sync_Script SHALL print a summary listing
   each file written and each file skipped (already up to date).
4. IF a target instance directory does not exist, THEN THE Sync_Script SHALL exit with a non-zero
   status code and report the missing directory before attempting any file writes.
5. WHEN all target instances already have up-to-date coordination files, THE Sync_Script SHALL
   report that no changes are needed and exit with status code `0`.

---

### Requirement 6: Quorum Invariant

**User Story:** As a safety engineer, I want the quorum check to enforce a strict majority rule before any
healing action fires, so that isolated drones never unilaterally rewrite formation assignments.

#### Acceptance Criteria

1. WHEN `compute_quorum()` is called with a `slot_assignments` dict and a membership view where
   the alive drone count (counting the own node as always alive) strictly exceeds
   `len(Formation_Universe) / 2`, THE CoordinationManager SHALL receive `QuorumState.QUORUM`.
2. IF the alive drone count (counting the own node as always alive) is less than or equal to
   `len(Formation_Universe) / 2`, THEN `compute_quorum()` SHALL return `QuorumState.ISOLATED`.
3. IF the local node's own ID is not present in `slot_assignments`, THEN `compute_quorum()`
   SHALL return `QuorumState.IDLE`.
4. WHERE `quorum_required` is `false`, `compute_quorum()` SHALL always return
   `QuorumState.QUORUM` regardless of alive count.
5. THE own node SHALL always count as alive in the quorum calculation.

---

### Requirement 7: HealPlan Slot Bijection

**User Story:** As a safety engineer, I want every accepted heal plan to assign exactly one unique slot to
each surviving drone, so that no two drones ever fly the same position.

#### Acceptance Criteria

1. WHEN `plan_healing()` returns an accepted `HealPlan`, THE `HealPlan` SHALL contain a
   `slot_assignments` dict whose keys are exactly the set of IDs passed as the `healthy_members`
   parameter.
2. WHEN `plan_healing()` returns an accepted `HealPlan`, THE `slot_assignments` values SHALL be
   exactly the integers `{0, 1, …, n−1}` where `n` is the number of surviving drones.
3. WHEN `plan_healing()` returns an accepted `HealPlan`, THE `slot_assignments` SHALL contain no
   duplicate values.
4. WHEN `plan_healing()` returns an accepted `HealPlan`, no drone ID from the dead set SHALL
   appear as a key in `slot_assignments`.
5. IF the `healthy_members` and `dead_members` parameters passed to `plan_healing()` contain one
   or more overlapping IDs, THEN `plan_healing()` SHALL raise a `ValueError`.

---

### Requirement 8: Anchor Slot Invariant

**User Story:** As a pilot, I want the elected anchor drone to always hold slot 0 after a heal, so that
the formation geometry is always anchored to a stable reference point.

#### Acceptance Criteria

1. WHEN `plan_healing()` returns an accepted `HealPlan`, THE `HealPlan` SHALL assign
   `slot_assignments[Proposed_Anchor] == 0`.
2. WHEN the current slot-0 drone is present in the `healthy_members` parameter, THE
   `plan_healing()` SHALL treat that drone as the `Proposed_Anchor`.
3. IF the current slot-0 drone is present in the dead set (i.e., not in `healthy_members`), THEN
   `plan_healing()` SHALL treat the lexicographically-first ID in `healthy_members` as the
   `Proposed_Anchor`.
4. IF `healthy_members` is empty, THEN `plan_healing()` SHALL return a `HealPlan` with
   `accepted = false` and `reject_reason = "no healthy members"`.

---

### Requirement 9: Separation Safety Invariant

**User Story:** As a safety engineer, I want every accepted heal plan to guarantee that no two drones will
come within the minimum safe separation distance during or after the formation transition, so that
the heal never creates a collision risk.

#### Acceptance Criteria

1. WHEN `plan_healing()` returns an accepted `HealPlan`, THE recorded `plan.min_separation` SHALL
   be greater than or equal to `flight_cfg.formation.min_formation_separation_m`, measured as the
   minimum closest-approach distance along full move-path segments for all pairs of drones.
2. WHEN the minimum pairwise path separation would be less than `min_formation_separation_m`, THE
   `plan_healing()` SHALL return a `HealPlan` with `accepted = false`, a non-empty `reject_reason`,
   and `reject_kind = FINAL`.
3. WHEN a move path crosses within the dead-drone obstacle radius of a known dead drone position,
   THE `plan_healing()` SHALL return a `HealPlan` with `accepted = false` and
   `reject_kind = TRANSIENT`; the dead-drone obstacle radius is the value of
   `flight_cfg.formation.dead_obstacle_radius_m`, or `3.0 m` if that field is not configured.

---

### Requirement 10: Session Heal Cap

**User Story:** As a safety engineer, I want the coordination manager to stop sending formation updates
after a configurable maximum, so that a software bug cannot cause unlimited formation churn during
a single flight session.

#### Acceptance Criteria

1. THE CoordinationManager SHALL maintain an internal counter `_heal_count` that increments by
   one for each `FORMATION_UPDATE` for which `formation_update_sender` returned `True` (i.e.,
   successfully sent); the cap check SHALL occur before the send attempt, and `_heal_count`
   SHALL be incremented only after `formation_update_sender` returns `True`.
2. WHEN `_heal_count` reaches `max_heals_per_session`, THE CoordinationManager SHALL set
   `_heals_disabled` to `true` and send no further `FORMATION_UPDATE` messages for the lifetime
   of that process instance.
3. THE `_heal_count` SHALL never exceed `max_heals_per_session` for any sequence of events.
4. IF `_heals_disabled` is `true`, THEN THE CoordinationManager SHALL continue its poll loop
   (monitoring quorum and membership) but SHALL NOT compute or send any new `HealPlan`.

---

### Requirement 11: Rejoin Stabilization

**User Story:** As a pilot, I want a previously dead drone that resumes heartbeats to remain in the dead
state until its heartbeats have been stable long enough, so that transient network issues do not
cause the swarm to perform unnecessary formation changes.

#### Acceptance Criteria

1. WHEN a drone marked `DEAD` resumes sending heartbeats, THE MembershipView SHALL record the
   timestamp of the first resumed heartbeat as the `rejoin_stable_start` time for that drone.
2. WHILE the time elapsed since `rejoin_stable_start` is less than `rejoin_stable_s`, THE
   MembershipView SHALL keep the drone in the `DEAD` state even when heartbeats are being
   received.
3. WHEN the time elapsed since `rejoin_stable_start` reaches `rejoin_stable_s` AND no heartbeat
   has been missed during that window, THE MembershipView SHALL promote the drone's state to
   `ALIVE`; a "missed heartbeat" is defined as no heartbeat received within one `hb_interval`
   window.
4. IF a heartbeat is missed during the rejoin stabilization window (no heartbeat received within
   one `hb_interval`), THE MembershipView SHALL reset `rejoin_stable_start` to the time of the
   next received heartbeat, and the drone SHALL remain in the `DEAD` state until a full
   `rejoin_stable_s` window of uninterrupted heartbeats elapses from that reset timestamp.
