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


@pytest.mark.parametrize("pkg, port, relay_port", [
    ("DroneOS", 14550, 14650),
    ("DroneOS1", 14551, 14651),
    ("DroneOS2", 14552, 14652),
    ("DroneOS3", 14553, 14653),
])
def test_sim_network_uses_unique_loopback_endpoints(pkg, port, relay_port, monkeypatch):
    monkeypatch.setenv("DRONEOS_PROFILE", "sim")
    profile = importlib.import_module(f"{pkg}.shared.config.profile")
    models = importlib.import_module(f"{pkg}.shared.config.models")
    from pathlib import Path

    config = profile.resolve_network_config(Path(f"{pkg}/configs"), models.NetworkConfig)

    assert (config.host, config.port) == ("127.0.0.1", port)
    assert ("127.0.0.1", relay_port) in config.peer_endpoints
    assert len(config.peer_endpoints) == 4
