import {
  mergeDetections,
  isValidDetectionItem,
  loadPersistedTombstones,
  savePersistedTombstones,
  loadPersistedGarbage,
  savePersistedGarbage,
  reconcileSnapshot,
  getConfirmedGarbage,
  getProvisionalGarbage,
  LifecycleState,
  DEFAULT_BENCHMARK_TARGETS,
  isDemoModeEnabled,
  getInitialBenchmarkTargets
} from './garbage.js';

function assert(condition, message) {
  if (!condition) {
    throw new Error(`Assertion failed: ${message}`);
  }
}

console.log("Running Garbage Marker Utility Tests...");

// 1. Validation tests
assert(!isValidDetectionItem(null), "null item should be invalid");
assert(!isValidDetectionItem({}), "empty item should be invalid");
assert(!isValidDetectionItem({ id: "g1", lat: NaN, lon: 87.0, confidence: 0.8 }), "NaN lat should be invalid");
assert(!isValidDetectionItem({ id: "g1", lat: 0, lon: 0, confidence: 0.8 }), "(0, 0) coordinates should be invalid");
assert(!isValidDetectionItem({ id: "g1", lat: 22.0, lon: 87.0, confidence: 1.5 }), "confidence > 1 should be invalid");
assert(!isValidDetectionItem({ id: "g1", lat: 22.0, lon: 87.0, confidence: -0.1 }), "negative confidence should be invalid");
assert(isValidDetectionItem({ id: "g1", lat: 22.314, lon: 87.310, confidence: 0.85 }), "valid item should pass");

// 2. New item addition
const now1 = 100000;
const item1 = { id: "drone1-g0001", label: "rubbish", confidence: 0.75, lat: 22.314000, lon: 87.310000, hits: 2 };
const state1 = mergeDetections({}, [item1], "drone1", now1);

assert(Object.keys(state1).length === 1, "Should have 1 item in state");
assert(state1["drone1-g0001"].confidence === 0.75, "Confidence should match");
assert(state1["drone1-g0001"].hits === 2, "Hits should match");
assert(state1["drone1-g0001"].reportingDrone === "drone1", "Reporting drone should match");
assert(state1["drone1-g0001"].seenBy.includes("drone1"), "seenBy should contain drone1");

// 3. Same ID update
const now2 = 105000;
const item1Update = { id: "drone1-g0001", label: "rubbish", confidence: 0.90, lat: 22.314005, lon: 87.310005, hits: 4 };
const state2 = mergeDetections(state1, [item1Update], "drone1", now2);

assert(Object.keys(state2).length === 1, "Should still have 1 item");
assert(state2["drone1-g0001"].confidence === 0.90, "Confidence should take max (0.90)");
assert(state2["drone1-g0001"].hits === 4, "Hits should take max (4)");
assert(state2["drone1-g0001"].lastSeen === now2, "lastSeen should update to now2");
assert(state2["drone1-g0001"].firstSeen === now1, "firstSeen should remain now1");

// 4. Multi-drone sighting within 2m (treated as same object)
const now3 = 110000;
// Offset by ~0.5m (approx 0.000005 deg lat is ~0.55m)
const itemFromDrone2 = { id: "drone2-g0001", label: "rubbish", confidence: 0.85, lat: 22.314004, lon: 87.310000, hits: 3 };
const state3 = mergeDetections(state2, [itemFromDrone2], "drone2", now3);

assert(Object.keys(state3).length === 1, "Should merge close sighting from drone2 into drone1-g0001");
const merged = state3["drone1-g0001"];
assert(merged.hits === 7, "Hits should be bumped by 3 (4 + 3 = 7)");
assert(merged.seenBy.includes("drone1") && merged.seenBy.includes("drone2"), "seenBy should include both drone1 and drone2");
assert(merged.lat === item1.lat && merged.lon === item1.lon, "Coordinates should NOT move when merged from other drone");

// 5. Far sighting (>2m) creates separate item
const itemFar = { id: "drone2-g0002", label: "rubbish", confidence: 0.65, lat: 22.315000, lon: 87.310000, hits: 2 };
const state4 = mergeDetections(state3, [itemFar], "drone2", now3);
assert(Object.keys(state4).length === 2, "Should create separate item for far sighting");
assert(state4["drone2-g0002"] !== undefined, "drone2-g0002 should exist");

// 6. Dismissed IDs respect
const dismissed = new Set(["drone1-g0001"]);
const state5 = mergeDetections(state4, [item1Update], "drone1", now3, dismissed);
// item1Update is dismissed, so should not update or revive if removed
const stateFromScratch = mergeDetections({}, [item1], "drone1", now1, dismissed);
assert(Object.keys(stateFromScratch).length === 0, "Dismissed item must not be added");

// 7. Max 500 entries bound
let hugeState = {};
for (let i = 0; i < 505; i++) {
  hugeState[`item-${i}`] = {
    id: `item-${i}`,
    lat: 22.0 + i * 0.001,
    lon: 87.0,
    confidence: 0.5,
    hits: 1,
    firstSeen: 1000 + i,
    lastSeen: 1000 + i, // oldest have lower lastSeen
  };
}
const boundedState = mergeDetections(hugeState, [], "drone1", 2000);
assert(Object.keys(boundedState).length === 500, "State must be capped at 500 entries");
assert(boundedState["item-0"] === undefined, "Oldest item-0 must have been dropped");
assert(boundedState["item-4"] === undefined, "Oldest item-4 must have been dropped");
assert(boundedState["item-504"] !== undefined, "Newest item-504 must be retained");

// 8. Lifecycle: RETIRED removes existing marker
const tombstones = {};
const activeItem = { id: "G-1001", label: "rubbish", confidence: 0.9, lat: 22.314, lon: 87.310, version: 1 };
const activeState = mergeDetections({}, [activeItem], "gfs", 1000, new Set(), false, tombstones);
assert(activeState["G-1001"] !== undefined, "G-1001 must be active initially");

const retireEvent = { id: "G-1001", status: "RETIRED", version: 2 };
const retiredState = mergeDetections(activeState, [retireEvent], "gfs", 2000, new Set(), false, tombstones);
assert(retiredState["G-1001"] === undefined, "G-1001 must be removed upon RETIRED");
assert(tombstones["G-1001"] !== undefined, "Tombstone must be recorded for G-1001");
assert(tombstones["G-1001"].version === 2, "Tombstone version must be 2");

// 9. Lifecycle: Duplicate retirement is idempotent
const duplicateRetire = { id: "G-1001", status: "RETIRED", version: 2 };
const dupState = mergeDetections(retiredState, [duplicateRetire], "gfs", 2100, new Set(), false, tombstones);
assert(dupState["G-1001"] === undefined, "G-1001 remains removed on duplicate retirement");
assert(tombstones["G-1001"].version === 2, "Tombstone version unchanged");

// 10. Lifecycle: Delayed older update cannot resurrect a retired object
const delayedUpdate = { id: "G-1001", label: "rubbish", confidence: 0.85, lat: 22.314, lon: 87.310, version: 1 };
const stateAfterDelayed = mergeDetections(dupState, [delayedUpdate], "drone1", 2200, new Set(), false, tombstones);
assert(stateAfterDelayed["G-1001"] === undefined, "Delayed older update must NOT resurrect retired G-1001");

// 11. Lifecycle: Retirement before creation is handled via tombstone
const earlyRetire = { id: "G-2002", status: "RETIRED", version: 3 };
const statePreRetire = mergeDetections({}, [earlyRetire], "gfs", 3000, new Set(), false, tombstones);
assert(statePreRetire["G-2002"] === undefined, "G-2002 not added on early retirement");
assert(tombstones["G-2002"] !== undefined, "Tombstone recorded for early retired G-2002");

// Later, out-of-order create arrives with older version 2
const lateCreate = { id: "G-2002", label: "rubbish", confidence: 0.8, lat: 22.315, lon: 87.311, version: 2 };
const stateAfterLateCreate = mergeDetections(statePreRetire, [lateCreate], "drone2", 3100, new Set(), false, tombstones);
assert(stateAfterLateCreate["G-2002"] === undefined, "Late creation with older version must NOT create object with active tombstone");

// 12. Lifecycle: Explicit Reactivation with strictly newer version succeeds
const reactivateEvent = { id: "G-1001", status: "REACTIVATED", label: "rubbish", confidence: 0.95, lat: 22.314, lon: 87.310, version: 3 };
const reactivatedState = mergeDetections(stateAfterLateCreate, [reactivateEvent], "gfs", 4000, new Set(), false, tombstones);
assert(reactivatedState["G-1001"] !== undefined, "Explicit REACTIVATED event with version 3 must resurrect G-1001");
assert(tombstones["G-1001"] === undefined, "Tombstone must be cleared upon explicit reactivation");

// 13. Lifecycle: Stale version for active object is rejected
const staleActiveUpdate = { id: "G-1001", label: "rubbish", confidence: 0.5, lat: 22.399, lon: 87.399, version: 2 }; // older than version 3
const stateAfterStale = mergeDetections(reactivatedState, [staleActiveUpdate], "drone1", 4100, new Set(), false, tombstones);
assert(stateAfterStale["G-1001"].confidence === 0.95, "Confidence must not be overwritten by stale version");
assert(stateAfterStale["G-1001"].lat === 22.314, "Coordinates must not be overwritten by stale version");

// 14. Issue 1: Complete GCS Restart Recovery — 14-Step Exact Sequence
console.log("Running Issue 1: 14-Step Exact Sequence Test...");
const durableStorage = {};

// Step 1: G1 ACTIVE, version 1
let stepState = {};
let stepTombstones = loadPersistedTombstones(durableStorage);
const step1_active_v1 = { id: "G-1", label: "rubbish", confidence: 0.88, lat: 22.314, lon: 87.310, version: 1, status: "ACTIVE" };
stepState = mergeDetections(stepState, [step1_active_v1], "drone1", 1000, new Set(), false, stepTombstones);
assert(stepState["G-1"] !== undefined, "Step 1: G1 must be ACTIVE");
assert(stepState["G-1"].version === 1, "Step 1: G1 version must be 1");

// Step 2: G1 RETIRED, version 2
const step2_retired_v2 = { id: "G-1", status: "RETIRED", version: 2 };
stepState = mergeDetections(stepState, [step2_retired_v2], "gfs", 2000, new Set(), false, stepTombstones);
assert(stepState["G-1"] === undefined, "Step 2: G1 must be removed upon RETIRED");
assert(stepTombstones["G-1"] !== undefined && stepTombstones["G-1"].version === 2, "Step 2: Tombstone recorded at V=2");

// Step 3: Persist retirement state
savePersistedGarbage(durableStorage, stepState);
savePersistedTombstones(durableStorage, stepTombstones);
assert(durableStorage["PhoneOS_Tombstones"] !== undefined, "Step 3: Tombstones must be persisted");

// Step 4: Restart GCS
stepState = loadPersistedGarbage(durableStorage);
stepTombstones = loadPersistedTombstones(durableStorage);
assert(stepState["G-1"] === undefined, "Step 4: G1 must not be active on restart");
assert(stepTombstones["G-1"] !== undefined && stepTombstones["G-1"].version === 2, "Step 4: Tombstone intact on restart");

// Step 5: Verify stale ACTIVE version 1 is rejected
const step5_stale_v1 = { id: "G-1", label: "rubbish", confidence: 0.90, lat: 22.314, lon: 87.310, version: 1, status: "ACTIVE" };
stepState = mergeDetections(stepState, [step5_stale_v1], "drone2", 3000, new Set(), false, stepTombstones);
assert(stepState["G-1"] === undefined, "Step 5: Stale ACTIVE V=1 must be rejected by tombstone V=2");

// Step 6: Receive REACTIVATED version 3
const step6_reactivated_v3 = { id: "G-1", label: "rubbish", confidence: 0.95, lat: 22.3141, lon: 87.3101, version: 3, status: "REACTIVATED" };
stepState = mergeDetections(stepState, [step6_reactivated_v3], "gfs", 4000, new Set(), false, stepTombstones);

// Step 7: Restore G1
assert(stepState["G-1"] !== undefined, "Step 7: G1 must be restored upon REACTIVATED V=3");
assert(stepState["G-1"].version === 3, "Step 7: G1 version must be 3");
assert(stepTombstones["G-1"] === undefined, "Step 7: Tombstone for G1 must be cleared");

// Step 8: Persist or reconstruct active state
savePersistedGarbage(durableStorage, stepState);
savePersistedTombstones(durableStorage, stepTombstones);

// Step 9: Restart GCS again
stepState = loadPersistedGarbage(durableStorage);
stepTombstones = loadPersistedTombstones(durableStorage);

// Step 10: Verify G1 still exists exactly once
assert(stepState["G-1"] !== undefined, "Step 10: G1 must still exist after second restart");
assert(Object.keys(stepState).filter(k => k === "G-1").length === 1, "Step 10: G1 exists exactly once");
assert(stepState["G-1"].version === 3, "Step 10: G1 version must be 3");

// Step 11: Deliver duplicate version 3
const step11_duplicate_v3 = { id: "G-1", label: "rubbish", confidence: 0.95, lat: 22.3141, lon: 87.3101, version: 3, status: "REACTIVATED" };
stepState = mergeDetections(stepState, [step11_duplicate_v3], "gfs", 5000, new Set(), false, stepTombstones);

// Step 12: Verify no duplicate marker
assert(Object.keys(stepState).filter(k => k === "G-1").length === 1, "Step 12: No duplicate marker created");
assert(stepState["G-1"].version === 3, "Step 12: Marker version remains 3");

// Step 13: Deliver stale version 2
const step13_stale_v2 = { id: "G-1", label: "rubbish", confidence: 0.50, lat: 22.399, lon: 87.399, version: 2, status: "ACTIVE" };
stepState = mergeDetections(stepState, [step13_stale_v2], "drone1", 6000, new Set(), false, stepTombstones);

// Step 14: Verify G1 remains active
assert(stepState["G-1"] !== undefined, "Step 14: G1 remains active");
assert(stepState["G-1"].version === 3, "Step 14: G1 version remains 3 (stale V=2 ignored)");
assert(stepState["G-1"].confidence === 0.95, "Step 14: G1 confidence unmodified by stale V=2");
assert(stepState["G-1"].lat === 22.3141, "Step 14: G1 coordinates unmodified by stale V=2");

console.log("Issue 1: 14-Step Exact Sequence Test Passed 100%!");

// 15. Storage Resilience & Fallback Tests
console.log("Running Storage Resilience Tests...");

// Case A: Storage initially null or missing
const nullState = loadPersistedGarbage(null);
const nullTomb = loadPersistedTombstones(null);
assert(typeof nullState === 'object' && Object.keys(nullState).length === 0, "Null storage returns empty state");
assert(typeof nullTomb === 'object' && Object.keys(nullTomb).length === 0, "Null storage returns empty tombstones");
savePersistedGarbage(null, { test: 1 }); // Should not throw
savePersistedTombstones(null, { test: 1 }); // Should not throw

// Case B: Storage contains corrupted JSON
const corruptStorage = {
  "PhoneOS_Garbage": "INVALID_JSON{broken",
  "PhoneOS_Tombstones": "CORRUPT_JSON[broken"
};
const recoveredGarbage = loadPersistedGarbage(corruptStorage);
const recoveredTomb = loadPersistedTombstones(corruptStorage);
assert(typeof recoveredGarbage === 'object' && Object.keys(recoveredGarbage).length === 0, "Corrupted JSON gracefully returns empty garbage");
assert(typeof recoveredTomb === 'object' && Object.keys(recoveredTomb).length === 0, "Corrupted JSON gracefully returns empty tombstones");

// Case C: Storage write failure (QuotaExceededError or private browsing)
const throwingStorage = {
  setItem: () => {
    const err = new Error("QuotaExceededError");
    err.name = "QuotaExceededError";
    throw err;
  },
  getItem: () => null
};
savePersistedGarbage(throwingStorage, { "G-1": { id: "G-1" } }); // Must not throw
savePersistedTombstones(throwingStorage, { "G-1": { version: 2 } }); // Must not throw

// Benchmark Targets Demo Gating Tests
console.log("Running Benchmark Target Demo Gating Tests...");
const realSessionGarbage = loadPersistedGarbage(null, { enableDemo: false });
assert(Object.keys(realSessionGarbage).length === 0, "Real session must NEVER seed default benchmark targets");

const demoSessionGarbage = loadPersistedGarbage(null, { enableDemo: true });
assert(Object.keys(demoSessionGarbage).length === 2, "Demo session seeds default benchmark targets");
assert(demoSessionGarbage['G-0ab2d222'].isDemo === true, "Demo target G-0ab2d222 must be labeled isDemo: true");
assert(demoSessionGarbage['G-0ab2d222'].synthetic === true, "Demo target G-0ab2d222 must be labeled synthetic: true");
assert(demoSessionGarbage['G-0ab2d222'].label.includes('demo'), "Demo target label must indicate demo");
assert(demoSessionGarbage['G-467e474f'].isDemo === true, "Demo target G-467e474f must be labeled isDemo: true");
assert(demoSessionGarbage['G-467e474f'].synthetic === true, "Demo target G-467e474f must be labeled synthetic: true");
assert(demoSessionGarbage['G-467e474f'].label.includes('demo'), "Demo target label must indicate demo");

assert(isDemoModeEnabled({ enableDemo: false }) === false, "isDemoModeEnabled respects explicit false");
assert(isDemoModeEnabled({ enableDemo: true }) === true, "isDemoModeEnabled respects explicit true");
assert(Object.keys(getInitialBenchmarkTargets({ enableDemo: false })).length === 0, "getInitialBenchmarkTargets returns empty when demo disabled");
assert(Object.keys(getInitialBenchmarkTargets({ enableDemo: true })).length === 2, "getInitialBenchmarkTargets returns 2 when demo enabled");

// Case D: Contract B Authoritative GFS Snapshot Recovery and Reconciliation
console.log("Running Contract B Snapshot Reconciliation Tests...");
const localTombstones = { "G-RETIRED": { retiredAt: 1000, version: 4 } };
const initialLocalGarbage = {
  "G-ALREADY-NEWER": { id: "G-ALREADY-NEWER", version: 5, lat: 22.1, lon: 87.1, confidence: 0.9 }
};

const gfsSnapshot = [
  // 1. Snapshot has G-RETIRED with older V=3 -> Should be DROPPED due to local tombstone V=4
  { id: "G-RETIRED", global_id: "G-RETIRED", version: 3, lat: 22.2, lon: 87.2, confidence: 0.8 },
  // 2. Snapshot has G-ALREADY-NEWER with older V=4 -> Local V=5 must NOT be overwritten
  { id: "G-ALREADY-NEWER", global_id: "G-ALREADY-NEWER", version: 4, lat: 22.1, lon: 87.1, confidence: 0.7 },
  // 3. Snapshot has G-FRESH with V=1 -> Should be added
  { id: "G-FRESH", global_id: "G-FRESH", version: 1, lat: 22.3, lon: 87.3, confidence: 0.91 },
  // 4. Snapshot has G-REACTIVATED-FROM-GFS with V=5 > local tombstone V=4 -> Tombstone cleared, object added
  { id: "G-RETIRED-THEN-REACTIVATED", global_id: "G-RETIRED-THEN-REACTIVATED", version: 5, lat: 22.4, lon: 87.4, confidence: 0.94 }
];
localTombstones["G-RETIRED-THEN-REACTIVATED"] = { retiredAt: 1000, version: 4 };

const reconciled = reconcileSnapshot(initialLocalGarbage, gfsSnapshot, localTombstones);
assert(reconciled["G-RETIRED"] === undefined, "Snapshot item with V <= local tombstone must NOT be resurrected");
assert(reconciled["G-ALREADY-NEWER"].version === 5, "Local item with strictly newer version must be preserved");
assert(reconciled["G-FRESH"] !== undefined && reconciled["G-FRESH"].version === 1, "Fresh snapshot item must be added");
assert(reconciled["G-RETIRED-THEN-REACTIVATED"] !== undefined, "Snapshot item strictly newer than tombstone must be restored");
assert(localTombstones["G-RETIRED-THEN-REACTIVATED"] === undefined, "Tombstone for reactivated item must be cleared");

// 16. Section 1: Snapshot Recovery Ordering & Race Suite
console.log("Running Section 1: Exact Snapshot Recovery Ordering & Race Suite...");

// Simulated GCS Client State
let raceGarbage = {};
let raceTombstones = {};
let isSnapshotPending = false;
let eventBuffer = [];
let snapshotWatermark = 0;
let snapshotRevision = 0;

// Stage 1: G1 is created at version 1
const race_g1_v1 = { id: "G-1", label: "rubbish", confidence: 0.88, lat: 22.314, lon: 87.310, version: 1, status: "ACTIVE", event_id: 10 };
raceGarbage = mergeDetections(raceGarbage, [race_g1_v1], "drone1", 1000, new Set(), false, raceTombstones);
assert(raceGarbage["G-1"] !== undefined && raceGarbage["G-1"].version === 1, "Stage 1: G1 created at V=1");

// Stage 2: G1 is retired at version 2
const race_retire_v2 = { id: "G-1", status: "RETIRED", version: 2, event_id: 20 };
raceGarbage = mergeDetections(raceGarbage, [race_retire_v2], "gfs", 2000, new Set(), false, raceTombstones);
assert(raceGarbage["G-1"] === undefined, "Stage 2: G1 removed upon RETIRED");
assert(raceTombstones["G-1"] !== undefined && raceTombstones["G-1"].version === 2, "Stage 2: Tombstone at V=2");

// Stage 3: GCS loses all local storage (memory wiped, storage wiped)
raceGarbage = {};
raceTombstones = {};
assert(Object.keys(raceGarbage).length === 0, "Stage 3: All active garbage wiped");
assert(Object.keys(raceTombstones).length === 0, "Stage 3: All tombstones wiped");

// Stage 4: GCS reconnects and requests a snapshot
isSnapshotPending = true;
eventBuffer = [];
// Sent: { type: "gfs_snapshot_request" }

// Stage 5 & 6: Old ACTIVE version-1 event arrives while old relay events remain queued / snapshot in flight
const stale_queued_active_v1 = { id: "G-1", label: "rubbish", confidence: 0.90, lat: 22.314, lon: 87.310, version: 1, status: "ACTIVE", event_id: 15 };
if (isSnapshotPending) {
  eventBuffer.push(stale_queued_active_v1);
} else {
  raceGarbage = mergeDetections(raceGarbage, [stale_queued_active_v1], "drone1", 2500, new Set(), false, raceTombstones);
}
assert(eventBuffer.length === 1, "Stage 5: Queued event buffered during snapshot reconciliation");
assert(raceGarbage["G-1"] === undefined, "Stage 5: G1 not yet resurrected because event is buffered");

// Snapshot returns from GFS via Relay
const snapshotResponse = {
  type: "gfs_snapshot_response",
  snapshot_revision: 2,
  watermark_event_id: 50,
  objects: [], // G1 is not active in GFS
  tombstones: {
    "G-1": { retiredAt: 2000, version: 2 } // GFS includes authoritative tombstone at V=2!
  }
};

// GCS processes snapshot response
if (snapshotResponse.snapshot_revision >= snapshotRevision) {
  snapshotRevision = snapshotResponse.snapshot_revision;
  snapshotWatermark = snapshotResponse.watermark_event_id;
  raceGarbage = reconcileSnapshot(
    raceGarbage,
    snapshotResponse.objects,
    raceTombstones,
    snapshotResponse.tombstones,
    snapshotWatermark
  );

  // Drain buffer
  const buffered = [...eventBuffer];
  eventBuffer = [];
  isSnapshotPending = false;

  for (const item of buffered) {
    // Drop if covered by watermark
    if (item.event_id && item.event_id <= snapshotWatermark) {
      continue;
    }
    raceGarbage = mergeDetections(raceGarbage, [item], "drone1", 2600, new Set(), false, raceTombstones);
  }
}

// Verification: Stale event covered by snapshot watermark AND rejected by authoritative tombstone V=2
assert(raceGarbage["G-1"] === undefined, "CRITICAL: G1 must NEVER resurrect from stale V=1 event!");
assert(raceTombstones["G-1"] !== undefined && raceTombstones["G-1"].version === 2, "Tombstone G1 preserved at V=2");

// Even if an unwatermarked stale V=1 arrives directly after snapshot
const delayed_unwatermarked_v1 = { id: "G-1", label: "rubbish", confidence: 0.90, lat: 22.314, lon: 87.310, version: 1, status: "ACTIVE" };
raceGarbage = mergeDetections(raceGarbage, [delayed_unwatermarked_v1], "drone2", 3000, new Set(), false, raceTombstones);
assert(raceGarbage["G-1"] === undefined, "Delayed older update V=1 rejected by authoritative tombstone V=2");

// Stage 7: Newer REACTIVATED version-3 event arrives
const newer_reactivated_v3 = { id: "G-1", label: "rubbish", confidence: 0.96, lat: 22.3142, lon: 87.3102, version: 3, status: "REACTIVATED", event_id: 60 };
raceGarbage = mergeDetections(raceGarbage, [newer_reactivated_v3], "gfs", 4000, new Set(), false, raceTombstones);

assert(raceGarbage["G-1"] !== undefined, "Stage 7: G1 successfully restored upon REACTIVATED V=3");
assert(raceGarbage["G-1"].version === 3, "Stage 7: G1 version is 3");
assert(raceTombstones["G-1"] === undefined, "Stage 7: Tombstone cleared upon reactivation");

// Duplicate snapshot response (idempotent, older or same revision ignored)
const duplicateSnapshot = { ...snapshotResponse };
if (duplicateSnapshot.snapshot_revision < snapshotRevision) {
  // Ignored
} else {
  raceGarbage = reconcileSnapshot(raceGarbage, duplicateSnapshot.objects, raceTombstones, duplicateSnapshot.tombstones, duplicateSnapshot.watermark_event_id);
}
assert(raceGarbage["G-1"] !== undefined && raceGarbage["G-1"].version === 3, "Duplicate snapshot is idempotent; G1 V=3 preserved");

console.log("Section 1: Exact Snapshot Recovery Ordering & Race Suite Passed 100%!");

// 17. Local Track <-> Global Object Bidirectional Deduplication Tests
console.log("Running Section 2: Local Track <-> Global Object Bidirectional Deduplication Suite...");

// Case A: Local detection merges into an existing global object within 2m
const stateWithGlobal = {
  "G-0ab2d222": {
    id: "G-0ab2d222",
    global_id: "G-0ab2d222",
    label: "rubbish",
    lat: 47.641500,
    lon: -122.140087,
    confidence: 0.70,
    hits: 10,
    seenBy: ["drone1"],
    version: 1,
    status: "ACTIVE"
  }
};
const localSighting = {
  id: "drone2-g0001",
  label: "rubbish",
  lat: 47.641505, // ~0.55m away
  lon: -122.140087,
  confidence: 0.85,
  hits: 2
};
const stateAfterLocalSighting = mergeDetections(stateWithGlobal, [localSighting], "drone2", 7000);
assert(Object.keys(stateAfterLocalSighting).length === 1, "Must NOT duplicate: local track must merge into existing G-0ab2d222");
assert(stateAfterLocalSighting["G-0ab2d222"] !== undefined, "G-0ab2d222 preserved as primary key");
assert(stateAfterLocalSighting["drone2-g0001"] === undefined, "drone2-g0001 must not be added as a duplicate");
assert(stateAfterLocalSighting["G-0ab2d222"].confidence === 0.85, "Confidence updated to 0.85");
assert(stateAfterLocalSighting["G-0ab2d222"].hits === 12, "Hits incremented (10 + 2 = 12)");
assert(stateAfterLocalSighting["G-0ab2d222"].seenBy.includes("drone2"), "drone2 added to seenBy");

// Case B: Global object arrives and subsumes an existing local track within 2m
const stateWithLocal = {
  "drone1-g0001": {
    id: "drone1-g0001",
    label: "rubbish",
    lat: 47.641500,
    lon: -122.140087,
    confidence: 0.75,
    hits: 5,
    seenBy: ["drone1"],
    status: "ACTIVE"
  }
};
const globalArrival = {
  id: "G-467e474f",
  global_id: "G-467e474f",
  label: "rubbish",
  lat: 47.641504, // ~0.44m away
  lon: -122.140087,
  confidence: 0.80,
  hits: 8,
  version: 2,
  status: "ACTIVE"
};
const stateAfterGlobalArrival = mergeDetections(stateWithLocal, [globalArrival], "gfs", 8000);
assert(Object.keys(stateAfterGlobalArrival).length === 1, "Must NOT duplicate: global object must subsume local track");
assert(stateAfterGlobalArrival["G-467e474f"] !== undefined, "G-467e474f exists");
assert(stateAfterGlobalArrival["drone1-g0001"] === undefined, "drone1-g0001 must be pruned");
assert(stateAfterGlobalArrival["G-467e474f"].hits === 13, "Hits folded in (8 + 5 = 13)");
assert(stateAfterGlobalArrival["G-467e474f"].seenBy.includes("drone1"), "seenBy preserved from local track");

// Case C: Reconcile snapshot subsumes an existing local track within 2m
const localBeforeSnapshot = {
  "drone3-g0001": {
    id: "drone3-g0001",
    label: "rubbish",
    lat: 47.641600,
    lon: -122.140050,
    confidence: 0.65,
    hits: 3,
    seenBy: ["drone3"]
  }
};
const snapshotObj = [{
  id: "G-9999",
  global_id: "G-9999",
  label: "rubbish",
  lat: 47.641602,
  lon: -122.140050,
  confidence: 0.90,
  observation_count: 10,
  source_drones: ["drone1", "drone2"],
  version: 1,
  status: "ACTIVE"
}];
const stateAfterSnapshotSubsume = reconcileSnapshot(localBeforeSnapshot, snapshotObj, {});
assert(Object.keys(stateAfterSnapshotSubsume).length === 1, "Snapshot must subsume local track within 2m");
assert(stateAfterSnapshotSubsume["G-9999"] !== undefined, "G-9999 added");
assert(stateAfterSnapshotSubsume["drone3-g0001"] === undefined, "drone3-g0001 pruned");
assert(stateAfterSnapshotSubsume["G-9999"].seenBy.includes("drone3"), "seenBy drone3 folded into snapshot object");

console.log("Section 2: Local Track <-> Global Object Bidirectional Deduplication Suite Passed 100%!");

// ============================================================================
// Section 3: PhoneOS Provisional Detection Lifecycle Suite (12 Requirements)
// ============================================================================
console.log("Running Section 3: PhoneOS Provisional Detection Lifecycle Suite...");

// 1. mergeDetections with 5 candidates: 1 confirmed, 4 provisional -> 5 markers displayed
const fiveCandidates = [
  {
    id: "G-conf-01",
    global_id: "G-conf-01",
    label: "rubbish",
    lat: 47.641468,
    lon: -122.140165,
    confidence: 0.78,
    lifecycle_state: LifecycleState.CONFIRMED,
    confirmation_method: "multi_drone_association",
    version: 1,
    status: "ACTIVE"
  },
  {
    id: "G-prov-01",
    global_id: "G-prov-01",
    label: "rubbish",
    lat: 47.641500,
    lon: -122.140200,
    confidence: 0.62,
    lifecycle_state: LifecycleState.PROVISIONAL,
    provisional_reason: "SINGLE_DRONE_LOW_CONFIDENCE",
    version: 1,
    status: "ACTIVE"
  },
  {
    id: "G-prov-02",
    global_id: "G-prov-02",
    label: "rubbish",
    lat: 47.641550,
    lon: -122.140250,
    confidence: 0.59,
    lifecycle_state: LifecycleState.PROVISIONAL,
    provisional_reason: "SINGLE_DRONE_LOW_CONFIDENCE",
    version: 1,
    status: "ACTIVE"
  },
  {
    id: "G-prov-03",
    global_id: "G-prov-03",
    label: "rubbish",
    lat: 47.641600,
    lon: -122.140300,
    confidence: 0.61,
    lifecycle_state: LifecycleState.PROVISIONAL,
    provisional_reason: "SINGLE_DRONE_LOW_CONFIDENCE",
    version: 1,
    status: "ACTIVE"
  },
  {
    id: "G-prov-04",
    global_id: "G-prov-04",
    label: "rubbish",
    lat: 47.641650,
    lon: -122.140350,
    confidence: 0.58,
    lifecycle_state: LifecycleState.PROVISIONAL,
    provisional_reason: "SINGLE_DRONE_LOW_CONFIDENCE",
    version: 1,
    status: "ACTIVE"
  }
];

const stateWithFive = mergeDetections({}, fiveCandidates, "gfs", 10000);
assert(Object.keys(stateWithFive).length === 5, "Requirement 1: Exactly 5 markers must be displayed");

// 2. Separate count readouts: getConfirmedGarbage gives 1, getProvisionalGarbage gives 4
const confirmedMap = getConfirmedGarbage(stateWithFive);
const provisionalMap = getProvisionalGarbage(stateWithFive);
assert(Object.keys(confirmedMap).length === 1, "Requirement 2: Confirmed count must be exactly 1");
assert(Object.keys(provisionalMap).length === 4, "Requirement 2: Provisional count must be exactly 4");

// 3. Distinct visual styling attributes: Provisional metadata
for (let i = 1; i <= 4; i++) {
  const pId = `G-prov-0${i}`;
  const pItem = stateWithFive[pId];
  assert(pItem !== undefined, `Provisional ${pId} must exist`);
  assert(pItem.lifecycle_state === LifecycleState.PROVISIONAL, `Requirement 3: ${pId} lifecycle_state must be PROVISIONAL`);
  assert(pItem.provisional_reason === "SINGLE_DRONE_LOW_CONFIDENCE", `Requirement 3: ${pId} provisional_reason must match`);
  assert(!pItem.confirmation_method, `Requirement 3: ${pId} confirmation_method must be falsy`);
}

// 4. Confirmed metadata
const cItem = stateWithFive["G-conf-01"];
assert(cItem.lifecycle_state === LifecycleState.CONFIRMED, "Requirement 4: Confirmed lifecycle_state must be CONFIRMED");
assert(cItem.confirmation_method === "multi_drone_association", "Requirement 4: Confirmed confirmation_method must match");

// 5. Visibility filters correctly partition markers
const allKeys = Object.keys(stateWithFive);
const cKeys = Object.keys(confirmedMap);
const pKeys = Object.keys(provisionalMap);
assert(cKeys.includes("G-conf-01"), "Requirement 5: Confirmed map contains G-conf-01");
assert(!cKeys.some(k => k.startsWith("G-prov")), "Requirement 5: Confirmed map has NO provisional keys");
assert(!pKeys.includes("G-conf-01"), "Requirement 5: Provisional map has NO confirmed keys");
assert(cKeys.length + pKeys.length === allKeys.length, "Requirement 5: Total partitioned equals 5");

// 6. Promotion event: global object with same ID transitions from PROVISIONAL to CONFIRMED
// G-prov-01 is confirmed by second drone:
const promoEvent = {
  id: "G-prov-01",
  global_id: "G-prov-01",
  label: "rubbish",
  lat: 47.641501,
  lon: -122.140201,
  confidence: 0.81,
  lifecycle_state: LifecycleState.CONFIRMED,
  confirmation_method: "multi_drone_association",
  provisional_reason: null,
  version: 2,
  status: "ACTIVE"
};

const stateAfterPromo = mergeDetections(stateWithFive, [promoEvent], "gfs", 12000);
assert(Object.keys(stateAfterPromo).length === 5, "Requirement 6: Total marker count must remain 5 (no duplicates)");
assert(stateAfterPromo["G-prov-01"].lifecycle_state === LifecycleState.CONFIRMED, "Requirement 6: G-prov-01 promoted in-place to CONFIRMED");
assert(stateAfterPromo["G-prov-01"].version === 2, "Requirement 6: Version updated to 2");
const confAfterPromo = getConfirmedGarbage(stateAfterPromo);
const provAfterPromo = getProvisionalGarbage(stateAfterPromo);
assert(Object.keys(confAfterPromo).length === 2, "Requirement 6: Confirmed count now 2");
assert(Object.keys(provAfterPromo).length === 3, "Requirement 6: Provisional count now 3");

// 7. Retirement event: marker disappears upon status: 'RETIRED' and tombstone recorded
const testTombstones = {};
const retireProvEvent = {
  id: "G-prov-04",
  status: "RETIRED",
  version: 2
};
const stateAfterRetire = mergeDetections(stateAfterPromo, [retireProvEvent], "gfs", 13000, new Set(), false, testTombstones);
assert(stateAfterRetire["G-prov-04"] === undefined, "Requirement 7: G-prov-04 removed on retirement");
assert(Object.keys(stateAfterRetire).length === 4, "Requirement 7: Total active markers now 4");
assert(testTombstones["G-prov-04"] !== undefined, "Requirement 7: Tombstone recorded for G-prov-04");

// 8. Offline snapshot reload: reconcileSnapshot preserves both confirmed and provisional correctly
const snapshotPayload = [
  {
    id: "G-conf-01",
    global_id: "G-conf-01",
    label: "rubbish",
    lat: 47.641468,
    lon: -122.140165,
    confidence: 0.78,
    lifecycle_state: LifecycleState.CONFIRMED,
    confirmation_method: "multi_drone_association",
    version: 1,
    status: "ACTIVE"
  },
  {
    id: "G-prov-01",
    global_id: "G-prov-01",
    label: "rubbish",
    lat: 47.641501,
    lon: -122.140201,
    confidence: 0.81,
    lifecycle_state: LifecycleState.CONFIRMED,
    confirmation_method: "multi_drone_association",
    version: 2,
    status: "ACTIVE"
  },
  {
    id: "G-prov-02",
    global_id: "G-prov-02",
    label: "rubbish",
    lat: 47.641550,
    lon: -122.140250,
    confidence: 0.59,
    lifecycle_state: LifecycleState.PROVISIONAL,
    provisional_reason: "SINGLE_DRONE_LOW_CONFIDENCE",
    version: 1,
    status: "ACTIVE"
  },
  {
    id: "G-prov-03",
    global_id: "G-prov-03",
    label: "rubbish",
    lat: 47.641600,
    lon: -122.140300,
    confidence: 0.61,
    lifecycle_state: LifecycleState.PROVISIONAL,
    provisional_reason: "SINGLE_DRONE_LOW_CONFIDENCE",
    version: 1,
    status: "ACTIVE"
  }
];

const restoredFromSnapshot = reconcileSnapshot({}, snapshotPayload, testTombstones);
assert(Object.keys(restoredFromSnapshot).length === 4, "Requirement 8: Reconciled snapshot has 4 items");
assert(Object.keys(getConfirmedGarbage(restoredFromSnapshot)).length === 2, "Requirement 8: Snapshot has 2 confirmed");
assert(Object.keys(getProvisionalGarbage(restoredFromSnapshot)).length === 2, "Requirement 8: Snapshot has 2 provisional");

// 9. Version-aware update: older version ignored, newer version applied
const staleUpdate = {
  id: "G-prov-01",
  version: 1,
  confidence: 0.50,
  lifecycle_state: LifecycleState.PROVISIONAL
};
const stateAfterStalePromo = mergeDetections(restoredFromSnapshot, [staleUpdate], "gfs", 14000, new Set(), false, testTombstones);
assert(stateAfterStalePromo["G-prov-01"].version === 2, "Requirement 9: Version 2 preserved against stale version 1");
assert(stateAfterStalePromo["G-prov-01"].lifecycle_state === LifecycleState.CONFIRMED, "Requirement 9: Confirmed status preserved");

// 10. Local dismissal: user dismisses provisional marker -> stays dismissed across snapshots
const userDismissed = new Set(["G-prov-02"]);
const stateAfterDismiss = mergeDetections(restoredFromSnapshot, [], "gfs", 15000, userDismissed, false, testTombstones);
assert(stateAfterDismiss["G-prov-02"] === undefined, "Requirement 10: Dismissed G-prov-02 not in active state");
const reSnapshot = reconcileSnapshot(stateAfterDismiss, snapshotPayload, testTombstones, {}, 0, userDismissed);
assert(reSnapshot["G-prov-02"] === undefined, "Requirement 10: Dismissed marker stays excluded after snapshot reload");

// 11. Same drone multiple observations do not prematurely set lifecycle_state = 'CONFIRMED'
const localSingleDronePkt1 = {
  id: "drone4-track99",
  label: "rubbish",
  confidence: 0.62,
  lat: 47.642000,
  lon: -122.141000,
  hits: 2
};
const stateDrone1 = mergeDetections({}, [localSingleDronePkt1], "drone4", 16000);
assert(stateDrone1["drone4-track99"].lifecycle_state === LifecycleState.PROVISIONAL, "Requirement 11: Single drone track is PROVISIONAL");

const localSingleDronePkt2 = {
  id: "drone4-track99",
  label: "rubbish",
  confidence: 0.63,
  lat: 47.642000,
  lon: -122.141000,
  hits: 5
};
const stateDrone2 = mergeDetections(stateDrone1, [localSingleDronePkt2], "drone4", 17000);
assert(stateDrone2["drone4-track99"].lifecycle_state === LifecycleState.PROVISIONAL, "Requirement 11: Multiple hits from SAME drone must remain PROVISIONAL");
assert(stateDrone2["drone4-track99"].seenBy.length === 1 && stateDrone2["drone4-track99"].seenBy[0] === "drone4", "Requirement 11: Single drone provenance");

// 12. Max 500 entries bound respects mixed provisional and confirmed objects
let maxTestState = {};
for (let i = 0; i < 510; i++) {
  const isConf = (i % 2 === 0);
  maxTestState[`item-mix-${i}`] = {
    id: `item-mix-${i}`,
    lat: 47.64 + i * 0.0001,
    lon: -122.14,
    confidence: isConf ? 0.75 : 0.60,
    lifecycle_state: isConf ? LifecycleState.CONFIRMED : LifecycleState.PROVISIONAL,
    hits: 1,
    firstSeen: 20000 + i,
    lastSeen: 20000 + i
  };
}
const boundedMixedState = mergeDetections(maxTestState, [], "gfs", 30000);
assert(Object.keys(boundedMixedState).length === 500, "Requirement 12: Bound capped at 500");
assert(boundedMixedState["item-mix-0"] === undefined, "Requirement 12: Oldest dropped");
assert(boundedMixedState["item-mix-509"] !== undefined, "Requirement 12: Newest retained");

console.log("Section 3: PhoneOS Provisional Detection Lifecycle Suite (12 Requirements) Passed 100%!");
console.log("All garbage utility, recovery, and provisional lifecycle tests passed successfully!");
