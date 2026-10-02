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

class UdpWebsocketRelay:
    def __init__(self, ws_host="0.0.0.0", ws_port=8080, udp_bind_host="0.0.0.0", udp_bind_port=14551, udp_target_port=14550, udp_broadcast_addr="255.255.255.255", udp_target_host=None, gs_heartbeat_interval=1.0, udp_target_endpoints=None):
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
        
        self.clients = set()
        self.known_endpoints = {} # Target ID to address tuple
        
        # We need a reference to the loop
        self.loop = None
        self.transport = None
        self.protocol = None
        self._heartbeat_task = None

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
        return True

    class UdpProtocol(asyncio.DatagramProtocol):
        def __init__(self, relay):
            self.relay = relay

        def connection_made(self, transport):
            self.transport = transport
            logger.info(f"UDP socket bound and ready.")

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
                
            # Broadcast to all connected WebSockets
            if self.clients:
                # A slow or abandoned app connection must not hold up the
                # healthy GCS client.  Bound each send and evict only the
                # failed client; this matters when several simulated relays
                # share one Windows host.
                clients = tuple(self.clients)
                results = await asyncio.gather(
                    *(asyncio.wait_for(ws.send(msg_str), timeout=1.0) for ws in clients),
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
        
        # Prevent duplicate WS connections from the same IP
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
                    await self.forward_ws_to_udp(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket client disconnected: {websocket.remote_address}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.clients.discard(websocket)

    async def forward_ws_to_udp(self, message: str):
        if not self.transport:
            return
            
        try:
            msg_dict = json.loads(message)
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

        # Setup WebSocket Server
        try:
            async with websockets.serve(self.ws_handler, self.ws_host, self.ws_port):
                logger.info(f"Relay WebSocket server listening on ws://{self.ws_host}:{self.ws_port}")
                await asyncio.Future()  # run forever
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()


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
    args = parser.parse_args()

    relay = UdpWebsocketRelay(
        ws_host=args.ws_host,
        ws_port=args.ws_port,
        udp_bind_host=args.udp_bind_host,
        udp_bind_port=args.udp_bind_port,
        udp_target_port=args.udp_target_port,
        udp_target_host=args.udp_target_host,
        gs_heartbeat_interval=args.gs_heartbeat_interval,
        udp_target_endpoints=dict(args.udp_target)
    )
    try:
        asyncio.run(relay.start())
    except KeyboardInterrupt:
        logger.info("Relay shutdown requested.")
