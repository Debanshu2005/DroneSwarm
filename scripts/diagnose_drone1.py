"""Trace the exact Drone1 WebSocket control path with safe HOVER then LAND."""

import asyncio
import json
import time

import websockets


URL = "ws://127.0.0.1:8084"


async def send_and_trace(websocket, action: str, cmd_id: str) -> None:
    command = {
        "msg_type": "control",
        "sender_id": "gs_mobile_01",
        "timestamp": time.time(),
        "target_id": "drone1",
        "action": action,
        "params": {},
        "cmd_id": cmd_id,
    }
    print(f"SEND: url={URL} target_id=drone1 action={action} cmd_id={cmd_id} timestamp={command['timestamp']}")
    await websocket.send(json.dumps(command))
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        message = json.loads(await asyncio.wait_for(websocket.recv(), deadline - time.monotonic()))
        if message.get("msg_type") == "command_lifecycle":
            print(
                "RECV LIFECYCLE: "
                f"sender_id={message.get('sender_id')} target_id={message.get('target_id')} "
                f"action={message.get('action')} stage={message.get('stage')} cmd_id={message.get('cmd_id')}"
            )
            if message.get("cmd_id") == cmd_id and message.get("stage") in {"ACCEPTED", "REJECTED", "FAILED", "TIMEOUT"}:
                return
    raise TimeoutError(f"No terminal lifecycle response for {cmd_id}")


async def main() -> None:
    async with websockets.connect(URL) as websocket:
        await send_and_trace(websocket, "hover", "diagnostic_drone1")
        await send_and_trace(websocket, "land", "diagnostic_drone1_land")


if __name__ == "__main__":
    asyncio.run(main())
