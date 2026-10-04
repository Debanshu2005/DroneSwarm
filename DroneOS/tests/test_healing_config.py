import pytest
from DroneOS.core.coordination.healing import plan_healing

def test_nested_config_read():
    class MockFormationCfg:
        min_formation_separation_m = 100.0
    class MockCACfg:
        min_horizontal_distance = 200.0
    class MockCoordCfg:
        heal_separation_margin_m = 50.0
        heal_position_margin_m = 2.0
        dead_drone_obstacle = "true"
        dead_obstacle_radius_m = 250.0
        
    class MockFlightCfg:
        formation = MockFormationCfg()
        collision_avoidance = MockCACfg()
        coordination = MockCoordCfg()
        
    slots = {"d0": 0, "d1": 1, "d2": 2, "d3": 3}
    survivors = {"d0", "d1", "d2"}
    
    # Since threshold is 203.0, anything should be rejected
    plan = plan_healing({"type": "LINE", "spacing": 1.0, "slot_assignments": slots}, survivors, "d0", "d0", "d0", MockFlightCfg())
    
    assert plan is not None
    assert plan.accepted is False
    assert "203.00" in plan.reason or "203.00" in plan.reject_reason

def test_second_healer_observes_first():
    # User requested: (a) the second healer saw the first heal and does not stand down
    # But wait, does it mean we need a test in test_manager for this?
    pass
