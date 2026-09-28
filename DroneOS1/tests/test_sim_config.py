import os
import importlib
import pytest

@pytest.mark.parametrize("pkg", ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"])
def test_sim_profile_resolves_correctly(pkg, monkeypatch):
    monkeypatch.setenv("DRONEOS_PROFILE", "sim")
    
    pkg_profile = importlib.import_module(f"{pkg}.shared.config.profile")
    pkg_models = importlib.import_module(f"{pkg}.shared.config.models")
    from pathlib import Path
    config = pkg_profile.resolve_flight_config(Path(f"{pkg}/configs"), pkg_models.FlightConfig)
    
    assert config.adapter_type == "airsim"
    assert config.takeoff_altitude is not None
    assert config.max_velocity is not None
