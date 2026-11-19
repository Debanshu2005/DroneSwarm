"""Root conftest.py for DroneSwarm test suite."""

import os
import pytest

@pytest.fixture(scope="session", autouse=True)
def configure_test_diagnostics(tmp_path_factory):
    """Ensure all diagnostic recording during tests writes to a clean temporary directory."""
    temp_dir = tmp_path_factory.mktemp("test_diagnostics")
    os.environ["PERCEPTION_DIAGNOSTICS_DIR"] = str(temp_dir)
    yield
    # Cleanup env var
    os.environ.pop("PERCEPTION_DIAGNOSTICS_DIR", None)
