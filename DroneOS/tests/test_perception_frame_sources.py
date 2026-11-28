"""Unit tests for Perception FrameSources (AirSim, OpenCV, ImageFolder)."""

import asyncio
from unittest.mock import MagicMock, patch
import cv2
import numpy as np
import pytest

from DroneOS.perception.frame_sources import (
    AirSimFrameSource,
    ImageFolderSource,
    OpenCVFrameSource,
)


@pytest.mark.asyncio
async def test_airsim_frame_source_connection_failure():
    """AirSimFrameSource must handle unreachable server gracefully and return None."""
    src = AirSimFrameSource(vehicle_name="Drone1", host="127.0.0.1", port=49999)
    # Attempting capture with no running AirSim returns None without throwing
    frame = await src.get_frame()
    assert frame is None
    assert not src.is_synthetic
    await src.close()


@pytest.mark.asyncio
async def test_opencv_frame_source_unavailable_device():
    """OpenCVFrameSource with invalid device index fails gracefully."""
    src = OpenCVFrameSource(device=999)
    frame = await src.get_frame()
    assert frame is None
    assert not src.is_synthetic
    await src.close()


@pytest.mark.asyncio
async def test_image_folder_source_missing_directory(tmp_path):
    """ImageFolderSource with non-existent path handles missing folder gracefully."""
    missing = tmp_path / "non_existent_folder_xyz"
    src = ImageFolderSource(folder_path=str(missing))
    assert src.is_synthetic is True
    assert src.image_files == []
    frame = await src.get_frame()
    assert frame is None
    await src.close()


@pytest.mark.asyncio
async def test_image_folder_source_replay_and_synthetic_flag(tmp_path):
    """ImageFolderSource cycles through images and marks frames synthetic."""
    img1_path = tmp_path / "test1.jpg"
    img2_path = tmp_path / "test2.jpg"

    dummy1 = np.ones((100, 100, 3), dtype=np.uint8) * 50
    dummy2 = np.ones((100, 100, 3), dtype=np.uint8) * 150

    cv2.imwrite(str(img1_path), dummy1)
    cv2.imwrite(str(img2_path), dummy2)

    src = ImageFolderSource(folder_path=str(tmp_path))
    assert src.is_synthetic is True
    assert len(src.image_files) == 2

    # First frame
    f1 = await src.get_frame()
    assert f1 is not None
    assert f1.image.shape == (100, 100, 3)

    # Second frame
    f2 = await src.get_frame()
    assert f2 is not None

    # Cycling wraps around
    f3 = await src.get_frame()
    assert f3 is not None

    await src.close()


@pytest.mark.asyncio
async def test_opencv_device_not_found_and_opt_in_default():
    """Default config is airsim (opt-in only), and unavailable device returns None without crash."""
    from DroneOS.shared.config.models import PerceptionConfig

    default_cfg = PerceptionConfig()
    assert default_cfg.source == "airsim"

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    with patch("cv2.VideoCapture", return_value=mock_cap):
        src = OpenCVFrameSource(device=0, reconnect_backoff_s=0.01)
        frame = await src.get_frame()
        assert frame is None
        assert src.last_error is not None
        await src.close()


@pytest.mark.asyncio
async def test_opencv_none_frame_and_reconnect_after_failure():
    """read() returning None triggers reconnect after backoff and recovers on next valid frame."""
    img_ok = np.zeros((48, 64, 3), dtype=np.uint8)
    cap_bad = MagicMock()
    cap_bad.isOpened.return_value = True
    cap_bad.get.return_value = 0.0
    cap_bad.read.return_value = (False, None)

    cap_good = MagicMock()
    cap_good.isOpened.return_value = True
    cap_good.get.return_value = 640.0
    cap_good.read.return_value = (True, img_ok)

    with patch("cv2.VideoCapture", side_effect=[cap_bad, cap_good]):
        src = OpenCVFrameSource(device=0, reconnect_backoff_s=0.01)
        f1 = await src.get_frame()
        assert f1 is None
        cap_bad.release.assert_called_once()

        await asyncio.sleep(0.02)
        f2 = await src.get_frame()
        assert f2 is not None
        assert f2.image.shape == (48, 64, 3)
        assert f2.metadata["width"] == 64
        # Buffer size 1 set for freshest frame
        cap_good.set.assert_any_call(cv2.CAP_PROP_BUFFERSIZE, 1)

        await src.close()
        cap_good.release.assert_called_once()


@pytest.mark.asyncio
async def test_opencv_stalled_camera_degrades_status_and_gate_rejects():
    """Stalled camera marks source is_stalled=True and stale frame triggers STALE_FRAME gate."""
    import time
    from DroneOS.perception.pipeline import PerceptionPipeline
    from DroneOS.perception.types import Frame
    from DroneOS.shared.config.models import PerceptionConfig
    from DroneOS.shared.protocol.messages import TelemetryData

    src = OpenCVFrameSource(device=0, stall_timeout_s=0.02)
    src._opened_at_monotonic = time.monotonic() - 1.0
    src.last_error = "empty frame"
    assert src.is_stalled is True

    # Stale frame rejected by STALE_FRAME gate
    stale_frame = Frame(image=np.zeros((64, 64, 3), dtype=np.uint8), timestamp=time.time() - 10.0)
    fake_src = MagicMock()
    fake_src.is_synthetic = False
    async def _get_stale():
        return stale_frame
    fake_src.get_frame = _get_stale

    fake_fc = MagicMock()
    async def _get_telem():
        return TelemetryData(
            drone_id="d1",
            timestamp=time.time(),
            latitude=28.5,
            longitude=77.1,
            altitude=6.0,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            heading=0.0,
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
        )
    fake_fc.get_telemetry = _get_telem

    fake_det = MagicMock()
    fake_det._available = True
    cfg = PerceptionConfig(enabled=True, decoupled_capture=False, max_frame_age_s=1.0, max_sync_skew_s=15.0)
    pipe = PerceptionPipeline("d1", fake_src, fake_det, fake_fc, MagicMock(), cfg)
    await pipe._step()
    assert pipe.last_rejection_reason is not None
    assert pipe.last_rejection_reason.startswith("STALE_FRAME")


@pytest.mark.asyncio
async def test_opencv_freshest_frame_with_slow_consumer():
    """Decoupled capture overwrites older frames so slow consumer sees the newest frame."""
    from DroneOS.perception.pipeline import PerceptionPipeline
    from DroneOS.shared.config.models import PerceptionConfig
    from DroneOS.shared.protocol.messages import TelemetryData

    frames = [
        np.full((32, 32, 3), 10, dtype=np.uint8),
        np.full((32, 32, 3), 20, dtype=np.uint8),
        np.full((32, 32, 3), 99, dtype=np.uint8),
    ]
    idx = 0

    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.get.return_value = 32.0

    def _read():
        nonlocal idx
        img = frames[min(idx, len(frames) - 1)]
        idx += 1
        return True, img

    cap.read.side_effect = _read

    fake_fc = MagicMock()
    async def _get_telem():
        return TelemetryData(
            drone_id="d1",
            timestamp=1000.0,
            latitude=28.5,
            longitude=77.1,
            altitude=6.0,
            roll=0.0,
            pitch=0.0,
            yaw=0.0,
            heading=0.0,
            flight_mode="OFFBOARD",
            armed_state="ARMED",
            gps_valid=True,
        )
    fake_fc.get_telemetry = _get_telem

    with patch("cv2.VideoCapture", return_value=cap):
        src = OpenCVFrameSource(device=0)
        cfg = PerceptionConfig(enabled=True, decoupled_capture=True)
        pipe = PerceptionPipeline("d1", src, MagicMock(), fake_fc, MagicMock(), cfg)
        pipe.start_capture()
        await asyncio.sleep(0.05)
        assert pipe._latest_frame is not None
        assert int(pipe._latest_frame.image[0, 0, 0]) == 99
        await pipe.stop()
        await src.close()

