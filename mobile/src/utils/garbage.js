// Pure utility functions for merging, validating, and managing detected garbage markers

import { calculateDistance } from './geoUtils.js';

export const MAX_GARBAGE_ENTRIES = 500;
export const MULTI_DRONE_MERGE_RADIUS_M = 2.0;

/**
 * Validate coordinates and confidence for an incoming detection item.
 */
export function isValidDetectionItem(item) {
  if (!item || typeof item !== 'object') return false;

  const lat = Number(item.lat);
  const lon = Number(item.lon);
  const conf = Number(item.confidence);

  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(conf)) return false;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return false;
  // Reject (0, 0) default/uninitialized coordinates
  if (Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6) return false;
  if (conf < 0 || conf > 1.0) return false;
  if (!item.id || typeof item.id !== 'string') return false;

  return true;
}

/**
 * Merge newly received detection items into the current garbage state dictionary.
 * Pure function - returns a new dictionary without mutating arguments.
 *
 * @param {Record<string, object>} prevById - Existing garbage state mapped by item id
 * @param {Array<object>} items - Incoming detection items from DetectionMessage
 * @param {string} droneId - Reporting drone sender_id
 * @param {number} nowMs - Current epoch millisecond timestamp
 * @param {Set<string>|Array<string>} dismissedIds - IDs explicitly dismissed by operator
 * @param {boolean} isSynthetic - Whether message is flagged synthetic
 * @returns {Record<string, object>} Updated garbage state dictionary
 */
export function mergeDetections(
  prevById = {},
  items = [],
  droneId = 'unknown',
  nowMs = Date.now(),
  dismissedIds = new Set(),
  isSynthetic = false
) {
  const dismissedSet = dismissedIds instanceof Set ? dismissedIds : new Set(dismissedIds || []);
  const nextById = { ...prevById };

  for (const rawItem of items) {
    if (!isValidDetectionItem(rawItem)) continue;
    if (dismissedSet.has(rawItem.id)) continue;

    const syntheticFlag = Boolean(rawItem.synthetic || isSynthetic);

    // Rule A: Same ID exists -> update
    if (nextById[rawItem.id]) {
      const existing = nextById[rawItem.id];
      const seenBy = Array.isArray(existing.seenBy) ? [...existing.seenBy] : [existing.reportingDrone || droneId];
      if (!seenBy.includes(droneId)) seenBy.push(droneId);

      nextById[rawItem.id] = {
        ...existing,
        confidence: Math.max(existing.confidence, Number(rawItem.confidence)),
        hits: Math.max(existing.hits, Number(rawItem.hits || 1)),
        lastSeen: nowMs,
        seenBy,
        altitude_agl: rawItem.altitude_agl !== undefined ? rawItem.altitude_agl : existing.altitude_agl,
        synthetic: Boolean(existing.synthetic || syntheticFlag),
      };
      continue;
    }

    // Rule B: Different ID within 2 m -> treat as same object observed by another drone
    let matchedKey = null;
    let minDistance = Infinity;

    for (const [key, existing] of Object.entries(nextById)) {
      if (dismissedSet.has(key)) continue;
      const d = calculateDistance(rawItem.lat, rawItem.lon, existing.lat, existing.lon);
      if (d <= MULTI_DRONE_MERGE_RADIUS_M && d < minDistance) {
        minDistance = d;
        matchedKey = key;
      }
    }

    if (matchedKey !== null) {
      const existing = nextById[matchedKey];
      const seenBy = Array.isArray(existing.seenBy) ? [...existing.seenBy] : [existing.reportingDrone || droneId];
      if (!seenBy.includes(droneId)) seenBy.push(droneId);

      nextById[matchedKey] = {
        ...existing,
        confidence: Math.max(existing.confidence, Number(rawItem.confidence)),
        hits: existing.hits + Number(rawItem.hits || 1),
        lastSeen: nowMs,
        seenBy,
        synthetic: Boolean(existing.synthetic || syntheticFlag),
      };
      continue;
    }

    // Rule C: New object
    nextById[rawItem.id] = {
      id: rawItem.id,
      label: rawItem.label || 'rubbish',
      confidence: Number(rawItem.confidence),
      lat: Number(rawItem.lat),
      lon: Number(rawItem.lon),
      hits: Number(rawItem.hits || 1),
      altitude_agl: rawItem.altitude_agl !== undefined ? rawItem.altitude_agl : null,
      firstSeen: nowMs,
      lastSeen: nowMs,
      reportingDrone: droneId,
      seenBy: [droneId],
      synthetic: syntheticFlag,
    };
  }

  // Bound to MAX_GARBAGE_ENTRIES (500), dropping oldest by lastSeen
  const keys = Object.keys(nextById);
  if (keys.length > MAX_GARBAGE_ENTRIES) {
    const sorted = keys.map(k => nextById[k]).sort((a, b) => a.lastSeen - b.lastSeen);
    const excess = keys.length - MAX_GARBAGE_ENTRIES;
    for (let i = 0; i < excess; i++) {
      delete nextById[sorted[i].id];
    }
  }

  return nextById;
}

