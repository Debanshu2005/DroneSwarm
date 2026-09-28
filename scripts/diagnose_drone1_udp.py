"""Bypass relay ingress to determine whether Drone1's UDP node executes control."""

import asyncio
import json
import socket
import time

import websockets


COMMAND = {
    "msg_type": "control",
    "sender_id": "gs_mobile_01",
    "timestamp": time.time(),
    "target_id": "drone1",
    "action": "hover",
    "params": {},
    "cmd_id": "diagnostic_drone1_udp",
}


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8084") as websocket:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.sendto(json.dumps(COMMAND).encode("utf-8"), ("127.0.0.1", 14550))
        print("SEND UDP: 127.0.0.1:14550 target_id=drone1 action=hover cmd_id=diagnostic_drone1_udp")
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(websocket.recv(), deadline - time.monotonic()))
            if message.get("msg_type") == "command_lifecycle":
                print(f"RECV LIFECYCLE: {message}")
                if message.get("cmd_id") == COMMAND["cmd_id"]:
                    return
    raise TimeoutError("Drone1 did not return a lifecycle message after direct UDP injection")


if __name__ == "__main__":
    asyncio.run(main())
