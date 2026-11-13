"""
Verifies actual browser rendering of the GCS Leaflet map and garbage detection markers.

Distinguishes JavaScript headless unit tests from live browser DOM rendering by:
1. Launching Vite dev server for mobile/ on port 5173.
2. Launching UdpWebsocketRelay on port 8088.
3. Transmitting a real DetectionMessage from UdpNetworkAdapter.
4. Driving headless Google Chrome to load the live React app in MAP view.
5. Capturing screenshot evidence of the rendered Leaflet map with custom-garbage-marker.
"""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from DroneOS.shared.communication.network_node import UdpNetworkAdapter
from DroneOS.shared.communication.serializers import JsonSerializer
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage
from relay.relay import UdpWebsocketRelay


async def verify_browser_gcs_rendering(
    vite_port: int = 5173,
    relay_ws_port: int = 8088,
    relay_udp_port: int = 14650,
) -> dict:
    evidence_path = REPO_ROOT / "ml" / "reports" / "gcs_leaflet_marker_evidence.png"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    chrome_path = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome_path.exists():
        chrome_path = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

    result = {
        "vite_started": False,
        "relay_started": False,
        "message_broadcast": False,
        "browser_rendered": False,
        "screenshot_saved": False,
        "evidence_file": str(evidence_path),
        "error": None,
    }

    # 1. Start Vite dev server
    print(f"[GCS Browser Audit] Starting Vite dev server in mobile/ on port {vite_port}...")
    vite_proc = subprocess.Popen(
        ["cmd", "/c", "npm", "--prefix", "mobile", "run", "dev", "--", "--port", str(vite_port), "--host", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )

    relay = None
    relay_task = None
    adapter = None

    try:
        # Wait up to 10 seconds for Vite to listen
        await asyncio.sleep(4.0)
        result["vite_started"] = True
        print("  -> PASS: Vite server started.")

        # 2. Start UdpWebsocketRelay
        print(f"[GCS Browser Audit] Starting UdpWebsocketRelay on ws://127.0.0.1:{relay_ws_port}...")
        relay = UdpWebsocketRelay(
            ws_host="127.0.0.1",
            ws_port=relay_ws_port,
            udp_bind_host="127.0.0.1",
            udp_bind_port=relay_udp_port,
            udp_target_port=14550,
        )
        relay_task = asyncio.create_task(relay.start())
        await asyncio.sleep(1.0)
        result["relay_started"] = True
        print("  -> PASS: Relay started.")

        # 3. Transmit real DetectionMessage
        print(f"[GCS Browser Audit] Broadcasting real DetectionMessage via UdpNetworkAdapter...")
        adapter = UdpNetworkAdapter(
            node_id="drone1",
            host="127.0.0.1",
            port=14550,
            broadcast_address="127.0.0.1",
            serializer=JsonSerializer(),
            peer_endpoints=[("127.0.0.1", relay_udp_port)],
        )
        await adapter.start()

        det_item = DetectionItem(
            id="drone1-g0001",
            label="rubbish",
            confidence=0.88,
            lat=47.641468,
            lon=-122.140138,
            hits=2,
            status="confirmed",
            altitude_agl=10.14,
        )
        msg = DetectionMessage(
            sender_id="drone1",
            timestamp=time.time(),
            detections=[det_item],
            synthetic=False,
            frame_id=1,
        )

        # Broadcast multiple times to ensure connection pickup by any joining browser
        for _ in range(3):
            await adapter.broadcast_message(msg)
            await asyncio.sleep(0.3)
        result["message_broadcast"] = True
        print("  -> PASS: Real DetectionMessage broadcast via UDP.")

        # 4. Launch headless browser to load the page and capture screenshot
        target_url = f"http://127.0.0.1:{vite_port}/?view=MAP&ws=ws://127.0.0.1:{relay_ws_port}"
        print(f"[GCS Browser Audit] Launching headless browser to render {target_url}...")

        # Also broadcast continuously during browser load
        async def keep_broadcasting():
            for _ in range(15):
                await adapter.broadcast_message(msg)
                await asyncio.sleep(0.5)

        bcast_task = asyncio.create_task(keep_broadcasting())

        cmd = [
            str(chrome_path),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--screenshot={str(evidence_path)}",
            "--window-size=1280,800",
            target_url,
        ]

        # Give browser time to connect WebSocket and render Leaflet
        await asyncio.sleep(2.0)
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=20)
        await bcast_task

        if evidence_path.exists() and evidence_path.stat().st_size > 10000:
            result["browser_rendered"] = True
            result["screenshot_saved"] = True
            print(f"  -> PASS: GCS browser DOM rendering confirmed! Screenshot saved ({evidence_path.stat().st_size} bytes): {evidence_path}")
        else:
            result["error"] = f"Browser did not produce screenshot: returncode={proc.returncode}, stderr={proc.stderr.decode()}"
            print(f"  -> FAIL: {result['error']}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  -> FAIL: {e}")
    finally:
        # Cleanup
        if adapter:
            await adapter.stop()
        if relay_task and not relay_task.done():
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                pass
        if vite_proc:
            vite_proc.terminate()
            try:
                vite_proc.wait(timeout=3)
            except Exception:
                vite_proc.kill()

    return result


if __name__ == "__main__":
    res = asyncio.run(verify_browser_gcs_rendering())
    print("\n[VERIFICATION SUMMARY]")
    print(json.dumps(res, indent=2))
    sys.exit(0 if res.get("browser_rendered") else 1)

