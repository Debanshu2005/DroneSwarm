"""Unit tests for Perception FrameSources (AirSim, OpenCV, ImageFolder)."""

import asyncio
from pathlib import Path
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

