"""Unit tests for perception protocol messages and serialization."""

import pytest
from unittest.mock import AsyncMock

from DroneOS.shared.communication.network_node import UdpNetworkAdapter
from DroneOS.shared.communication.serializers import JsonSerializer
from DroneOS.shared.protocol.messages import (
    DetectionItem,
    DetectionMessage,
    MessageType,
)


def test_detection_message_serialization_round_trip():
    """Verify DetectionMessage and DetectionItem serialization round trip."""
    serializer = JsonSerializer()

    item1 = DetectionItem(
        id="drone1-g0001",
        label="rubbish",
        confidence=0.8923,
        lat=22.3145678,
        lon=87.3091234,
        hits=5,
        status="new",
        altitude_agl=10.5,
    )
    item2 = DetectionItem(
        id="drone1-g0002",
        label="rubbish",
        confidence=0.7412,
        lat=22.3146000,
        lon=87.3092000,
        hits=2,
        status="update",
        altitude_agl=None,
    )

    orig_msg = DetectionMessage(
        sender_id="drone1",
        timestamp=1700000000.123,
        detections=[item1, item2],
        synthetic=True,
        frame_id=42,
    )

    raw_bytes = serializer.serialize(orig_msg)
    assert isinstance(raw_bytes, bytes)

    deserialized = serializer.deserialize(raw_bytes)
    assert isinstance(deserialized, DetectionMessage)
    assert deserialized.msg_type == MessageType.DETECTION
    assert deserialized.sender_id == "drone1"
    assert deserialized.synthetic is True
    assert deserialized.frame_id == 42
    assert len(deserialized.detections) == 2

    d0 = deserialized.detections[0]
    assert d0.id == "drone1-g0001"
    assert d0.label == "rubbish"
    assert pytest.approx(d0.confidence, abs=1e-4) == 0.8923
    assert pytest.approx(d0.lat, abs=1e-7) == 22.3145678
    assert pytest.approx(d0.lon, abs=1e-7) == 87.3091234
    assert d0.hits == 5
    assert d0.status == "new"
    assert d0.altitude_agl == 10.5

    d1 = deserialized.detections[1]
    assert d1.id == "drone1-g0002"
    assert d1.status == "update"
    assert d1.altitude_agl is None


@pytest.mark.asyncio
async def test_detection_message_hmac_round_trip(monkeypatch):
    """Verify signed DetectionMessage passes validation when DRONE_NET_SECRET is set."""
    monkeypatch.setenv("DRONE_NET_SECRET", "perception-secret-key")
    serializer = JsonSerializer()

    sender = UdpNetworkAdapter("drone1", "0.0.0.0", 14550, "255.255.255.255", serializer)
    receiver = UdpNetworkAdapter("receiver", "0.0.0.0", 14550, "255.255.255.255", serializer)
    callback = AsyncMock()
    receiver.register_callback(callback)

    item = DetectionItem(
        id="drone1-g0001",
        label="rubbish",
        confidence=0.95,
        lat=22.3,
        lon=87.3,
        hits=3,
        status="new",
    )
    msg = DetectionMessage(
        sender_id="drone1",
        timestamp=1700000000.0,
        detections=[item],
        synthetic=False,
        frame_id=1,
    )

    signed = sender._sign_message(msg)
    assert signed.hmac_sig is not None

    payload = serializer.serialize(signed)
    await receiver._handle_incoming(payload, ("127.0.0.1", 45000))

    callback.assert_awaited_once()
    received_msg = callback.await_args[0][0]
    assert isinstance(received_msg, DetectionMessage)
    assert received_msg.detections[0].id == "drone1-g0001"
