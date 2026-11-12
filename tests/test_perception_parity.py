"""Parity tests for perception module across DroneOS, DroneOS1, DroneOS2, DroneOS3."""

import pytest
from unittest.mock import AsyncMock
import numpy as np

packages = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]


@pytest.mark.parametrize("pkg_name", packages)
def test_perception_imports_parity(pkg_name):
    """Verify all perception components and protocol classes can be imported from each package."""
    geolocate = __import__(f"{pkg_name}.perception.geolocate", fromlist=["project_pixel_to_ned"])
    assert hasattr(geolocate, "project_pixel_to_ned")

    aggregator = __import__(f"{pkg_name}.perception.aggregator", fromlist=["TrackAggregator"])
    assert hasattr(aggregator, "TrackAggregator")

    detector = __import__(f"{pkg_name}.perception.detector", fromlist=["GarbageDetector"])
    assert hasattr(detector, "GarbageDetector")
    assert hasattr(detector.GarbageDetector, "ensure_loaded")

    sources = __import__(f"{pkg_name}.perception.frame_sources", fromlist=["AirSimFrameSource", "OpenCVFrameSource", "ImageFolderSource"])
    assert hasattr(sources, "AirSimFrameSource")

    pipeline_mod = __import__(f"{pkg_name}.perception.pipeline", fromlist=["PerceptionPipeline"])
    assert hasattr(pipeline_mod, "PerceptionPipeline")

    messages = __import__(f"{pkg_name}.shared.protocol.messages", fromlist=["DetectionMessage", "DetectionItem", "MessageType"])
    assert hasattr(messages, "DetectionMessage")
    assert hasattr(messages.MessageType, "DETECTION")

    models = __import__(f"{pkg_name}.shared.config.models", fromlist=["PerceptionConfig"])
    assert hasattr(models, "PerceptionConfig")


@pytest.mark.parametrize("pkg_name", packages)
def test_perception_protocol_parity(pkg_name):
    """Verify JsonSerializer in all packages serializes and deserializes DetectionMessage."""
    messages = __import__(f"{pkg_name}.shared.protocol.messages", fromlist=["DetectionMessage", "DetectionItem", "MessageType"])
    serializers = __import__(f"{pkg_name}.shared.communication.serializers", fromlist=["JsonSerializer"])

    serializer = serializers.JsonSerializer()
    item = messages.DetectionItem(
        id=f"{pkg_name.lower()}-g0001",
        label="rubbish",
        confidence=0.85,
        lat=22.314,
        lon=87.310,
        hits=3,
        status="new",
        altitude_agl=12.0,
    )
    msg = messages.DetectionMessage(
        sender_id=pkg_name.lower(),
        timestamp=1700000000.0,
        detections=[item],
        synthetic=True,
        frame_id=1,
    )

    data = serializer.serialize(msg)
    deserialized = serializer.deserialize(data)

    assert deserialized.msg_type == messages.MessageType.DETECTION
    assert deserialized.sender_id == pkg_name.lower()
    assert len(deserialized.detections) == 1
    assert deserialized.detections[0].id == f"{pkg_name.lower()}-g0001"
    assert deserialized.detections[0].confidence == 0.85
    assert deserialized.detections[0].status == "new"


@pytest.mark.asyncio
@pytest.mark.parametrize("pkg_name", packages)
async def test_perception_pipeline_parity(pkg_name):
    """Verify PerceptionPipeline instantiates and executes step in all packages."""
    pipeline_mod = __import__(f"{pkg_name}.perception.pipeline", fromlist=["PerceptionPipeline"])
    types_mod = __import__(f"{pkg_name}.perception.types", fromlist=["Frame", "Detection"])
    messages_mod = __import__(f"{pkg_name}.shared.protocol.messages", fromlist=["TelemetryData"])
    models_mod = __import__(f"{pkg_name}.shared.config.models", fromlist=["PerceptionConfig"])

    dummy_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame = types_mod.Frame(image=dummy_img, timestamp=100.0)

    frame_src = AsyncMock()
    frame_src.get_frame.return_value = frame
    frame_src.is_synthetic = False

    detector = AsyncMock()
    detector.detect.return_value = [
        types_mod.Detection(bbox_xyxy=(640, 360, 660, 380), confidence=0.9, label="rubbish")
    ]

    fc = AsyncMock()
    fc.get_telemetry.return_value = messages_mod.TelemetryData(
        flight_mode="OFFBOARD",
        armed_state="ARMED",
        gps_valid=True,
        altitude=10.0,
        roll=0.0,
        pitch=0.0,
        yaw=0.0,
        latitude=22.314,
        longitude=87.310,
    )

    network = AsyncMock()
    cfg = models_mod.PerceptionConfig(enabled=True, min_hits=1)

    pipeline = pipeline_mod.PerceptionPipeline(
        drone_id=f"{pkg_name.lower()}",
        frame_source=frame_src,
        detector=detector,
        flight_controller=fc,
        network_adapter=network,
        config=cfg,
    )

    await pipeline._step()
    network.broadcast_message.assert_awaited_once()
    broadcasted = network.broadcast_message.await_args[0][0]
    assert broadcasted.sender_id == pkg_name.lower()
    assert len(broadcasted.detections) == 1

