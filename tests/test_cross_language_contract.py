"""Cross-language contract test between Python DroneOS serializer and Mobile JavaScript client."""

import shutil
import subprocess
import pytest

from DroneOS.shared.communication.serializers import JsonSerializer
from DroneOS.shared.protocol.messages import DetectionItem, DetectionMessage


def test_cross_language_detection_contract():
    """Serialize DetectionMessage in Python, feed JSON to mobile/src/utils/garbage.js in Node."""
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js runtime not found in PATH")

    serializer = JsonSerializer()
    item = DetectionItem(
        id="drone1-g0001",
        label="rubbish",
        confidence=0.88,
        lat=22.3145,
        lon=87.3095,
        hits=3,
        status="new",
        altitude_agl=10.0,
    )
    msg = DetectionMessage(
        sender_id="drone1",
        timestamp=1700000000.0,
        detections=[item],
        synthetic=True,
        frame_id=5,
    )
    json_bytes = serializer.serialize(msg)
    json_str = json_bytes.decode("utf-8")

    node_script = """
import { mergeDetections } from './mobile/src/utils/garbage.js';
import fs from 'fs';

const input = JSON.parse(fs.readFileSync(0, 'utf-8'));
const merged = mergeDetections({}, input.detections, input.sender_id, Date.now(), new Set(), input.synthetic);
const item = merged['drone1-g0001'];
if (!item) throw new Error('Missing drone1-g0001');
if (item.confidence !== 0.88) throw new Error('Bad confidence: ' + item.confidence);
if (item.hits !== 3) throw new Error('Bad hits: ' + item.hits);
if (item.lat !== 22.3145 || item.lon !== 87.3095) throw new Error('Bad coords');
if (item.reportingDrone !== 'drone1') throw new Error('Bad droneId');
if (item.synthetic !== true) throw new Error('Bad synthetic flag');
console.log('CONTRACT_OK');
"""

    proc = subprocess.run(
        [node_exe, "--input-type=module", "-e", node_script],
        input=json_str,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, f"Node process failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "CONTRACT_OK" in proc.stdout


def test_cross_language_drone_health_and_network_contract():
    """Verify DroneHealth and MultiWebSocketManager logic via Node runtime."""
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js runtime not found in PATH")

    node_script = """
import { evaluateTelemetryFreshness, evaluateDroneHealth } from './mobile/src/utils/DroneHealth.js';
import { MultiWebSocketManager } from './mobile/src/networking/MultiWebSocketManager.js';

// 1. Telemetry Freshness Evaluation
const now = Date.now();

// Drone with fresh heartbeat but no separate telemetry timestamp
const droneHeartbeatOnly = {
    id: "drone1",
    status: "CONNECTED",
    lastHeartbeat: now - 500,
    lastSeen: now - 500,
};
const f1 = evaluateTelemetryFreshness(droneHeartbeatOnly);
if (f1 !== 'LIVE') throw new Error(`Expected LIVE for fresh heartbeat, got ${f1}`);

// Drone previously OFFLINE with incoming fresh heartbeat
const droneRecovering = {
    id: "drone2",
    status: "OFFLINE",
    lastHeartbeat: now - 300,
    lastSeen: now - 300,
};
const f2 = evaluateTelemetryFreshness(droneRecovering);
if (f2 !== 'LIVE') throw new Error(`Expected LIVE for recovering drone, got ${f2} (circular OFFLINE trap!)`);

// Drone with ancient lastSeen (>8s)
const droneTimedOut = {
    id: "drone3",
    status: "CONNECTED",
    lastTelemetry: now - 10000,
    lastHeartbeat: now - 10000,
    lastSeen: now - 10000,
};
const f3 = evaluateTelemetryFreshness(droneTimedOut);
if (f3 !== 'OFFLINE') throw new Error(`Expected OFFLINE for timed out drone, got ${f3}`);

// 2. MultiWebSocketManager URL Canonicalization
const m = new MultiWebSocketManager();
const u1 = m.normalizeConnectionUrl('ws://localhost:8081');
const u2 = m.normalizeConnectionUrl('ws://127.0.0.1:8081');
const u3 = m.normalizeConnectionUrl('localhost:8081');

if (u1 !== 'ws://127.0.0.1:8081') throw new Error(`Bad u1: ${u1}`);
if (u2 !== 'ws://127.0.0.1:8081') throw new Error(`Bad u2: ${u2}`);
if (u3 !== 'ws://127.0.0.1:8081') throw new Error(`Bad u3: ${u3}`);

// 3. Saved connections ignores port 8088
let storedVal = JSON.stringify(['ws://127.0.0.1:8088', 'ws://127.0.0.1:8081']);
m._safeStorageGet = () => storedVal;
globalThis.WebSocket = class MockWebSocket {
    constructor() {
        this.readyState = 0;
    }
    close() {}
};
m.loadSavedConnections();
if (m.connections['ws://127.0.0.1:8088']) throw new Error('Port 8088 must not be connected from saved connections!');
if (!m.connections['ws://127.0.0.1:8081']) throw new Error('Port 8081 must be connected from saved connections!');

console.log('HEALTH_NETWORK_CONTRACT_OK');
process.exit(0);
"""

    proc = subprocess.run(
        [node_exe, "--input-type=module", "-e", node_script],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"Node process failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "HEALTH_NETWORK_CONTRACT_OK" in proc.stdout


