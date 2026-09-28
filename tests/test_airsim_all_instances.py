import pytest
import importlib
import yaml
import os
import sys

PACKAGES = ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]

@pytest.mark.parametrize("pkg", PACKAGES)
def test_airsim_adapter_instance(pkg):
    class FakeAirSim:
        class YawMode:
            def __init__(self, *args, **kwargs): pass
        class DrivetrainType:
            MaxDegreeOfFreedom = 0
        @staticmethod
        def to_eularian_angles(q): return (0,0,0)
        
    sys.modules['airsim'] = FakeAirSim

    factory_module = importlib.import_module(f"{pkg}.adapters.factory")
    AdapterFactory = factory_module.AdapterFactory
    
    models = importlib.import_module(f"{pkg}.shared.config.models")
    FlightConfig = models.FlightConfig
    DroneConfig = models.DroneConfig
    
    IFlightController = importlib.import_module(f"{pkg}.core.interfaces").IFlightController
    
    yaml_path = os.path.join(pkg, "configs", "drone.yaml")
    with open(yaml_path, "r") as f:
        drone_data = yaml.safe_load(f)
    
    drone_cfg = DroneConfig(**drone_data)
    flight_cfg = FlightConfig(
        adapter_type="airsim",
        takeoff_altitude=10.0,
        max_velocity=5.0,
        pipeline_hz=20.0,
        px4_connection_string="",
        airsim_host="127.0.0.1",
        airsim_port=41451
    )
    
    adapter = AdapterFactory.create_flight_controller(drone_cfg, flight_cfg)
    
    assert isinstance(adapter, IFlightController)
    assert type(adapter).__name__ == "AirSimFlightController"
    assert adapter.__class__.__module__.startswith(pkg)
    
    expected_vehicle = {"DroneOS": "Drone1", "DroneOS1": "Drone2", "DroneOS2": "Drone3", "DroneOS3": "Drone4"}
    assert adapter.vehicle_name == expected_vehicle[pkg]
    
    adapter_file = os.path.join(pkg, "adapters", "airsim_adapter.py")
    with open(adapter_file, "r") as f:
        content = f.read()
    
    for other_pkg in PACKAGES:
        if other_pkg != pkg:
            assert f"from {other_pkg}." not in content, f"Cross import found: {other_pkg} in {pkg}"
