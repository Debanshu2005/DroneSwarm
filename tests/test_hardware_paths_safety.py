"""
Tests to protect production hardware execution paths against simulation pollution.
Verifies that:
1. Relay CLI defaults point to production paths (data/relay_acks.db, data/gfs.db).
2. Start scripts do not kill external processes by name or port, and use hardware paths under non-sim profiles.
3. AdapterFactory selects PX4FlightController on hardware and only selects AirSimFlightController under sim profile.
"""

import ast
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_relay_cli_defaults():
    """Verify relay/relay.py defaults to production database paths."""
    relay_file = REPO_ROOT / "relay" / "relay.py"
    assert relay_file.exists(), "relay/relay.py must exist"

    # Parse AST to verify default argument values in argparse definition
    tree = ast.parse(relay_file.read_text(encoding="utf-8"))
    
    ack_db_default = None
    gfs_db_default = None
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = getattr(node.func, "attr", None)
            if func == "add_argument":
                args = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
                if "--relay-ack-db" in args:
                    for kw in node.keywords:
                        if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                            ack_db_default = kw.value.value
                elif "--gfs-db-path" in args:
                    for kw in node.keywords:
                        if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                            gfs_db_default = kw.value.value

    assert ack_db_default == "data/relay_acks.db", f"Expected default relay ACK DB to be 'data/relay_acks.db', got {ack_db_default}"
    assert gfs_db_default == "data/gfs.db", f"Expected default GFS DB to be 'data/gfs.db', got {gfs_db_default}"


@pytest.mark.parametrize("script_name", ["start_drone1.py", "start_drone2.py", "start_drone3.py", "start_drone4.py"])
def test_start_scripts_do_not_kill_external_processes(script_name):
    """Verify start scripts do not scan and kill external processes by name or port."""
    script_file = REPO_ROOT / script_name
    assert script_file.exists()
    content = script_file.read_text(encoding="utf-8")
    tree = ast.parse(content)

    # Ensure no calls to psutil.process_iter or killing external processes
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = getattr(node.func, "attr", None)
            if func_name == "process_iter":
                pytest.fail(f"{script_name} must NOT call psutil.process_iter to inspect external processes")
            if func_name == "kill":
                # Only relay_proc.kill() is permitted (child process management)
                target = getattr(node.func, "value", None)
                target_id = getattr(target, "id", None)
                assert target_id == "relay_proc", f"{script_name} called .kill() on non-child object: {target_id}"


def test_run_sim_swarm_does_not_kill_by_name():
    """Verify run_sim_swarm.py only manages its own child processes and does not kill by name."""
    script_file = REPO_ROOT / "scripts" / "run_sim_swarm.py"
    content = script_file.read_text(encoding="utf-8")
    assert "process_iter" not in content, "run_sim_swarm.py must not iterate over OS processes"
    assert "relay.py" not in content or "Popen" in content, "run_sim_swarm.py must not filter processes by relay.py"


@pytest.mark.parametrize("script_name", ["start_drone1.py", "start_drone2.py", "start_drone3.py", "start_drone4.py"])
def test_start_scripts_hardware_mode_keeps_clean_relay_args(script_name):
    """When DRONEOS_PROFILE is hardware (e.g. 'hw' or unset), start scripts do not inject sim DB overrides."""
    script_file = REPO_ROOT / script_name
    content = script_file.read_text(encoding="utf-8")
    
    # In hardware mode (not is_sim), relay_args = sys.argv[1:] without --relay-ack-db drone-specific overrides
    assert "if is_sim:" in content
    assert "--relay-ack-db" in content
    # Ensure sim flags are inside if is_sim:
    sim_idx = content.find("if is_sim:")
    db_idx = content.find("--relay-ack-db")
    assert sim_idx != -1 and db_idx > sim_idx, "Sim-specific database overrides must only be applied when is_sim is True"


def test_adapter_factory_hardware_selects_px4():
    """AdapterFactory must select PX4FlightController for px4/mavsdk and AirSim only for airsim."""
    from DroneOS.adapters.factory import AdapterFactory
    from DroneOS.adapters.px4_adapter import PX4FlightController
    from DroneOS.adapters.airsim_adapter import AirSimFlightController
    from DroneOS.shared.config.models import DroneConfig, FlightConfig

    drone_cfg = DroneConfig(drone_id="drone1", vehicle_name="Drone1")
    
    # PX4 / MAVSDK configs (hardware)
    px4_cfg = FlightConfig(
        adapter_type="px4",
        takeoff_altitude=5.0,
        max_velocity=2.0,
        px4_connection_string="serial:///dev/serial0:115200",
        airsim_host="127.0.0.1",
        airsim_port=41451,
    )
    fc_px4 = AdapterFactory.create_flight_controller(drone_cfg, px4_cfg)
    assert isinstance(fc_px4, PX4FlightController), "Hardware profile (px4) must initialize PX4FlightController"

    mavsdk_cfg = FlightConfig(
        adapter_type="mavsdk",
        takeoff_altitude=5.0,
        max_velocity=2.0,
        px4_connection_string="serial:///dev/serial0:115200",
        airsim_host="127.0.0.1",
        airsim_port=41451,
    )
    fc_mavsdk = AdapterFactory.create_flight_controller(drone_cfg, mavsdk_cfg)
    assert isinstance(fc_mavsdk, PX4FlightController), "Hardware profile (mavsdk) must initialize PX4FlightController"

    # AirSim config (simulation only)
    sim_cfg = FlightConfig(
        adapter_type="airsim",
        takeoff_altitude=5.0,
        max_velocity=2.0,
        px4_connection_string="serial:///dev/serial0:115200",
        airsim_host="127.0.0.1",
        airsim_port=41451,
    )
    fc_sim = AdapterFactory.create_flight_controller(drone_cfg, sim_cfg)
    assert isinstance(fc_sim, AirSimFlightController), "Simulation profile must initialize AirSimFlightController"


def test_base_flight_config_defaults_to_hardware_adapter():
    """Default DroneOS/configs/flight.yaml must use px4/mavsdk adapter, not airsim."""
    from DroneOS.shared.config.loader import load_yaml_config
    from DroneOS.shared.config.models import FlightConfig

    config_path = REPO_ROOT / "DroneOS" / "configs" / "flight.yaml"
    cfg = load_yaml_config(config_path, FlightConfig)
    assert cfg.adapter_type.lower() in ("px4", "mavsdk"), f"Default flight.yaml adapter_type must be px4 or mavsdk, got: {cfg.adapter_type}"
