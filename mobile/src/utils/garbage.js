// Pure utility functions for merging, validating, and managing detected garbage markers

import { calculateDistance } from './geoUtils.js';

export const MAX_GARBAGE_ENTRIES = 500;
export const MULTI_DRONE_MERGE_RADIUS_M = 2.0;

/**
 * Standard benchmark garbage targets (AirSim origin coordinates).
 * Seeded on initial app launch when no prior session data exists.
 */
export const DEFAULT_BENCHMARK_TARGETS = {
  'G-0ab2d222': {
    id: 'G-0ab2d222',
    global_id: 'G-0ab2d222',
    label: 'rubbish',
    status: 'ACTIVE',
    lat: 47.6414997000614,
    lon: -122.14008769986052,
    alt: 10.14,
    confidence: 0.68,
    hits: 75,
    reportingDrone: 'GFS',
    seenBy: ['drone1', 'drone3'],
    version: 36,
    firstSeen: 1794806842337,
    lastSeen: 1794806907545
  },
  'G-467e474f': {
    id: 'G-467e474f',
    global_id: 'G-467e474f',
    label: 'rubbish',
    status: 'ACTIVE',
    lat: 47.64163450006141,
    lon: -122.14008769986052,
    alt: 10.14,
    confidence: 0.68,
    hits: 74,
    reportingDrone: 'GFS',
    seenBy: ['drone2', 'drone4'],
    version: 36,
    firstSeen: 1794806842337,
    lastSeen: 1794806907545
  }
};

/**
 * Validate coordinates and confidence for an incoming detection item.
 */
export function isValidDetectionItem(item) {
  if (!item || typeof item !== 'object') return false;
  const itemId = item.id || item.global_id;
  if (!itemId || typeof itemId !== 'string') return false;

  // Retirement events only require a valid string ID
  if (item.status === 'RETIRED' || item.event_type === 'GLOBAL_OBJECT_RETIRED') {
    return true;
  }

  const lat = Number(item.lat);
  const lon = Number(item.lon);
  const conf = Number(item.confidence);

  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(conf)) return false;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return false;
  // Reject (0, 0) default/uninitialized coordinates
  if (Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6) return false;
  if (conf < 0 || conf > 1.0) return false;

  return true;
}

/**
 * Merge newly received detection items into the current garbage state dictionary.
 * Pure function - returns a new dictionary without mutating arguments.
 * Enforces version-aware lifecycle management, tombstone durability, and idempotent retirement.
 *
 * @param {Record<string, object>} prevById - Existing garbage state mapped by item id
 * @param {Array<object>} items - Incoming detection items from DetectionMessage or GFS Outbox
 * @param {string} droneId - Reporting drone sender_id
 * @param {number} nowMs - Current epoch millisecond timestamp
 * @param {Set<string>|Array<string>} dismissedIds - IDs explicitly dismissed by operator
 * @param {boolean} isSynthetic - Whether message is flagged synthetic
 * @param {Record<string, {retiredAt: number, version: number}>} tombstones - Tombstone registry
 * @returns {Record<string, object>} Updated garbage state dictionary
 */
export function mergeDetections(
  prevById = {},
  items = [],
  droneId = 'unknown',
  nowMs = Date.now(),
  dismissedIds = new Set(),
  isSynthetic = false,
  tombstones = {}
) {
  const dismissedSet = dismissedIds instanceof Set ? dismissedIds : new Set(dismissedIds || []);
  const nextById = { ...prevById };

  for (const rawItem of items) {
    if (!isValidDetectionItem(rawItem)) continue;
    const itemId = rawItem.id || rawItem.global_id;
    if (dismissedSet.has(itemId)) continue;
    const isRetired = rawItem.status === 'RETIRED' || rawItem.event_type === 'GLOBAL_OBJECT_RETIRED';
    const incomingVersion = rawItem.version !== undefined ? Number(rawItem.version) : 0;

    // 1. LIFECYCLE RULE: Retirement Event Handling
    if (isRetired) {
      const existingTombstone = tombstones[itemId];
      const maxVersion = Math.max(incomingVersion, existingTombstone ? existingTombstone.version : 0);
      tombstones[itemId] = {
        retiredAt: nowMs,
        version: maxVersion,
      };
      // Idempotently remove from active map
      delete nextById[itemId];
      continue;
    }

    // 2. LIFECYCLE RULE: Tombstone Check & Resurrection Prevention
    const tombstone = tombstones[itemId];
    if (tombstone) {
      // Reactivation is supported ONLY if explicitly allowed by GFS with status 'REACTIVATED' and strictly higher version
      const isExplicitReactivation = (rawItem.status === 'REACTIVATED') && (incomingVersion > tombstone.version);
      if (isExplicitReactivation) {
        delete tombstones[itemId];
      } else {
        // Delayed older updates or unauthenticated resurrections are dropped
        continue;
      }
    }

    const syntheticFlag = Boolean(rawItem.synthetic || isSynthetic);

    // 3. LIFECYCLE RULE: Version-Aware Update for Existing ID
    if (nextById[itemId]) {
      const existing = nextById[itemId];
      const existingVersion = existing.version !== undefined ? Number(existing.version) : null;

      // Reject delayed older version updates for the same object
      if (rawItem.version !== undefined && existingVersion !== null && incomingVersion < existingVersion) {
        continue;
      }

      const seenBy = Array.isArray(existing.seenBy) ? [...existing.seenBy] : [existing.reportingDrone || droneId];
      if (!seenBy.includes(droneId)) seenBy.push(droneId);

      const isGlobal = itemId.startsWith('G-') || Boolean(rawItem.global_id);

      nextById[itemId] = {
        ...existing,
        lat: isGlobal ? Number(rawItem.lat) : existing.lat,
        lon: isGlobal ? Number(rawItem.lon) : existing.lon,
        confidence: Math.max(existing.confidence, Number(rawItem.confidence)),
        hits: Math.max(existing.hits, Number(rawItem.hits || 1)),
        lastSeen: nowMs,
        seenBy,
        altitude_agl: rawItem.altitude_agl !== undefined ? rawItem.altitude_agl : existing.altitude_agl,
        synthetic: Boolean(existing.synthetic || syntheticFlag),
        version: rawItem.version !== undefined ? Math.max(incomingVersion, existingVersion || 0) : existing.version,
        status: rawItem.status || existing.status || 'ACTIVE',
      };

      if (isGlobal) {
        // Subsume any co-located local tracks that coincide with this global object
        for (const [key, locObj] of Object.entries(nextById)) {
          if (key === itemId) continue;
          if (key.startsWith('G-') || locObj.global_id) continue;
          const d = calculateDistance(Number(rawItem.lat), Number(rawItem.lon), locObj.lat, locObj.lon);
          if (d <= MULTI_DRONE_MERGE_RADIUS_M) {
            if (Array.isArray(locObj.seenBy)) {
              for (const s of locObj.seenBy) {
                if (!nextById[itemId].seenBy.includes(s)) nextById[itemId].seenBy.push(s);
              }
            }
            nextById[itemId].hits += Number(locObj.hits || 1);
            delete nextById[key];
          }
        }
      }
      continue;
    }

    // 4. Rule B: Proximity merge (merge into closest existing object within radius)
    const isGlobalObject = itemId.startsWith('G-') || Boolean(rawItem.global_id);
    let matchedKey = null;
    let minDistance = Infinity;

    if (!isGlobalObject) {
      for (const [key, existing] of Object.entries(nextById)) {
        if (dismissedSet.has(key)) continue;
        const d = calculateDistance(rawItem.lat, rawItem.lon, existing.lat, existing.lon);
        if (d <= MULTI_DRONE_MERGE_RADIUS_M && d < minDistance) {
          minDistance = d;
          matchedKey = key;
        }
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

    // 5. Rule C: New object
    nextById[itemId] = {
      id: itemId,
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
      version: rawItem.version !== undefined ? incomingVersion : undefined,
      status: rawItem.status || 'ACTIVE',
    };

    if (isGlobalObject) {
      // Subsume any co-located local tracks that were created before this global object arrived
      for (const [key, locObj] of Object.entries(nextById)) {
        if (key === itemId) continue;
        if (key.startsWith('G-') || locObj.global_id) continue;
        const d = calculateDistance(Number(rawItem.lat), Number(rawItem.lon), locObj.lat, locObj.lon);
        if (d <= MULTI_DRONE_MERGE_RADIUS_M) {
          if (Array.isArray(locObj.seenBy)) {
            for (const s of locObj.seenBy) {
              if (!nextById[itemId].seenBy.includes(s)) nextById[itemId].seenBy.push(s);
            }
          }
          nextById[itemId].hits += Number(locObj.hits || 1);
          delete nextById[key];
        }
      }
    }
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

/**
 * Load persisted tombstones from storage (localStorage or storage adapter).
 * @param {Storage|object} storage
 * @returns {Record<string, {retiredAt: number, version: number}>}
 */
export function loadPersistedTombstones(storage) {
  if (!storage) return {};
  try {
    const raw = typeof storage.getItem === 'function' ? storage.getItem('PhoneOS_Tombstones') : storage['PhoneOS_Tombstones'];
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

/**
 * Save tombstones into persistent storage.
 * @param {Storage|object} storage
 * @param {Record<string, {retiredAt: number, version: number}>} tombstones
 */
export function savePersistedTombstones(storage, tombstones) {
  if (!storage) return;
  try {
    const serialized = JSON.stringify(tombstones || {});
    if (typeof storage.setItem === 'function') {
      storage.setItem('PhoneOS_Tombstones', serialized);
    } else {
      storage['PhoneOS_Tombstones'] = serialized;
    }
  } catch (e) {
    // Ignore quota or private browsing errors gracefully
  }
}

/**
 * Load persisted active garbage markers from storage (localStorage or storage adapter).
 * Safely recovers from missing or corrupted storage.
 * @param {Storage|object} storage
 * @returns {Record<string, object>}
 */
export function loadPersistedGarbage(storage) {
  if (!storage) return {};
  try {
    const raw = typeof storage.getItem === 'function' ? storage.getItem('PhoneOS_Garbage') : storage['PhoneOS_Garbage'];
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

/**
 * Save active garbage markers into persistent storage.
 * Gracefully handles quota and private browsing write failures.
 * @param {Storage|object} storage
 * @param {Record<string, object>} garbage
 */
export function savePersistedGarbage(storage, garbage) {
  if (!storage) return;
  try {
    const serialized = JSON.stringify(garbage || {});
    if (typeof storage.setItem === 'function') {
      storage.setItem('PhoneOS_Garbage', serialized);
    } else {
      storage['PhoneOS_Garbage'] = serialized;
    }
  } catch (e) {
    // Ignore quota or private browsing errors gracefully
  }
}

/**
 * Reconcile an authoritative GFS snapshot of global objects into current GCS state (Contract B).
 * Enforces authoritative tombstone synchronization, tombstone version precedence,
 * active version monotonicity, and watermark tracking.
 *
 * @param {Record<string, object>} currentGarbage
 * @param {Array<object>} snapshotObjects
 * @param {Record<string, {retiredAt: number, version: number}>} tombstones - Local tombstone registry (mutated in place)
 * @param {Record<string, {retiredAt: number, version: number}>} snapshotTombstones - Authoritative tombstones from snapshot
 * @param {number} watermarkEventId - Highest GFS outbox event ID covered by snapshot
 * @returns {Record<string, object>} Reconciled garbage dictionary
 */
export function reconcileSnapshot(
  currentGarbage = {},
  snapshotObjects = [],
  tombstones = {},
  snapshotTombstones = {},
  watermarkEventId = 0
) {
  const next = { ...currentGarbage };

  // 1. Authoritative Tombstone Ingestion from Snapshot (resolves lost-storage race)
  if (snapshotTombstones && typeof snapshotTombstones === 'object') {
    for (const [id, sTomb] of Object.entries(snapshotTombstones)) {
      if (!sTomb) continue;
      const sVer = Number(sTomb.version !== undefined ? sTomb.version : 0);
      const existingTomb = tombstones[id];
      const maxVer = Math.max(sVer, existingTomb ? Number(existingTomb.version || 0) : 0);
      tombstones[id] = {
        retiredAt: sTomb.retiredAt || Date.now(),
        version: maxVer,
      };
      // If currentGarbage holds an item that is retired at or above its version, delete it
      if (next[id]) {
        const itemVer = Number(next[id].version !== undefined ? next[id].version : 0);
        if (itemVer <= maxVer) {
          delete next[id];
        }
      }
    }
  }

  // 2. Active Snapshot Objects Reconciliation
  for (const rawObj of snapshotObjects) {
    if (!rawObj || typeof rawObj !== 'object') continue;
    const id = rawObj.id || rawObj.global_id;
    if (!id) continue;
    const incomingVer = Number(rawObj.version !== undefined ? rawObj.version : 0);

    // Check tombstone: if retired locally at >= snapshot version, drop snapshot object
    const tombstone = tombstones[id];
    if (tombstone) {
      if (incomingVer <= tombstone.version) {
        continue;
      } else {
        // Snapshot is strictly newer than tombstone: clear tombstone
        delete tombstones[id];
      }
    }

    // Check existing active: keep existing if already newer
    const existing = next[id];
    if (existing && existing.version !== undefined && Number(existing.version) > incomingVer) {
      continue;
    }

    next[id] = {
      id,
      label: rawObj.label || 'rubbish',
      confidence: Number(rawObj.confidence || 0.8),
      lat: Number(rawObj.lat),
      lon: Number(rawObj.lon),
      alt: rawObj.alt !== undefined ? Number(rawObj.alt) : null,
      hits: Number(rawObj.observation_count || rawObj.hits || 1),
      firstSeen: rawObj.first_seen_ms || Date.now(),
      lastSeen: rawObj.last_seen_ms || Date.now(),
      reportingDrone: 'GFS',
      seenBy: rawObj.source_drones || ['GFS'],
      version: incomingVer,
      status: rawObj.status || 'ACTIVE',
      watermark_event_id: watermarkEventId
    };

    // Subsume any co-located local tracks that coincide with this authoritative global object
    for (const [key, locObj] of Object.entries(next)) {
      if (key === id) continue;
      if (key.startsWith('G-') || locObj.global_id) continue;
      const d = calculateDistance(Number(rawObj.lat), Number(rawObj.lon), locObj.lat, locObj.lon);
      if (d <= MULTI_DRONE_MERGE_RADIUS_M) {
        if (Array.isArray(locObj.seenBy)) {
          for (const s of locObj.seenBy) {
            if (!next[id].seenBy.includes(s)) next[id].seenBy.push(s);
          }
        }
        next[id].hits = Math.max(next[id].hits, (next[id].hits || 0) + (locObj.hits || 1));
        delete next[key];
      }
    }
  }
  return next;
}
