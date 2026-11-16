import asyncio
import base64
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import websockets

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from relay.relay import UdpWebsocketRelay
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage
from DroneOS.shared.communication.serializers import JsonSerializer

async def test_browser():
    print("[1] Starting UdpWebsocketRelay...")
    relay = UdpWebsocketRelay(
        ws_host="127.0.0.1",
        ws_port=8088,
        udp_bind_host="127.0.0.1",
        udp_bind_port=14650,
        udp_target_port=14550
    )
    relay_task = asyncio.create_task(relay.start())
    await asyncio.sleep(0.8)

    print("[2] Launching Chrome with CDP...")
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    cmd = [
        chrome_path,
        "--headless=new",
        "--remote-debugging-port=9222",
        "--window-size=1280,720",
        "--disable-gpu",
        "about:blank"
    ]
    proc = subprocess.Popen(cmd)
    await asyncio.sleep(2.0)

    try:
        # Get CDP page target
        with urllib.request.urlopen("http://127.0.0.1:9222/json") as resp:
            tabs = json.loads(resp.read().decode())
            page_ws_url = tabs[0]["webSocketDebuggerUrl"]
        print("Connected to CDP at:", page_ws_url)

        async with websockets.connect(page_ws_url) as cdp:
            await cdp.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await cdp.send(json.dumps({"id": 2, "method": "Runtime.enable"}))

            target_url = "http://127.0.0.1:5173/?view=MAP&ws=ws://127.0.0.1:8088"
            print("Navigating to:", target_url)
            await cdp.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": target_url}}))
            await asyncio.sleep(3.5)

            # Transmit detection message via UDP to relay
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            det = DetectionItem(
                id="det_live_01",
                lat=47.641468,
                lon=-122.140138,
                confidence=0.65,
                hits=2,
                label="rubbish",
                first_seen=time.time(),
                last_seen=time.time(),
                status="confirmed"
            )
            msg = DetectionMessage(
                sender_id="drone1",
                timestamp=time.time(),
                detections=[det]
            )
            serializer = JsonSerializer()
            raw_bytes = serializer.serialize(msg)
            sock.sendto(raw_bytes, ("127.0.0.1", 14650))
            print("Sent DetectionMessage via UDP to relay port 14650")
            await asyncio.sleep(1.5)

            # Query DOM
            async def evaluate_js(expr, req_id):
                await cdp.send(json.dumps({"id": req_id, "method": "Runtime.evaluate", "params": {"expression": expr}}))
                while True:
                    m = json.loads(await cdp.recv())
                    if m.get("id") == req_id:
                        return m.get("result", {}).get("result", {}).get("value")

            marker_count = await evaluate_js("document.querySelectorAll('.custom-garbage-marker').length", 10)
            print("Leaflet DOM marker count:", marker_count)

            # Check duplicate suppression: send duplicate message
            sock.sendto(raw_bytes, ("127.0.0.1", 14650))
            await asyncio.sleep(0.5)
            marker_count_after_dup = await evaluate_js("document.querySelectorAll('.custom-garbage-marker').length", 11)
            print("Marker count after duplicate message:", marker_count_after_dup)

            # Capture screenshot
            await cdp.send(json.dumps({"id": 20, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
            while True:
                m = json.loads(await cdp.recv())
                if m.get("id") == 20:
                    ss_b64 = m["result"]["data"]
                    break
            
            with open("ml/reports/airsim_live_phase2_1_gcs_screenshot.png", "wb") as f:
                f.write(base64.b64decode(ss_b64))
            print("Screenshot saved to ml/reports/airsim_live_phase2_1_gcs_screenshot.png")

    finally:
        proc.terminate()
        relay_task.cancel()
        try:
            await relay_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(test_browser())
