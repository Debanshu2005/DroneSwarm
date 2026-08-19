import asyncio
import json
import websockets
import socket
import sys
import os

# Add relay directory to path to import relay
sys.path.append(os.path.join(os.path.dirname(__file__), '../relay'))
from relay import UdpWebsocketRelay

async def run_test():
    ws_port = 8089
    udp_bind = 14559
    udp_target = 14558
    
    relay = UdpWebsocketRelay(ws_port=ws_port, udp_bind_port=udp_bind, udp_target_port=udp_target)
    task = asyncio.create_task(relay.start())
    await asyncio.sleep(0.5) 
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", udp_target))
    sock.setblocking(False)
    
    try:
        async with websockets.connect(f"ws://localhost:{ws_port}") as ws:
            test_msg = {"msg_type": "heartbeat", "sender_id": "test_phone"}
            await ws.send(json.dumps(test_msg))
            await asyncio.sleep(0.5)
            
        loop = asyncio.get_running_loop()
        data, addr = await loop.sock_recvfrom(sock, 1024)
        received = json.loads(data.decode('utf-8'))
        
        if received["sender_id"] == "test_phone" and received["msg_type"] == "heartbeat":
            print("TEST PASSED")
            sys.exit(0)
        else:
            print("TEST FAILED")
            sys.exit(1)
            
    except Exception as e:
        print(f"TEST FAILED with exception: {e}")
        sys.exit(1)
    finally:
        task.cancel()
        sock.close()

if __name__ == "__main__":
    asyncio.run(run_test())
