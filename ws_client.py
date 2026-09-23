import asyncio
import websockets
import json
import time

async def listen():
    uri = "ws://localhost:8081"
    print(f"Connecting to {uri}")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Waiting for STATUS messages...")
            # Send a heartbeat so relay knows we exist
            hb = {"msg_type": "heartbeat", "sender_id": "gs_mobile_01", "status": "active"}
            await websocket.send(json.dumps(hb))
            
            while True:
                response = await websocket.recv()
                data = json.loads(response)
                
                # Check for error status messages
                if data.get("msg_type") == "status":
                    sev = data.get("severity", "info").upper()
                    if sev in ["ERROR", "CRITICAL"]:
                        print("\n[WS CLIENT RECEIVED OVER THE WIRE]:")
                        print(json.dumps(data, indent=2))
                        
                        # Simulate Mobile App's DroneContext logic:
                        print(f"\n[MOBILE APP EVENT LOG SIMULATION]:")
                        print(f"Time: {time.time()}")
                        print(f"Msg: ACK from {data.get('sender_id')}: {data.get('status_text')}")
                        print(f"Severity: {sev}")
                        print(f"Source: DRONEOS")
                        print(f"Event: BACKEND_ERROR")
                        print("-" * 50)
                        
                        # We got what we needed, exit
                        return
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(listen())
