import { mergeDetections, isValidDetectionItem } from './garbage.js';

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

console.log("All garbage utility tests passed successfully!");

