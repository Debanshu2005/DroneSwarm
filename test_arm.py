import asyncio
import websockets
import json
import sys
import uuid

async def test_arm():
    uri = "ws://127.0.0.1:8083/ws/command"
    try:
        async with websockets.connect(uri) as ws:
            auth_msg = {"type": "AUTH", "token": "default_swarm_secret"}
            await ws.send(json.dumps(auth_msg))
            await asyncio.sleep(0.5)

            cmd_id = str(uuid.uuid4())
            cmd = {
                "msg_type": "control",
                "action": "arm",
                "params": {"_command_id": cmd_id},
                "cmd_id": cmd_id,
                "target_id": "drone3",
                "sender_id": "gs_script"
            }
            await ws.send(json.dumps(cmd))
            print(f"Sent ARM command {cmd_id}")
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                print(f"Received Lifecycle: {json.dumps(data, indent=2)}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test_arm())
