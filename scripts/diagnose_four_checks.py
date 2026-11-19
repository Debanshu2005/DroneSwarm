import asyncio
import json
import sqlite3
import subprocess
import shutil
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys_path = str(REPO_ROOT)
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from relay.relay import UdpWebsocketRelay


def check_1_gfs_sqlite(db_path: str):
    print("=" * 80)
    print("CHECK 1: Query GFS SQLite for distinct active garbage tracks")
    print(f"Database: {db_path}")
    print("=" * 80)
    if not Path(db_path).exists():
        print(f"ERROR: {db_path} does not exist.")
        return 0, []

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT global_id, label, status, lifecycle_state, confidence, lat, lon, source_count, source_estimates_json, expires_at_ms
        FROM global_objects
        WHERE status = 'ACTIVE'
    """)
    rows = cur.fetchall()
    print(f"Distinct active garbage tracks in GFS SQLite: {len(rows)}")
    for r in rows:
        print(f"  - [{r['lifecycle_state']}] {r['global_id']}: conf={r['confidence']}, lat={r['lat']:.6f}, lon={r['lon']:.6f}, sources={r['source_count']}, expires_at={r['expires_at_ms']}")

    # Also check total rows including RETIRED / ARCHIVED
    cur.execute("SELECT global_id, status, lifecycle_state FROM global_objects")
    all_rows = cur.fetchall()
    print(f"Total global_objects (all statuses): {len(all_rows)}")
    for r in all_rows:
        if r['status'] != 'ACTIVE':
            print(f"  - [{r['status']}/{r['lifecycle_state']}] {r['global_id']}")

    con.close()
    return len(rows), [dict(r) for r in rows]


async def check_2_relay_snapshot(db_path: str):
    print("\n" + "=" * 80)
    print("CHECK 2: Request fresh snapshot from Relay 1")
    print("=" * 80)
    relay = UdpWebsocketRelay(
        ws_port=8081,
        udp_bind_port=14650,
        gfs_db_path=db_path,
        relay_ack_db="data/relay_acks.db"
    )

    class MockWs:
        def __init__(self):
            self.sent_msgs = []
        async def send(self, data):
            self.sent_msgs.append(json.loads(data))

    ws = MockWs()
    await relay._handle_gfs_snapshot_request(ws, {"type": "gfs_snapshot_request", "request_id": "diag-snap-1"})

    if not ws.sent_msgs:
        print("Relay returned NO response.")
        return 0, []

    resp = ws.sent_msgs[0]
    objects = resp.get("objects", [])
    tombstones = resp.get("tombstones", {})
    print(f"Relay 1 snapshot response: {len(objects)} active tracks, {len(tombstones)} tombstones")
    for o in objects:
        print(f"  - [{o.get('lifecycle_state')}] {o.get('global_id') or o.get('id')}: conf={o.get('confidence')}, lat={o.get('lat')}, lon={o.get('lon')}")
    for tid, t in tombstones.items():
        print(f"  - [TOMBSTONE] {tid}: retiredAt={t.get('retiredAt')}, ver={t.get('version')}")

    return len(objects), objects


def check_3_phoneos_state(snapshot_objects):
    print("\n" + "=" * 80)
    print("CHECK 3: Inspect PhoneOS state after snapshot reconciliation")
    print("=" * 80)
    node_exe = shutil.which("node")
    if not node_exe:
        print("ERROR: node not found.")
        return 0, {}

    node_script = f"""
    import {{ reconcileSnapshot, getConfirmedGarbage, getProvisionalGarbage }} from './mobile/src/utils/garbage.js';
    const snapshotObjects = {json.dumps(snapshot_objects)};
    const tombstones = {{}};
    const reconciled = reconcileSnapshot({{}}, snapshotObjects, tombstones, {{}}, 0, new Set());
    const confirmed = getConfirmedGarbage(reconciled);
    const provisional = getProvisionalGarbage(reconciled);
    console.log(JSON.stringify({{
        total: Object.keys(reconciled).length,
        confirmedCount: Object.keys(confirmed).length,
        provisionalCount: Object.keys(provisional).length,
        items: reconciled
    }}));
    """
    proc = subprocess.run([node_exe, "--input-type=module", "-e", node_script], capture_output=True, text=True)
    if proc.returncode != 0:
        print("Node error:", proc.stderr)
        return 0, {}

    res = json.loads(proc.stdout)
    print(f"PhoneOS stored tracks after reconcileSnapshot: {res['total']}")
    print(f"  - CONFIRMED count:   {res['confirmedCount']}")
    print(f"  - PROVISIONAL count: {res['provisionalCount']}")
    for mid, item in res["items"].items():
        print(f"  - [{item.get('lifecycle_state')}] {mid}: conf={item.get('confidence')}, hits={item.get('hits')}, lat={item.get('lat')}, lon={item.get('lon')}")

    return res['total'], res


def check_4_track_ids_and_merging(db_path: str):
    print("\n" + "=" * 80)
    print("CHECK 4: Check whether the 5 targets have distinct track IDs or are being merged")
    print("=" * 80)
    if not Path(db_path).exists():
        return

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT count(1) FROM inbox_packet_items")
    item_count = cur.fetchone()[0]

    cur.execute("SELECT obs_id, disposition, rejection_reason, target_global_id, raw_payload FROM inbox_packet_items")
    items = cur.fetchall()

    cur.execute("SELECT * FROM global_track_identity_history")
    history = cur.fetchall()

    print(f"Total inbox observation items: {len(items)}")
    unique_obs_ids = set()
    local_tracks = set()
    drones = set()

    for r in items:
        unique_obs_ids.add(r['obs_id'])
        try:
            payload = json.loads(r['raw_payload'])
            local_tracks.add(payload.get('local_track_id') or payload.get('id'))
            drones.add(payload.get('drone_id'))
        except Exception:
            pass

    print(f"Unique obs_ids in inbox: {len(unique_obs_ids)}")
    print(f"Unique local_track_ids: {len(local_tracks)} -> {sorted(list(local_tracks))}")
    print(f"Reporting drones: {sorted(list(drones))}")
    print(f"Identity history bindings: {len(history)}")
    for h in history:
        print(f"  - {h['drone_id']}:{h['local_track_id']} -> {h['global_id']}")

    con.close()


async def main():
    db_path = "data/gfs.db"
    c1, active_rows = check_1_gfs_sqlite(db_path)
    c2, snap_objs = await check_2_relay_snapshot(db_path)
    c3, phone_state = check_3_phoneos_state(snap_objs)
    check_4_track_ids_and_merging(db_path)


if __name__ == "__main__":
    asyncio.run(main())
