import asyncio
import json
import websockets
import socket
import sys
import os

# Add relay directory to path to import relay
sys.path.append(os.path.join(os.path.dirname(__file__), '../relay'))
from relay import UdpWebsocketRelay

async def run_regression():
    ws_port = 8090
    udp_bind = 14550  # Must match target and bind for loopback test
    udp_target = 14550
    
    relay = UdpWebsocketRelay(ws_port=ws_port, udp_bind_port=udp_bind, udp_target_port=udp_target)
    task = asyncio.create_task(relay.start())
    await asyncio.sleep(0.5)
    
    try:
        async with websockets.connect(f"ws://localhost:{ws_port}") as ws:
            # Emulate DroneOS broadcasting telemetry to 14550
            test_telemetry = {"msg_type": "telemetry", "sender_id": "drone1", "telemetry": {"altitude": 10.5}}
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(json.dumps(test_telemetry).encode('utf-8'), ("127.0.0.1", 14550))
            
            # The WS client should immediately receive it back from Relay
            response = await asyncio.wait_for(ws.recv(), timeout=2.0)
            received = json.loads(response)
            
            if received["sender_id"] == "drone1" and received["msg_type"] == "telemetry":
                print("REGRESSION TEST PASSED: UDP Telemetry successfully forwarded to WS Client")
                sys.exit(0)
            else:
                print("REGRESSION TEST FAILED: Wrong message received")
                sys.exit(1)
                
    except Exception as e:
        print(f"REGRESSION TEST FAILED with exception: {e}")
        sys.exit(1)
    finally:
        task.cancel()
        
if __name__ == "__main__":
    asyncio.run(run_regression())
