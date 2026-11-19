"""
End-to-end verification script for Relay-PhoneOS WebSocket Stability & Garbage Marker Persistence.
Verifies:
1. Four concurrent Relay instances on ports 8081-8084 with allow_duplicate_ips=True.
2. Concurrent client connections from 127.0.0.1 do NOT disconnect or evict each other.
3. Stable heartbeat and telemetry transmission over UDP into each relay.
4. GFS snapshot request / response correlation with request_id.
5. Multi-drone detection event buffering and persistence across snapshot reconciliation.
6. Five targets (1 confirmed, 4 provisional) stably maintained without disappearing.
"""

import asyncio
import json
import time
import uuid
import sqlite3
import subprocess
import sys
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from relay.relay import UdpWebsocketRelay, init_relay_ack_db
from gfs.database import init_db, get_db
import websockets


async def run_stability_verification():
    print("=" * 80)
    print("FOUR-RELAY PHONEOS WEBSOCKET STABILITY & GARBAGE PROPAGATION VERIFICATION")
    print("=" * 80)

    test_dir = REPO_ROOT / "scratch" / "relay_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    gfs_db = str(test_dir / "sim_gfs.db")
    relay_ack_db = str(test_dir / "sim_relay_acks.db")

    init_db(gfs_db)
    init_relay_ack_db(relay_ack_db)

    # Seed GFS with 5 known targets (1 confirmed, 4 provisional)
    targets = [
        {"id": "G-TARGET-1", "conf": 0.85, "state": "CONFIRMED", "lat": 47.64150, "lon": -122.14020, "sources": ["drone1", "drone2"]},
        {"id": "G-TARGET-2", "conf": 0.62, "state": "PROVISIONAL", "lat": 47.64155, "lon": -122.14015, "sources": ["drone1"]},
        {"id": "G-TARGET-3", "conf": 0.59, "state": "PROVISIONAL", "lat": 47.64160, "lon": -122.14010, "sources": ["drone2"]},
        {"id": "G-TARGET-4", "conf": 0.61, "state": "PROVISIONAL", "lat": 47.64165, "lon": -122.14005, "sources": ["drone3"]},
        {"id": "G-TARGET-5", "conf": 0.60, "state": "PROVISIONAL", "lat": 47.64170, "lon": -122.14000, "sources": ["drone4"]},
    ]

    with get_db(gfs_db) as con:
        cur = con.cursor()
        for t in targets:
            cur.execute("""
                INSERT INTO global_objects (
                    global_id, label, status, lat, lon, alt, east_m, north_m,
                    cov_xx, cov_yy, cov_xy, confidence, first_seen_ms, last_seen_ms,
                    observation_count, source_count, source_estimates_json, version,
                    lifecycle_state, confirmation_method, provisional_reason
                ) VALUES (?, 'rubbish', 'ACTIVE', ?, ?, 0.0, 0.0, 0.0,
                          0.2, 0.2, 0.0, ?, ?, ?, 2, ?, ?, 1,
                          ?, ?, ?)
            """, (
                t["id"], t["lat"], t["lon"], t["conf"],
                int(time.time() * 1000) - 5000, int(time.time() * 1000),
                len(t["sources"]), json.dumps({s: [t["lat"], t["lon"]] for s in t["sources"]}),
                t["state"],
                "MULTI_DRONE_AGREEMENT" if t["state"] == "CONFIRMED" else None,
                "SINGLE_DRONE_LOW_CONFIDENCE" if t["state"] == "PROVISIONAL" else None
            ))

    print(f"[Stage 1] Seeded GFS SQLite with {len(targets)} objects (1 CONFIRMED, 4 PROVISIONAL).")

    # Start 4 Relays (ports 8081-8084)
    relays = []
    tasks = []
    relay_ports = [8081, 8082, 8083, 8084]
    udp_bind_ports = [14650, 14651, 14652, 14653]

    for ws_p, udp_p in zip(relay_ports, udp_bind_ports):
        r = UdpWebsocketRelay(
            ws_host="127.0.0.1",
            ws_port=ws_p,
            udp_bind_port=udp_p,
            gfs_db_path=gfs_db,
            relay_ack_db=relay_ack_db,
            allow_duplicate_ips=True,
            ws_send_timeout=3.0
        )
        relays.append(r)
        tasks.append(asyncio.create_task(r.start()))

    await asyncio.sleep(0.5)
    print(f"[Stage 2] Started 4 relays on WS ports {relay_ports}.")

    # Connect clients simulating PhoneOS MultiWebSocketManager
    # Connect 2 clients to 8081 (simulating duplicate connection or React StrictMode)
    # and 1 client to each of 8082, 8083, 8084
    client_connections = []
    try:
        c1a = await websockets.connect("ws://127.0.0.1:8081")
        c1b = await websockets.connect("ws://127.0.0.1:8081")
        c2 = await websockets.connect("ws://127.0.0.1:8082")
        c3 = await websockets.connect("ws://127.0.0.1:8083")
        c4 = await websockets.connect("ws://127.0.0.1:8084")
        client_connections = [c1a, c1b, c2, c3, c4]

        await asyncio.sleep(0.5)

        # Verify no connection was disconnected
        for idx, c in enumerate(client_connections):
            assert c.close_code is None, f"Client {idx} closed unexpectedly with code {c.close_code}!"
        print(f"[Stage 3] PASS: 5 concurrent connections from 127.0.0.1 established across 4 relays without eviction!")

        # Test Snapshot Request with request_id correlation on Relay 1
        req_id = f"snap-{int(time.time()*1000)}-test"
        await c1a.send(json.dumps({
            "type": "gfs_snapshot_request",
            "request_id": req_id,
            "timestamp": int(time.time() * 1000)
        }))

        raw_snap = await asyncio.wait_for(c1a.recv(), timeout=2.0)
        snap = json.loads(raw_snap)
        assert snap.get("type") == "gfs_snapshot_response"
        assert snap.get("request_id") == req_id
        assert len(snap.get("objects", [])) == 5
        print(f"[Stage 4] PASS: Relay 1 served GFS snapshot with request_id={req_id}, containing 5 global objects.")

        # Test UDP Forwarding of Heartbeat and Detection into Relays
        import socket
        udp_client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for i, (ws_c, udp_p) in enumerate(zip([c1a, c2, c3, c4], udp_bind_ports)):
            drone_id = f"drone{i+1}"
            hb = {
                "msg_type": "heartbeat",
                "sender_id": drone_id,
                "timestamp": time.time(),
                "status": "active"
            }
            udp_client.sendto(json.dumps(hb).encode("utf-8"), ("127.0.0.1", udp_p))

            raw_hb = await asyncio.wait_for(ws_c.recv(), timeout=2.0)
            hb_recv = json.loads(raw_hb)
            assert hb_recv.get("sender_id") == drone_id
            assert hb_recv.get("msg_type") == "heartbeat"

        udp_client.close()
        print(f"[Stage 5] PASS: Drone heartbeats for drone1-drone4 forwarded cleanly through each relay.")

        # Now test PhoneOS reconciliation using Node script to verify exact garbage.js merge
        node_script = """
import { reconcileSnapshot, mergeDetections } from './mobile/src/utils/garbage.js';
import fs from 'fs';

const snapData = JSON.parse(fs.readFileSync(0, 'utf-8'));
const tombstones = {};
const reconciled = reconcileSnapshot({}, snapData.objects, tombstones, snapData.tombstones, snapData.watermark_event_id, new Set());

const keys = Object.keys(reconciled);
if (keys.length !== 5) {
    throw new Error(`Expected 5 objects in reconciled snapshot, got ${keys.length}`);
}

const confirmed = keys.filter(k => reconciled[k].lifecycle_state === 'CONFIRMED');
const provisional = keys.filter(k => reconciled[k].lifecycle_state === 'PROVISIONAL');

if (confirmed.length !== 1) throw new Error(`Expected 1 confirmed object, got ${confirmed.length}`);
if (provisional.length !== 4) throw new Error(`Expected 4 provisional objects, got ${provisional.length}`);

// Buffer in-flight detection while snapshot in-flight
const liveDet = [{
    id: 'G-TARGET-1',
    confidence: 0.90,
    lat: 47.64150,
    lon: -122.14020,
    hits: 5,
    lifecycle_state: 'CONFIRMED'
}];
const afterMerge = mergeDetections(reconciled, liveDet, 'drone1', Date.now(), new Set(), false, tombstones);

if (Object.keys(afterMerge).length !== 5) {
    throw new Error(`Expected 5 objects after merge, got ${Object.keys(afterMerge).length}`);
}

console.log(JSON.stringify({
    success: true,
    total: keys.length,
    confirmed: confirmed.length,
    provisional: provisional.length
}));
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            input=json.dumps(snap),
            text=True,
            capture_output=True
        )
        assert proc.returncode == 0, f"Node script error: {proc.stderr}"
        res = json.loads(proc.stdout.strip())
        assert res.get("success") is True
        print(f"[Stage 6] PASS: PhoneOS garbage reconciliation confirmed: {res['confirmed']} CONFIRMED, {res['provisional']} PROVISIONAL. Total: {res['total']}.")

        print("\n" + "=" * 80)
        print("ALL VERIFICATION STAGES PASSED CLEANLY!")
        print("=" * 80)

    finally:
        for c in client_connections:
            try:
                await c.close()
            except Exception:
                pass
        for t in tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    asyncio.run(run_stability_verification())
