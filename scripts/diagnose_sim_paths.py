"""Direct, non-UI probe for each simulated WebSocket -> UDP -> DroneOS path.

Sends HOVER only (safe/idempotent) and prints heartbeat/telemetry plus command
lifecycle replies from the individual relay endpoint.
"""

import asyncio
import json
import time

import websockets


PATHS = (
    (8084, "drone1"),
    (8081, "drone2"),
    (8082, "drone3"),
    (8083, "drone4"),
)


async def probe(port: int, target_id: str) -> bool:
    command_id = f"sim-path-{target_id}-{int(time.time() * 1000)}"
    command = {
        "msg_type": "control",
        "sender_id": "diag_gcs",
        "target_id": target_id,
        "timestamp": time.time(),
        "action": "hover",
        "params": {},
        "cmd_id": command_id,
    }
    seen_telemetry = False
    seen_lifecycle = []
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as websocket:
            # First prove reverse traffic is reaching this relay/GCS connection.
            telemetry_deadline = time.monotonic() + 3.0
            while time.monotonic() < telemetry_deadline and not seen_telemetry:
                raw = await asyncio.wait_for(websocket.recv(), telemetry_deadline - time.monotonic())
                msg = json.loads(raw)
                if msg.get("sender_id") == target_id and msg.get("msg_type") in {"heartbeat", "telemetry"}:
                    seen_telemetry = True
            await websocket.send(json.dumps(command))
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(websocket.recv(), deadline - time.monotonic())
                msg = json.loads(raw)
                sender = msg.get("sender_id")
                msg_type = msg.get("msg_type")
                if sender == target_id and msg_type in {"heartbeat", "telemetry"}:
                    seen_telemetry = True
                if sender == target_id and msg_type == "command_lifecycle" and msg.get("cmd_id") == command_id:
                    seen_lifecycle.append(msg.get("stage"))
                    if msg.get("stage") in {"ACCEPTED", "REJECTED", "FAILED", "TIMEOUT"}:
                        break
    except Exception as exc:
        print(f"{target_id} via {port}: FAIL: WS -> relay ({type(exc).__name__}: {exc})")
        return False

    if not seen_telemetry:
        print(f"{target_id} via {port}: FAIL: DroneOS -> relay response; no heartbeat/telemetry")
        return False
    if "BACKEND_RECEIVED" not in seen_lifecycle:
        print(f"{target_id} via {port}: FAIL: UDP -> DroneOS; telemetry={seen_telemetry}; lifecycle={seen_lifecycle}")
        return False
    if not any(stage in seen_lifecycle for stage in {"ACCEPTED", "REJECTED", "FAILED", "TIMEOUT"}):
        print(f"{target_id} via {port}: FAIL: DroneOS command execution; telemetry={seen_telemetry}; lifecycle={seen_lifecycle}")
        return False
    print(f"{target_id} via {port}: PASS: WS -> relay -> UDP -> DroneOS -> response; telemetry={seen_telemetry}; lifecycle={seen_lifecycle}")
    return True


async def main() -> None:
    results = [await probe(port, target_id) for port, target_id in PATHS]
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
