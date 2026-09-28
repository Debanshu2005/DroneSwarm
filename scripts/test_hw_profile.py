import os
import sys
import yaml
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from DroneOS.shared.config.profile import resolve_flight_config
from DroneOS.shared.config.models import FlightConfig

def test_hw_profile():
    config_dir = Path('DroneOS/configs')
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_config_dir = Path(temp_dir) / 'configs'
        shutil.copytree(config_dir, temp_config_dir)
        
        flight_yaml = temp_config_dir / 'flight.yaml'
        with open(flight_yaml, 'r') as f:
            orig = f.read()
            
        with open(flight_yaml, 'w') as f:
            f.write(orig.replace('adapter_type: "px4"', 'adapter_type: "airsim"'))
            
        # Test HW profile allows airsim and only logs error
        os.environ['DRONEOS_PROFILE'] = 'hw'
        cfg = resolve_flight_config(temp_config_dir, FlightConfig)
        assert cfg.adapter_type == 'airsim', "HW profile did not allow airsim"
        
        # Test SIM profile exits with code 1 if px4 is resolved
        with open(flight_yaml, 'w') as f:
            f.write(orig)
            
        sim_yaml = temp_config_dir / 'flight.sim.yaml'
        if sim_yaml.exists():
            sim_yaml.unlink()
            
        os.environ['DRONEOS_PROFILE'] = 'sim'
        try:
            cfg = resolve_flight_config(temp_config_dir, FlightConfig)
            assert False, "SIM profile did not exit with code 1 on px4"
        except SystemExit as e:
            assert e.code == 1, f"SIM profile exited with {e.code} instead of 1"
            
    print('Tests passed.')

if __name__ == "__main__":
    test_hw_profile()
