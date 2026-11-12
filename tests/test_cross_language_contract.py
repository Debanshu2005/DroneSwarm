"""Cross-language contract test between Python DroneOS serializer and Mobile JavaScript client."""

import json
from pathlib import Path
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
