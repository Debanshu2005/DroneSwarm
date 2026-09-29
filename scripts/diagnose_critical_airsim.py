"""Trace critical Drone1 commands through a live AirSim simulation.

This intentionally uses the same WebSocket -> relay -> UDP path as the GCS.
It first enables formation so the critical-command preemption path is exercised.
"""

import asyncio
import json
import time

import websockets


URL = "ws://127.0.0.1:8084"
TARGET = "drone1"


async def send_and_wait(websocket, action, params=None):
    command_id = f"critical-{action}-{int(time.time() * 1000)}"
    command = {
        "msg_type": "control",
        "sender_id": "critical_diag_gcs",
        "target_id": TARGET,
        "timestamp": time.time(),
        "action": action,
        "params": params or {},
        "cmd_id": command_id,
    }
    print(f"SEND action={action} cmd_id={command_id} params={command['params']}")
    await websocket.send(json.dumps(command))

    terminal = None
    telemetry = []
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        message = json.loads(await asyncio.wait_for(websocket.recv(), deadline - time.monotonic()))
        if message.get("sender_id") != TARGET:
            continue
        if message.get("msg_type") == "telemetry":
            payload = message.get("telemetry") or {}
            telemetry.append({
                "altitude": payload.get("altitude"),
                "armed_state": payload.get("armed_state"),
                "flight_mode": payload.get("flight_mode"),
            })
        if message.get("msg_type") != "command_lifecycle" or message.get("cmd_id") != command_id:
            continue
        print(
            "LIFECYCLE "
            f"action={message.get('action')} stage={message.get('stage')} "
            f"reason={message.get('reason')}"
        )
        if message.get("stage") in {"ACCEPTED", "REJECTED", "FAILED", "TIMEOUT"}:
            terminal = message
            break

    print(f"RESULT action={action} terminal={terminal and terminal.get('stage')} telemetry_tail={telemetry[-3:]}")
    return terminal


async def main():
    async with websockets.connect(URL) as websocket:
        # Lets the relay establish its ground-station heartbeat before ARM.
        await asyncio.sleep(1.0)
        formation = await send_and_wait(websocket, "formation_update", {"type": "V", "spacing": 5.0})
        if not formation or formation.get("stage") != "ACCEPTED":
            raise RuntimeError("Formation update did not reach Drone1")

        arm = await send_and_wait(websocket, "arm")
        if not arm or arm.get("stage") != "ACCEPTED":
            raise RuntimeError("ARM was rejected; TAKEOFF was not attempted")

        # The pipeline reads the AirSim armed state asynchronously.  Allow a
        # telemetry tick before asking FlightManager to create TAKEOFF intent.
        await asyncio.sleep(1.0)

        takeoff = await send_and_wait(websocket, "takeoff", {"altitude_m": 1.0})
        if not takeoff or takeoff.get("stage") != "ACCEPTED":
            raise RuntimeError("TAKEOFF did not reach the flight controller")

        land = await send_and_wait(websocket, "land")
        if not land or land.get("stage") != "ACCEPTED":
            raise RuntimeError("LAND did not reach the flight controller")

        rtl = await send_and_wait(websocket, "rtl")
        if not rtl or rtl.get("stage") != "ACCEPTED":
            raise RuntimeError("RTL did not reach the flight controller")


if __name__ == "__main__":
    asyncio.run(main())
