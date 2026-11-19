import asyncio
import json
import pytest
import websockets
from relay.relay import UdpWebsocketRelay, init_relay_ack_db
from gfs.database import init_db


@pytest.mark.asyncio
async def test_relay_duplicate_ip_coexistence(tmp_path):
    """Verify that multiple WebSocket connections from 127.0.0.1 coexist without evicting each other."""
    db_file = str(tmp_path / "test_gfs.db")
    ack_db_file = str(tmp_path / "test_relay_ack.db")
    init_db(db_file)
    init_relay_ack_db(ack_db_file)

    relay = UdpWebsocketRelay(
        ws_host="127.0.0.1",
        ws_port=8191,
        udp_bind_port=14791,
        gfs_db_path=db_file,
        relay_ack_db=ack_db_file,
        allow_duplicate_ips=True
    )
    server_task = asyncio.create_task(relay.start())
    await asyncio.sleep(0.3)

    try:
        # Client 1 connects
        ws1 = await websockets.connect("ws://127.0.0.1:8191")
        # Client 2 connects from same IP
        ws2 = await websockets.connect("ws://127.0.0.1:8191")

        # Give the relay event loop time to process
        await asyncio.sleep(0.2)

        # Both connections must be open and active
        assert ws1.close_code is None, f"Client 1 was closed unexpectedly with code {ws1.close_code}!"
        assert ws2.close_code is None, f"Client 2 was closed unexpectedly with code {ws2.close_code}!"
        assert len(relay.clients) == 2, f"Expected 2 active clients, got {len(relay.clients)}"

        # Close clients cleanly
        await ws1.close()
        await ws2.close()
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_relay_snapshot_request_id_echo(tmp_path):
    """Verify that gfs_snapshot_request echoes back request_id in gfs_snapshot_response."""
    db_file = str(tmp_path / "test_gfs_echo.db")
    ack_db_file = str(tmp_path / "test_relay_ack_echo.db")
    init_db(db_file)
    init_relay_ack_db(ack_db_file)

    relay = UdpWebsocketRelay(
        ws_host="127.0.0.1",
        ws_port=8192,
        udp_bind_port=14792,
        gfs_db_path=db_file,
        relay_ack_db=ack_db_file,
        allow_duplicate_ips=True
    )
    server_task = asyncio.create_task(relay.start())
    await asyncio.sleep(0.3)

    try:
        ws = await websockets.connect("ws://127.0.0.1:8192")
        req_id = "test-snap-req-12345"
        await ws.send(json.dumps({
            "type": "gfs_snapshot_request",
            "request_id": req_id,
            "timestamp": 123456789
        }))

        raw_resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
        resp = json.loads(raw_resp)

        assert resp.get("type") == "gfs_snapshot_response"
        assert resp.get("request_id") == req_id, f"Expected request_id {req_id}, got {resp.get('request_id')}"
        assert "objects" in resp
        assert "tombstones" in resp

        await ws.close()
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
