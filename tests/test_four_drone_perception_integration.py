"""
Integration tests for 4-drone ML perception pipeline instantiation and diagnostics.
Verifies requirements A1, A2, A4, A5, and A6:
1. All four packages (DroneOS, DroneOS1, DroneOS2, DroneOS3) build PerceptionPipeline
   with their respective drone_id, vehicle_name, and package-local detector.
2. Missing weights file reports DEGRADED for that specific drone only.
3. All four perception.yaml files share identical inference configuration.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import yaml

from DroneOS.shared.config.loader import load_yaml_config
from DroneOS.shared.config.models import DroneConfig, PerceptionConfig

REPO_ROOT = Path(__file__).resolve().parent.parent

DRONE_SPECS = [
    ("DroneOS", "drone1", "Drone1"),
    ("DroneOS1", "drone2", "Drone2"),
    ("DroneOS2", "drone3", "Drone3"),
    ("DroneOS3", "drone4", "Drone4"),
]


@pytest.mark.parametrize("pkg_name,expected_drone_id,expected_vehicle_name", DRONE_SPECS)
def test_each_drone_package_builds_perception_pipeline(pkg_name, expected_drone_id, expected_vehicle_name):
    """
    Verify each of DroneOS, DroneOS1, DroneOS2, DroneOS3 builds PerceptionPipeline
    with its own vehicle_name, drone_id, and package-local GarbageDetector class.
    """
    detector_mod = __import__(f"{pkg_name}.perception.detector", fromlist=["GarbageDetector"])
    pipeline_mod = __import__(f"{pkg_name}.perception.pipeline", fromlist=["PerceptionPipeline"])
    sources_mod = __import__(f"{pkg_name}.perception.frame_sources", fromlist=["AirSimFrameSource"])

    GarbageDetector = detector_mod.GarbageDetector
    PerceptionPipeline = pipeline_mod.PerceptionPipeline
    AirSimFrameSource = sources_mod.AirSimFrameSource

    # Verify detector comes from the correct package
    assert GarbageDetector.__module__ == f"{pkg_name}.perception.detector"
    assert PerceptionPipeline.__module__ == f"{pkg_name}.perception.pipeline"

    cfg_dir = REPO_ROOT / pkg_name / "configs"
    drone_cfg = load_yaml_config(cfg_dir / "drone.yaml", DroneConfig)
    perc_cfg = load_yaml_config(cfg_dir / "perception.yaml", PerceptionConfig)

    assert drone_cfg.drone_id == expected_drone_id
    assert drone_cfg.vehicle_name == expected_vehicle_name

    frame_src = AirSimFrameSource(
        vehicle_name=drone_cfg.vehicle_name,
        host="127.0.0.1",
        port=41451,
        camera_name=perc_cfg.camera.airsim_camera,
    )
    assert frame_src.vehicle_name == expected_vehicle_name

    detector = GarbageDetector(
        weights_path=str(REPO_ROOT / perc_cfg.weights),
        confidence=perc_cfg.confidence,
        imgsz=perc_cfg.imgsz,
    )

    pipeline = PerceptionPipeline(
        drone_id=drone_cfg.drone_id,
        frame_source=frame_src,
        detector=detector,
        flight_controller=AsyncMock(),
        network_adapter=AsyncMock(),
        config=perc_cfg,
    )

    assert pipeline.drone_id == expected_drone_id
    assert pipeline.frame_source.vehicle_name == expected_vehicle_name
    assert isinstance(pipeline.detector, GarbageDetector)


def test_missing_weights_file_reports_degraded_for_that_drone_only(tmp_path):
    """
    A drone with a missing weights file must report DEGRADED in get_perception_status(),
    while an adjacent drone with valid weights continues to report OK.
    """
    # Create fake weights file for drone 1
    valid_weights = tmp_path / "valid_model.pt"
    valid_weights.write_bytes(b"fake_pt_weights")

    missing_weights = tmp_path / "nonexistent_model.pt"

    from DroneOS.main import DroneOSApp
    from DroneOS1.main import DroneOSApp as DroneOS1App

    cfg1_dir = REPO_ROOT / "DroneOS" / "configs"
    cfg2_dir = REPO_ROOT / "DroneOS1" / "configs"

    # Mock ultralytics so valid weights doesn't fail on missing torch/ultralytics
    mock_ultralytics = MagicMock()
    mock_ultralytics.YOLO.return_value = MagicMock()

    with patch.dict("sys.modules", {"ultralytics": mock_ultralytics}):
        # Drone 1 with valid weights
        with patch("sys.argv", ["main.py", str(cfg1_dir)]):
            app1 = DroneOSApp()
            app1.perception_cfg.enabled = True
            app1.perception_cfg.weights = str(valid_weights)
            # Preflight status for drone1
            from DroneOS.perception.preflight import run_perception_preflight
            st1, rsn1 = run_perception_preflight(app1.perception_cfg, repo_root=REPO_ROOT)
            app1._perception_preflight_status = st1
            app1._perception_preflight_reason = rsn1

            # Detector load succeeds
            app1.perception_pipeline.detector._available = True
            status1 = app1.get_perception_status()
            assert status1.status == "OK" or status1.get("status") == "OK"

        # Drone 2 with missing weights
        with patch("sys.argv", ["main.py", str(cfg2_dir)]):
            app2 = DroneOS1App()
            app2.perception_cfg.enabled = True
            app2.perception_cfg.weights = str(missing_weights)
            from DroneOS1.perception.preflight import run_perception_preflight as run_preflight_d2
            st2, rsn2 = run_preflight_d2(app2.perception_cfg, repo_root=REPO_ROOT)
            app2._perception_preflight_status = st2
            app2._perception_preflight_reason = rsn2

            app2.perception_pipeline.detector._available = False
            status2 = app2.get_perception_status()
            assert status2.status == "DEGRADED"
            assert "weights" in status2.reason.lower() or "missing" in status2.reason.lower()

        # Drone 1 remains OK
        status1_recheck = app1.get_perception_status()
        assert status1_recheck.status == "OK"


def test_all_four_perception_yamls_have_identical_inference_settings():
    """
    Verify all four perception.yaml files across DroneOS, DroneOS1, DroneOS2, DroneOS3
    define identical core ML inference, safety gating, and camera settings.
    """
    yamls = {}
    for pkg in ["DroneOS", "DroneOS1", "DroneOS2", "DroneOS3"]:
        p_path = REPO_ROOT / pkg / "configs" / "perception.yaml"
        assert p_path.exists(), f"Missing perception.yaml in {pkg}"
        with open(p_path, "r", encoding="utf-8") as f:
            yamls[pkg] = yaml.safe_load(f)

    baseline = yamls["DroneOS"]
    assert baseline["enabled"] is True, "Perception must be enabled by default in DroneOS baseline"

    checked_keys = [
        "enabled",
        "weights",
        "imgsz",
        "confidence",
        "iou",
        "min_area_ratio",
        "max_area_ratio",
        "max_aspect_ratio",
        "min_altitude_m",
        "max_tilt_deg",
        "max_range_m",
        "merge_radius_m",
        "peer_exclusion_radius_m",
    ]

    for pkg in ["DroneOS1", "DroneOS2", "DroneOS3"]:
        for k in checked_keys:
            assert yamls[pkg].get(k) == baseline.get(k), (
                f"Mismatch in key '{k}' between DroneOS ({baseline.get(k)}) and {pkg} ({yamls[pkg].get(k)})"
            )

        # Check camera settings
        base_cam = baseline.get("camera", {})
        pkg_cam = yamls[pkg].get("camera", {})
        assert pkg_cam.get("airsim_camera") == base_cam.get("airsim_camera") == "bottom_center"
        assert pkg_cam.get("hfov_deg") == base_cam.get("hfov_deg") == 90.0
        assert pkg_cam.get("mount_pitch_deg") == base_cam.get("mount_pitch_deg") == 90.0


def test_all_four_drones_startup_and_health_message_reports_perception_status_ok(caplog):
    """
    Verify each of the four drones logs 'Perception pipeline initialized' with the right vehicle name,
    and its diagnostics/health report includes perception_status OK.
    """
    import logging
    mock_ultralytics = MagicMock()
    mock_ultralytics.YOLO.return_value = MagicMock()

    with patch.dict("sys.modules", {"ultralytics": mock_ultralytics}):
        for pkg_name, expected_drone_id, expected_vehicle_name in DRONE_SPECS:
            main_mod = __import__(f"{pkg_name}.main", fromlist=["DroneOSApp"])
            AppClass = main_mod.DroneOSApp
            cfg_dir = REPO_ROOT / pkg_name / "configs"

            with caplog.at_level(logging.INFO):
                with patch("sys.argv", ["main.py", str(cfg_dir)]):
                    app = AppClass()
                    assert app.perception_cfg.enabled is True
                    assert app.perception_pipeline is not None
                    assert app.node_id == expected_drone_id
                    assert app.drone_cfg.vehicle_name == expected_vehicle_name

                    # Verify startup log
                    expected_log = f"Perception pipeline initialized on {expected_drone_id} ({expected_vehicle_name})"
                    assert expected_log in caplog.text

                    # Simulate detector loaded OK
                    app.perception_pipeline.detector._available = True
                    status = app.get_perception_status()
                    assert status.status == "OK"

                    # Verify diagnostics health report contains perception_status OK
                    report = app.diagnostics.get_full_report()
                    assert "perception_status" in report
                    assert report["perception_status"]["status"] == "OK"

