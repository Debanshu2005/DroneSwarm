import asyncio
import socket
import json
import websockets
import logging
import argparse

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PhoneOS_Relay")

class UdpWebsocketRelay:
    def __init__(self, ws_host="0.0.0.0", ws_port=8080, udp_bind_host="0.0.0.0", udp_bind_port=14551, udp_target_port=14550, udp_broadcast_addr="255.255.255.255"):
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.udp_bind_host = udp_bind_host
        self.udp_bind_port = udp_bind_port
        self.udp_target_port = udp_target_port
        self.udp_broadcast_addr = udp_broadcast_addr
        
        self.active_websockets = set()
        self.known_endpoints = {} # Target ID to address tuple
        
        # We need a reference to the loop
        self.loop = None
        self.transport = None
        self.protocol = None

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
            if self.active_websockets:
                # logger.debug(f"Forwarding {msg_dict.get('msg_type')} from {sender_id} to {len(self.active_websockets)} WS clients")
                aws = [ws.send(msg_str) for ws in self.active_websockets]
                await asyncio.gather(*aws, return_exceptions=True)
                
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON received from UDP: {e}")
        except Exception as e:
            logger.warning(f"Error forwarding UDP to WS: {e}")

    async def ws_handler(self, websocket):
        logger.info(f"New WebSocket client connected from {websocket.remote_address}")
        
        # Prevent duplicate WS connections from the same IP
        ip = websocket.remote_address[0]
        to_remove = [ws for ws in self.active_websockets if ws.remote_address[0] == ip]
        for ws in to_remove:
            logger.info(f"Closing duplicate WebSocket from {ip}")
            try:
                await ws.close()
            except:
                pass
            self.active_websockets.discard(ws)
            
        self.active_websockets.add(websocket)
        try:
            async for message in websocket:
                if isinstance(message, str):
                    await self.forward_ws_to_udp(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket client disconnected: {websocket.remote_address}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.active_websockets.remove(websocket)

    async def forward_ws_to_udp(self, message: str):
        if not self.transport:
            return
            
        try:
            msg_dict = json.loads(message)
            target_id = msg_dict.get('target_id')
            data = message.encode('utf-8')
            
            if target_id and target_id in self.known_endpoints:
                # Unicast
                addr = self.known_endpoints[target_id]
                self.transport.sendto(data, addr)
                logger.debug(f"Forwarded WS msg ({msg_dict.get('msg_type')}) via Unicast to {addr}")
            else:
                # Broadcast
                addr = (self.udp_broadcast_addr, self.udp_target_port)
                self.transport.sendto(data, addr)
                logger.debug(f"Forwarded WS msg ({msg_dict.get('msg_type')}) via Broadcast to {addr}")
                
        except json.JSONDecodeError:
            logger.warning("Received invalid JSON from WS, dropping.")
        except Exception as e:
            logger.error(f"Error forwarding WS to UDP: {e}")

    async def start(self):
        self.loop = asyncio.get_running_loop()
        
        # Setup UDP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
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

        # Setup WebSocket Server
        async with websockets.serve(self.ws_handler, self.ws_host, self.ws_port):
            logger.info(f"Relay WebSocket server listening on ws://{self.ws_host}:{self.ws_port}")
            await asyncio.Future()  # run forever

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PhoneOS WebSocket-to-UDP Relay")
    parser.add_argument("--ws-port", type=int, default=8080, help="WebSocket port to listen on")
    parser.add_argument("--udp-bind-port", type=int, default=14551, help="UDP port to bind for listening")
    parser.add_argument("--udp-target-port", type=int, default=14550, help="UDP port of DroneOS to broadcast to")
    args = parser.parse_args()

    relay = UdpWebsocketRelay(ws_port=args.ws_port, udp_bind_port=args.udp_bind_port, udp_target_port=args.udp_target_port)
    try:
        asyncio.run(relay.start())
    except KeyboardInterrupt:
        logger.info("Relay shutdown requested.")
