import asyncio
import socket
import json
import websockets
import logging
import argparse
import hashlib
import hmac
import os
import time

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PhoneOS_Relay")

import sqlite3
from pathlib import Path

try:
    from DroneOS.perception.diagnostics import log_relay_fwd
except ImportError:
    log_relay_fwd = None

def init_relay_ack_db(db_path: str):
    """Initializes the durable SQLite ledger for relay ACK persistence."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=5.0)
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA synchronous = NORMAL;")
        con.execute("""
            CREATE TABLE IF NOT EXISTS relay_ack_outbox (
                ack_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                delivery_attempt_id TEXT NOT NULL,
                gcs_client_id TEXT NOT NULL,
                ack_payload TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('RECEIVED', 'FORWARDED', 'CONFIRMED')),
                received_at_ms INTEGER NOT NULL,
                forward_attempt_count INTEGER NOT NULL DEFAULT 0,
                last_forwarded_at_ms INTEGER
            );
        """)
        con.commit()
    finally:
        con.close()

class UdpWebsocketRelay:
    def __init__(self, ws_host="0.0.0.0", ws_port=8080, udp_bind_host="0.0.0.0", udp_bind_port=14551, udp_target_port=14550, udp_broadcast_addr="255.255.255.255", udp_target_host=None, gs_heartbeat_interval=1.0, udp_target_endpoints=None, gfs_ack_host="127.0.0.1", gfs_ack_port=41459, relay_ack_db="data/relay_acks.db", gfs_db_path="data/gfs.db", allow_duplicate_ips=True, ws_send_timeout=3.0):
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.udp_bind_host = udp_bind_host
        self.udp_bind_port = udp_bind_port
        self.udp_target_port = udp_target_port
        self.udp_broadcast_addr = udp_broadcast_addr
        # None preserves production LAN broadcast.  Simulation supplies a
        # concrete loopback address so a relay reaches only its own DroneOS.
        self.udp_target_host = udp_target_host
        # Optional static routes let a simulation relay deliver a targeted
        # command even when the GCS is connected to a different drone's relay.
        self.udp_target_endpoints = {
            str(drone_id).strip().lower(): endpoint
            for drone_id, endpoint in (udp_target_endpoints or {}).items()
        }
        self.gs_heartbeat_interval = gs_heartbeat_interval
        self.auth_token = os.getenv("RELAY_AUTH_TOKEN")
        self.net_secret = os.getenv("DRONE_NET_SECRET")
        self.trace_network = os.getenv("DRONEOS_DIAGNOSTIC_TRACE", "").lower() in {"1", "true", "yes", "on"}
        self.allow_duplicate_ips = allow_duplicate_ips
        self.ws_send_timeout = ws_send_timeout

        self.clients = set()
        self.known_endpoints = {} # Target ID to address tuple

        # We need a reference to the loop
        self.loop = None
        self.transport = None
        self.protocol = None
        self._heartbeat_task = None
        self._ack_recovery_task = None
        self.gfs_ack_host = gfs_ack_host
        self.gfs_ack_port = gfs_ack_port
        self.relay_ack_db = relay_ack_db
        self.gfs_db_path = gfs_db_path
        try:
            init_relay_ack_db(self.relay_ack_db)
        except Exception as e:
            logger.warning(f"Could not init relay ack db: {e}")

    def _signature_payload(self, msg_dict: dict) -> bytes:
        payload = dict(msg_dict)
        payload.pop("hmac_sig", None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _sign_message_dict(self, msg_dict: dict) -> dict:
        if not self.net_secret:
            return msg_dict
        signed = dict(msg_dict)
        signed["hmac_sig"] = hmac.new(
            self.net_secret.encode("utf-8"),
            self._signature_payload(signed),
            hashlib.sha256
        ).hexdigest()
        return signed

    def _verify_message_dict(self, msg_dict: dict, addr: tuple) -> bool:
        if not self.net_secret:
            return True
        received_sig = msg_dict.get("hmac_sig")
        if not received_sig:
            logger.warning(f"UDP packet dropped: Missing HMAC signature from {addr}")
            return False
        expected_sig = hmac.new(
            self.net_secret.encode("utf-8"),
            self._signature_payload(msg_dict),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(received_sig, expected_sig):
            logger.warning(f"UDP packet dropped: Invalid HMAC signature from {addr}")
            return False
        return True

    async def _authenticate_websocket(self, websocket) -> bool:
        if not self.auth_token:
            return True
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            msg = json.loads(raw) if isinstance(raw, str) else {}
        except asyncio.TimeoutError:
            logger.warning(f"WebSocket auth timed out from {websocket.remote_address}")
            await websocket.close(code=4001, reason="Relay authentication timed out")
            return False
        except Exception as e:
            logger.warning(f"WebSocket auth failed from {websocket.remote_address}: {e}")
            await websocket.close(code=4001, reason="Relay authentication failed")
            return False

        if msg.get("type") != "AUTH" or msg.get("token") != self.auth_token:
            logger.warning(f"WebSocket auth rejected from {websocket.remote_address}")
            await websocket.close(code=4001, reason="Relay authentication rejected")
            return False
        logger.info(f"WebSocket client authenticated from {websocket.remote_address}")
        try:
            await websocket.send(json.dumps({"type": "AUTH_ACK", "status": "ok"}))
        except Exception:
            pass
        return True

    class UdpProtocol(asyncio.DatagramProtocol):
        def __init__(self, relay):
            self.relay = relay

        def connection_made(self, transport):
            self.transport = transport
            logger.info("UDP socket bound and ready.")

        def datagram_received(self, data, addr):
            # Pass to asyncio loop to handle forwarding
            if self.relay.loop and self.relay.loop.is_running():
                self.relay.loop.create_task(self.relay.forward_udp_to_ws(data, addr))

    async def forward_udp_to_ws(self, data: bytes, addr: tuple):
        if not data: return

        # MAVLink 1 is 0xFE, MAVLink 2 is 0xFD
        if data[0] in (0xFE, 0xFD):
            return

        if data[0] not in (ord('{'), ord('[')):
            logger.warning(f"UDP packet dropped: Expected JSON but got invalid byte {data[0]} from {addr}")
            return

        try:
            msg_str = data.decode('utf-8')
            msg_dict = json.loads(msg_str)
            if not self._verify_message_dict(msg_dict, addr):
                return
            sender_id = msg_dict.get('sender_id')

            # Prevent WS loopback of our own ground station commands
            if sender_id and sender_id.startswith('gs'):
                return

            # Auto-learn peer IP for targeted unicast replies from WS
            if sender_id:
                if sender_id not in self.known_endpoints or self.known_endpoints[sender_id] != addr:
                    logger.debug(f"Learned UDP endpoint for {sender_id}: {addr}")
                self.known_endpoints[sender_id] = addr

            # Check for GFS ACK confirmation datagram
            if isinstance(msg_dict, dict) and msg_dict.get('type') == 'gfs_ack_confirmed':
                self._handle_gfs_ack_confirmed(msg_dict)
                return

            # Broadcast to all connected WebSockets
            if self.clients:
                m_type = msg_dict.get('msg_type') or msg_dict.get('type')
                if log_relay_fwd:
                    if m_type == 'detection':
                        log_relay_fwd("RELAY_DETECTION_FWD", self.ws_port, {
                            "sender_id": sender_id,
                            "detection_ids": [d.get("id") for d in msg_dict.get("detections", [])],
                            "forwarded_to_clients": len(self.clients)
                        })
                    elif m_type == 'gfs_outbox_event':
                        log_relay_fwd("RELAY_GFS_EVENT_FWD", self.ws_port, {
                            "global_id": msg_dict.get("global_id"),
                            "status": msg_dict.get("status"),
                            "forwarded_to_clients": len(self.clients)
                        })

                # A slow or abandoned app connection must not hold up the
                # healthy GCS client.  Bound each send and evict only the
                # failed client; this matters when several simulated relays
                # share one Windows host.
                clients = tuple(self.clients)
                results = await asyncio.gather(
                    *(asyncio.wait_for(ws.send(msg_str), timeout=self.ws_send_timeout) for ws in clients),
                    return_exceptions=True,
                )
                for ws, result in zip(clients, results):
                    if isinstance(result, Exception):
                        logger.warning(f"Dropping stalled WebSocket client {ws.remote_address}: {result}")
                        self.clients.discard(ws)
                        try:
                            await ws.close()
                        except Exception:
                            pass

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON received from UDP: {e}")
        except Exception as e:
            logger.warning(f"Error forwarding UDP to WS: {e}")

    async def ws_handler(self, websocket):
        logger.info(f"New WebSocket client connected from {websocket.remote_address}")

        if not await self._authenticate_websocket(websocket):
            return

        # Prevent duplicate WS connections from the same IP (unless explicitly allowed)
        if not self.allow_duplicate_ips:
            ip = websocket.remote_address[0]
            to_remove = [ws for ws in self.clients if ws.remote_address[0] == ip]
            for ws in to_remove:
                logger.info(f"Closing duplicate WebSocket from {ip}")
                try:
                    await ws.close()
                except:
                    pass
                self.clients.discard(ws)

        self.clients.add(websocket)
        try:
            async for message in websocket:
                if isinstance(message, str):
                    await self.forward_ws_to_udp(message, websocket=websocket)
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"WebSocket client disconnected: {websocket.remote_address} (code={e.code}, reason={e.reason})")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.clients.discard(websocket)

    async def forward_ws_to_udp(self, message: str, websocket=None):
        if not self.transport:
            return

        try:
            msg_dict = json.loads(message)
            if isinstance(msg_dict, dict) and msg_dict.get('type') == 'gfs_outbox_ack':
                await self._handle_gfs_outbox_ack(msg_dict)
                return
            if isinstance(msg_dict, dict) and msg_dict.get('type') == 'gfs_snapshot_request':
                await self._handle_gfs_snapshot_request(websocket, msg_dict)
                return
            if isinstance(msg_dict, dict) and msg_dict.get('type') == 'gfs_clear_request':
                await self._handle_gfs_clear_request(websocket, msg_dict)
                return
            target_id = msg_dict.get('target_id')
            route_target_id = target_id.strip().lower() if isinstance(target_id, str) else target_id
            data = json.dumps(self._sign_message_dict(msg_dict)).encode('utf-8')
            if self.trace_network:
                logger.info(f"TRACE WS_RX ws={self.ws_port} target={target_id} type={msg_dict.get('msg_type')} action={msg_dict.get('action')} cmd_id={msg_dict.get('cmd_id')}")

            if route_target_id == "all" and self.udp_target_endpoints:
                # Send one copy to every configured simulation DroneOS node.
                # DroneOS de-duplicates by cmd_id when the GCS has multiple
                # relay connections open.
                for addr in dict.fromkeys(self.udp_target_endpoints.values()):
                    self.transport.sendto(data, addr)
                    if self.trace_network:
                        logger.info(f"TRACE UDP_TX udp={self.udp_bind_port} to={addr} route=configured-all")
                logger.debug(f"Forwarded WS msg ({msg_dict.get('msg_type')}) to all configured endpoints")
            elif route_target_id and (route_target_id in self.known_endpoints or route_target_id in self.udp_target_endpoints):
                # Prefer the live endpoint learned from telemetry, then use a
                # deterministic simulation route if this relay has not yet
                # observed that drone.
                addr = self.known_endpoints.get(route_target_id) or self.udp_target_endpoints[route_target_id]
                self.transport.sendto(data, addr)
                route = "learned-unicast" if route_target_id in self.known_endpoints else "configured-unicast"
                if self.trace_network:
                    logger.info(f"TRACE UDP_TX udp={self.udp_bind_port} to={addr} route={route}")
                logger.debug(f"Forwarded WS msg ({msg_dict.get('msg_type')}) via Unicast to {addr}")
            else:
                # Broadcast
                addr = (self.udp_target_host or self.udp_broadcast_addr, self.udp_target_port)
                self.transport.sendto(data, addr)
                if self.trace_network:
                    logger.info(f"TRACE UDP_TX udp={self.udp_bind_port} to={addr} route=configured-target")
                logger.debug(f"Forwarded WS msg ({msg_dict.get('msg_type')}) via Broadcast to {addr}")

        except json.JSONDecodeError:
            logger.warning("Received invalid JSON from WS, dropping.")
        except Exception as e:
            logger.error(f"Error forwarding WS to UDP: {e}")

    async def _send_relay_groundstation_heartbeat(self) -> bool:
        if not self.transport or not self.clients:
            return False

        msg = {
            "msg_type": "heartbeat",
            "sender_id": "gs_relay",
            "timestamp": time.time(),
            "target_id": None,
            "status": "active",
        }
        data = json.dumps(self._sign_message_dict(msg)).encode("utf-8")
        self.transport.sendto(data, (self.udp_target_host or self.udp_broadcast_addr, self.udp_target_port))
        return True

    async def _relay_groundstation_heartbeat_loop(self):
        while True:
            try:
                if self.clients:
                    await self._send_relay_groundstation_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Error publishing relay ground-station heartbeat: {e}")
            await asyncio.sleep(self.gs_heartbeat_interval)


    async def _handle_gfs_outbox_ack(self, msg_dict: dict):
        """Persists GCS ACK into durable SQLite ledger and forwards UDP datagram to GFS."""
        ack_id = msg_dict.get("ack_id")
        event_id = msg_dict.get("event_id")
        delivery_attempt_id = msg_dict.get("delivery_attempt_id")
        gcs_client_id = msg_dict.get("gcs_client_id")
        now_ms = int(time.time() * 1000)

        if not ack_id or not event_id:
            logger.warning("Dropping invalid GFS ACK: missing ack_id or event_id")
            return

        payload_str = json.dumps(msg_dict)
        # 1. Durable write before forward
        try:
            con = sqlite3.connect(self.relay_ack_db, timeout=5.0)
            with con:
                con.execute(
                    """INSERT OR IGNORE INTO relay_ack_outbox
                       (ack_id, event_id, delivery_attempt_id, gcs_client_id, ack_payload, status, received_at_ms)
                       VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?)""",
                    (ack_id, event_id, delivery_attempt_id, gcs_client_id, payload_str, now_ms)
                )
            con.close()
        except Exception as e:
            logger.error(f"Error persisting ACK to relay ledger: {e}")

        # 2. Forward UDP datagram to GFS
        if self.transport:
            dest = (self.gfs_ack_host, self.gfs_ack_port)
            self.transport.sendto(payload_str.encode('utf-8'), dest)
            try:
                con = sqlite3.connect(self.relay_ack_db, timeout=5.0)
                with con:
                    con.execute(
                        """UPDATE relay_ack_outbox
                           SET status = 'FORWARDED', forward_attempt_count = forward_attempt_count + 1, last_forwarded_at_ms = ?
                           WHERE ack_id = ?""",
                        (now_ms, ack_id)
                    )
                con.close()
            except Exception as e:
                logger.error(f"Error updating forwarded ACK in relay ledger: {e}")

    def _handle_gfs_ack_confirmed(self, msg_dict: dict):
        """Processes GFS ACK confirmation datagram, transitioning status to CONFIRMED."""
        ack_id = msg_dict.get("ack_id")
        if not ack_id:
            return
        try:
            con = sqlite3.connect(self.relay_ack_db, timeout=5.0)
            with con:
                con.execute("UPDATE relay_ack_outbox SET status = 'CONFIRMED' WHERE ack_id = ?", (ack_id,))
            con.close()
            logger.info(f"Relay ACK confirmed by GFS: {ack_id}")
        except Exception as e:
            logger.error(f"Error marking ACK confirmed: {e}")

    async def _handle_gfs_snapshot_request(self, websocket, msg_dict: dict):
        """Authoritative GFS snapshot recovery for GCS clients (Contract B)."""
        now_ms = int(time.time() * 1000)
        request_id = msg_dict.get("request_id") if isinstance(msg_dict, dict) else None
        objects = []
        tombstones = {}
        watermark_event_id = 0
        snapshot_revision = 0
        if self.gfs_db_path and Path(self.gfs_db_path).exists():
            try:
                con = sqlite3.connect(self.gfs_db_path, timeout=5.0)
                con.row_factory = sqlite3.Row
                cur = con.cursor()
                cur.execute("""
                    SELECT global_id, label, status, lat, lon, alt, confidence,
                           observation_count, source_count, version, first_seen_ms, last_seen_ms,
                           source_estimates_json, lifecycle_state, confirmation_method, provisional_reason
                    FROM global_objects
                """)
                for row in cur.fetchall():
                    ver = int(row["version"] or 0)
                    if ver > snapshot_revision:
                        snapshot_revision = ver

                    col_keys = row.keys()
                    src_drones = []
                    if "source_estimates_json" in col_keys and row["source_estimates_json"]:
                        try:
                            src_drones = list(json.loads(row["source_estimates_json"]).keys())
                        except Exception:
                            pass

                    l_state = row["lifecycle_state"] if "lifecycle_state" in col_keys and row["lifecycle_state"] else ("CONFIRMED" if row["status"] == "ACTIVE" else "RETIRED")
                    conf_method = row["confirmation_method"] if "confirmation_method" in col_keys else None
                    prov_reason = row["provisional_reason"] if "provisional_reason" in col_keys else None

                    if row["status"] == "ACTIVE":
                        objects.append({
                            "id": row["global_id"],
                            "global_id": row["global_id"],
                            "label": row["label"],
                            "status": row["status"],
                            "lifecycle_state": l_state,
                            "confirmation_method": conf_method,
                            "provisional_reason": prov_reason,
                            "lat": row["lat"],
                            "lon": row["lon"],
                            "alt": row["alt"],
                            "confidence": row["confidence"],
                            "observation_count": row["observation_count"],
                            "source_count": row["source_count"],
                            "source_drones": src_drones,
                            "version": ver,
                            "first_seen_ms": row["first_seen_ms"],
                            "last_seen_ms": row["last_seen_ms"],
                        })
                    elif row["status"] == "RETIRED":
                        tombstones[row["global_id"]] = {
                            "retiredAt": row["last_seen_ms"] or now_ms,
                            "version": ver
                        }

                try:
                    cur.execute("SELECT COALESCE(MAX(id), 0) FROM gfs_outbox_events")
                    row_w = cur.fetchone()
                    if row_w:
                        watermark_event_id = int(row_w[0] or 0)
                except Exception:
                    pass

                con.close()
            except Exception as e:
                logger.error(f"Error querying GFS snapshot: {e}")

        response = {
            "type": "gfs_snapshot_response",
            "msg_type": "gfs_snapshot_response",
            "request_id": request_id,
            "snapshot_revision": snapshot_revision,
            "watermark_event_id": watermark_event_id,
            "timestamp_ms": now_ms,
            "objects": objects,
            "tombstones": tombstones
        }
        if websocket:
            try:
                await websocket.send(json.dumps(response))
                logger.info(f"Served GFS snapshot response ({len(objects)} active, {len(tombstones)} tombstones, watermark={watermark_event_id}) to {getattr(websocket, 'remote_address', 'client')}")
                if log_relay_fwd:
                    log_relay_fwd("RELAY_GFS_SNAPSHOT_FWD", self.ws_port, {
                        "active_objects_count": len(objects),
                        "tombstones_count": len(tombstones),
                        "forwarded_to_clients": 1
                    })
            except Exception as e:
                logger.warning(f"Failed to send snapshot response to client: {e}")

    async def _handle_gfs_clear_request(self, websocket, msg_dict: dict):
        """Authoritative GFS clear request: clears all global objects and outbox events."""
        if self.gfs_db_path and Path(self.gfs_db_path).exists():
            try:
                con = sqlite3.connect(self.gfs_db_path, timeout=5.0)
                cur = con.cursor()
                cur.execute("DELETE FROM global_objects")
                cur.execute("DELETE FROM gfs_outbox_events")
                con.commit()
                con.close()
                logger.info("Cleared GFS database upon client request.")
            except Exception as e:
                logger.error(f"Error clearing GFS database: {e}")

        # Broadcast empty snapshot response to all connected clients
        now_ms = int(time.time() * 1000)
        clear_snapshot = {
            "type": "gfs_snapshot_response",
            "msg_type": "gfs_snapshot_response",
            "snapshot_revision": now_ms,
            "watermark_event_id": 0,
            "timestamp_ms": now_ms,
            "objects": [],
            "tombstones": {}
        }
        await self.broadcast_to_websockets(clear_snapshot)

    async def _relay_ack_recovery_loop(self):
        """Periodically recovers and re-forwards unconfirmed ACKs to GFS."""
        while True:
            try:
                await asyncio.sleep(3.0)
                if not self.transport:
                    continue
                now_ms = int(time.time() * 1000)
                con = sqlite3.connect(self.relay_ack_db, timeout=5.0)
                cur = con.cursor()
                cur.execute(
                    """SELECT ack_id, ack_payload FROM relay_ack_outbox
                       WHERE status IN ('RECEIVED', 'FORWARDED')
                         AND (last_forwarded_at_ms IS NULL OR last_forwarded_at_ms < ?)
                       LIMIT 20""",
                    (now_ms - 2500,)
                )
                rows = cur.fetchall()
                for r in rows:
                    ack_id, payload_str = r[0], r[1]
                    dest = (self.gfs_ack_host, self.gfs_ack_port)
                    self.transport.sendto(payload_str.encode('utf-8'), dest)
                    con.execute(
                        """UPDATE relay_ack_outbox
                           SET forward_attempt_count = forward_attempt_count + 1, last_forwarded_at_ms = ?
                           WHERE ack_id = ?""",
                        (now_ms, ack_id)
                    )
                con.commit()
                con.close()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in relay ACK recovery loop: {e}")

    async def start(self):
        self.loop = asyncio.get_running_loop()

        # Setup UDP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if os.name == 'nt':
            try:
                import ctypes
                SIO_UDP_CONNRESET = 0x9800000C
                in_buffer = ctypes.c_uint32(0)
                bytes_returned = ctypes.c_uint32(0)
                ws2_32 = ctypes.windll.ws2_32
                result = ws2_32.WSAIoctl(
                    sock.fileno(),
                    SIO_UDP_CONNRESET,
                    ctypes.byref(in_buffer),
                    ctypes.sizeof(in_buffer),
                    None,
                    0,
                    ctypes.byref(bytes_returned),
                    None,
                    None
                )
                if result != 0:
                    logger.warning(f"WSAIoctl SIO_UDP_CONNRESET failed: {ws2_32.WSAGetLastError()}")
            except Exception as e:
                logger.warning(f"Could not disable SIO_UDP_CONNRESET: {e}")
        try:
            sock.bind((self.udp_bind_host, self.udp_bind_port))
        except OSError as e:
            logger.error(f"Failed to bind UDP to {self.udp_bind_host}:{self.udp_bind_port}: {e}")
            raise

        self.transport, self.protocol = await self.loop.create_datagram_endpoint(
            lambda: self.UdpProtocol(self),
            sock=sock
        )
        logger.info(f"Relay listening for UDP on {self.udp_bind_host}:{self.udp_bind_port}")
        self._heartbeat_task = asyncio.create_task(self._relay_groundstation_heartbeat_loop())
        self._ack_recovery_task = asyncio.create_task(self._relay_ack_recovery_loop())

        # Setup WebSocket Server
        try:
            async with websockets.serve(self.ws_handler, self.ws_host, self.ws_port):
                logger.info(f"Relay WebSocket server listening on ws://{self.ws_host}:{self.ws_port}")
                await asyncio.Future()  # run forever
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            if self._ack_recovery_task:
                self._ack_recovery_task.cancel()


def parse_udp_target_endpoint(value: str):
    """Parse a CLI route in the form drone_id=host:port."""
    drone_id, separator, endpoint = value.partition("=")
    host, port_separator, port_text = endpoint.rpartition(":")
    if not separator or not drone_id or not port_separator or not host:
        raise argparse.ArgumentTypeError("expected drone_id=host:port")
    try:
        port = int(port_text)
    except ValueError as e:
        raise argparse.ArgumentTypeError("endpoint port must be an integer") from e
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("endpoint port must be between 1 and 65535")
    return drone_id, (host, port)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PhoneOS WebSocket-to-UDP Relay")
    parser.add_argument("--ws-host", type=str, default="0.0.0.0", help="WebSocket host/interface to listen on")
    parser.add_argument("--ws-port", type=int, default=8080, help="WebSocket port to listen on")
    parser.add_argument("--udp-bind-port", type=int, default=14551, help="UDP port to bind for listening")
    parser.add_argument("--udp-bind-host", type=str, default="0.0.0.0", help="UDP host/interface to bind")
    parser.add_argument("--udp-target-port", type=int, default=14550, help="UDP port of DroneOS to broadcast to")
    parser.add_argument("--udp-target-host", type=str, default=None, help="Optional unicast host for DroneOS (used by simulation)")
    parser.add_argument("--udp-target", action="append", type=parse_udp_target_endpoint, default=[], metavar="DRONE=HOST:PORT", help="Static targeted UDP route (repeatable; used by simulation)")
    parser.add_argument("--gs-heartbeat-interval", type=float, default=1.0, help="Seconds between relay ground-station heartbeats while a WebSocket client is connected")
    parser.add_argument("--allow-duplicate-ips", action="store_true", default=True, help="Allow multiple WebSocket connections from the same IP")
    parser.add_argument("--disallow-duplicate-ips", action="store_false", dest="allow_duplicate_ips", help="Disallow multiple WebSocket connections from the same IP")
    parser.add_argument("--ws-send-timeout", type=float, default=3.0, help="Per-client WebSocket send timeout in seconds")
    args = parser.parse_args()

    relay = UdpWebsocketRelay(
        ws_host=args.ws_host,
        ws_port=args.ws_port,
        udp_bind_host=args.udp_bind_host,
        udp_bind_port=args.udp_bind_port,
        udp_target_port=args.udp_target_port,
        udp_target_host=args.udp_target_host,
        gs_heartbeat_interval=args.gs_heartbeat_interval,
        udp_target_endpoints=dict(args.udp_target),
        allow_duplicate_ips=args.allow_duplicate_ips,
        ws_send_timeout=args.ws_send_timeout
    )
    try:
        asyncio.run(relay.start())
    except KeyboardInterrupt:
        logger.info("Relay shutdown requested.")
