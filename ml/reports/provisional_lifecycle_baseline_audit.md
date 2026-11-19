# Baseline Audit: GFS Provisional Detection Lifecycle & PhoneOS Integration

**Repository:** `https://github.com/Debanshu2005/DroneSwarm`  
**Branch:** `garbage-detection`  
**Working Tree Status:** Clean baseline on commit `c75628e`  
**Audit Date:** November 19, 2026  
**Document:** Phase 0 Baseline Engineering Audit  

---

## 1. Executive Summary & Context

A forensic investigation into four-drone collaborative garbage perception demonstrated that when five distinct, well-separated targets are spawned in Microsoft AirSim:
1. All 5 targets are detected by YOLO ($\text{conf} \in [0.58, 0.65] \ge 0.55$).
2. All 5 targets pass pinhole geolocation and 3D ray-to-peer CPA exclusion.
3. All 5 targets pass edge track aggregation temporal gating ($\text{min\_hits} = 2$) and emit valid local tracks.
4. GFS receives 6 observations across 4 drones.
5. **GFS creates only 1 GlobalObject (`G-750329df`) and defers the other 4 candidate tracklets as `DEFERRED_LOW_CONFIDENCE`.**
6. PhoneOS GCS receives an authoritative snapshot with only 1 active object and displays strictly **1 marker**.

The objective of this engineering initiative is to introduce a formal **PROVISIONAL** lifecycle state into the system, ensuring that valid, georeferenced single-drone observations are visible to human operators without degrading the trusted confirmation standards for survivor/garbage recovery.

---

## 2. Current Architecture & State Storage Audit

### 2.1 GFS Storage Topology ([`gfs/database.py`](file:///d:/CityGrid/my-project/PhoneOS_Swarm/gfs/database.py))

GFS persists its authoritative state in SQLite (`data/gfs.db`):

* **`global_objects` Table:**
  ```sql
  CREATE TABLE IF NOT EXISTS global_objects (
      global_id TEXT PRIMARY KEY,
      label TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'RETIRED', 'ARCHIVED')),
      lat REAL NOT NULL,
      lon REAL NOT NULL,
      alt REAL NOT NULL,
      east_m REAL NOT NULL,
      north_m REAL NOT NULL,
      cov_xx REAL NOT NULL,
      cov_yy REAL NOT NULL,
      cov_xy REAL NOT NULL,
      confidence REAL NOT NULL,
      first_seen_ms INTEGER NOT NULL,
      last_seen_ms INTEGER NOT NULL,
      observation_count INTEGER NOT NULL DEFAULT 1,
      source_count INTEGER NOT NULL DEFAULT 1,
      source_estimates_json TEXT NOT NULL DEFAULT '{}',
      version INTEGER NOT NULL DEFAULT 1
  );
  ```
  *Current Limitation:* `status` only accepts `'ACTIVE'`, `'RETIRED'`, `'ARCHIVED'`. There is no dedicated field to distinguish `CONFIRMED` from `PROVISIONAL` objects.
* **`global_track_identity_history` Table:**
  Maps `(drone_id, drone_session_id, session_generation, local_track_id)` to `global_id` to prevent track migration.
* **`deferred_observations` Table:**
  Stores observations deferred due to `LOW_CONFIDENCE`, `AMBIGUOUS_CLIQUE`, etc., for re-evaluation in subsequent batches within `deferred_observation_ttl_ms = 60000`.
  *Current Limitation:* Single-drone observations with confidence $0.55 \le \text{conf} < 0.70$ repeatedly fail Stage B hypothesis formation on every batch and are never committed to `global_objects`.
* **`gfs_outbox_events` Table:**
  Durable outbox queue delivering JSON events to Relay and GCS clients.

### 2.2 GFS Stage B Hypothesis Formation ([`gfs/hypothesis.py`](file:///d:/CityGrid/my-project/PhoneOS_Swarm/gfs/hypothesis.py#L248-L251))

In `form_new_global_objects`:
```python
# Hypothesis acceptance rule:
# Multi-drone confirmation (>= 2 drones) OR single-drone high confidence (>= 0.70)
if len(distinct_drones) >= 2 or max_conf >= 0.70:
    # Form new global object
    ...
else:
    # Single-drone low-confidence candidate: defer
    for t in clean_members:
        low_confidence_deferred.append(t)
```
*Current Limitation:* There is no intermediate path for candidates with $0.55 \le \text{conf} < 0.70$ to be represented as active provisional objects.

### 2.3 Relay Forwarding & Snapshot Generation ([`relay/relay.py`](file:///d:/CityGrid/my-project/PhoneOS_Swarm/relay/relay.py#L409-L440))

When PhoneOS requests a snapshot (`gfs_snapshot_request`), the relay executes:
```sql
SELECT global_id, label, status, lat, lon, alt, confidence,
       observation_count, source_count, version, first_seen_ms, last_seen_ms
FROM global_objects
```
It returns all rows with `status == 'ACTIVE'` in `objects: []`. Because single-drone candidates were deferred and not written to `global_objects`, they are absent from the snapshot.

### 2.4 PhoneOS Client Contract ([`mobile/src/utils/garbage.js`](file:///d:/CityGrid/my-project/PhoneOS_Swarm/mobile/src/utils/garbage.js))

PhoneOS manages garbage state via:
* `mergeDetections`: Merges incoming `DetectionMessage` or `gfs_outbox_event` payloads, enforcing tombstone checks and version-monotonic updates.
* `reconcileSnapshot`: Authoritatively synchronizes local state with the GFS snapshot objects and tombstones.
* *Current Limitation:* PhoneOS assumes all incoming objects with IDs starting with `G-` are confirmed global objects. There is no representation of `PROVISIONAL` state or visual distinction on the map.

---

## 3. Protocol Contracts & Baseline Analysis

| Layer | Contract / Channel | Current Behavior | Gap for Provisional Support |
| :--- | :--- | :--- | :--- |
| **DroneOS $\to$ GFS** | UDP (Port 41458) `DetectionMessage` | Sends local tracks confirmed by edge aggregator ($\text{hits} \ge 2$) | Fully compliant. No changes required at drone edge. |
| **GFS Core** | SQLite `global_objects` | Only stores confirmed objects ($\text{drones} \ge 2 \lor \text{conf} \ge 0.70$) | Needs `lifecycle_state`, `confirmation_method`, `provisional_reason`, and provisional TTL management. |
| **GFS Outbox** | UDP (Port 41455) `gfs_outbox_events` | Emits `GLOBAL_OBJECT_NEW`, `GLOBAL_OBJECT_UPDATE`, `GLOBAL_OBJECT_REACTIVATED`, `GLOBAL_OBJECT_RETIRED` | Needs `lifecycle_state` field and events `GLOBAL_OBJECT_PROVISIONAL`, `GLOBAL_OBJECT_CONFIRMED`. |
| **Relay $\to$ PhoneOS** | WebSocket `gfs_snapshot_response` | Transmits active `global_objects` | Needs to serialize `lifecycle_state`, `provisional_reason`, and `source_drones` in snapshot items. |
| **PhoneOS GCS** | React State & Leaflet Map | Renders all objects with solid pins colored by confidence | Needs dual state/filtering (`confirmedGarbage` vs `provisionalGarbage`), distinct visual styles (yellow/amber dashed ring for provisional), and layer toggles. |

---

## 4. Architectural Roadmap for Implementation

1. **Schema Migration:** Extend `global_objects` table in a backward-compatible manner:
   * Add `lifecycle_state TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK(lifecycle_state IN ('PROVISIONAL', 'CONFIRMED', 'REJECTED', 'RETIRED'))`.
   * Add `confirmation_method TEXT`.
   * Add `provisional_reason TEXT`.
   * Add `expires_at_ms INTEGER`.
2. **Deterministic Provisional Identity:**
   * Single-drone provisional objects use stable synthetic ID: `P-{hash(drone_id:local_track_id)[:8]}` or canonical global ID mapping.
   * Promotion from `PROVISIONAL` to `CONFIRMED` updates the object in place or provides unambiguous transition, preventing duplicate markers.
3. **GFS Stage B Enhancement:**
   * Multi-drone compatible ($\ge 2$ drones) or single-drone high-confidence ($\ge 0.70$) $\to$ `CONFIRMED`.
   * Single-drone valid candidate ($0.55 \le \text{conf} < 0.70$) $\to$ `PROVISIONAL` (`status = 'ACTIVE'`, `lifecycle_state = 'PROVISIONAL'`).
4. **Relay & Snapshot Updates:**
   * Transmit `lifecycle_state`, `provisional_reason`, and `confirmation_method` in both event streams and snapshots.
5. **PhoneOS UI & Logic Updates:**
   * Extend `garbage.js` and `GarbageLayer.jsx` to render provisional markers in amber/yellow with dashed rings and `UNCONFIRMED` badges.
   * Provide separate counts: `Confirmed: X`, `Provisional: Y`, `Total: Z`.
   * Provide layer toggle controls.
6. **Comprehensive Testing & Regressions:**
   * Unit tests for lifecycle transitions, persistence, expiry, snapshot reconciliation, and PhoneOS rendering.
