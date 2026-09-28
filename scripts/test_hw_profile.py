import os
import sys
import yaml
from pathlib import Path
from DroneOS.shared.config.profile import resolve_flight_config
from DroneOS.shared.config.models import FlightConfig

config_dir = Path('DroneOS/configs')
os.environ['DRONEOS_PROFILE'] = 'hw'

with open(config_dir / 'flight.yaml', 'r') as f:
    orig = f.read()

try:
    with open(config_dir / 'flight.yaml', 'w') as f:
        f.write(orig.replace('adapter_type: "px4"', 'adapter_type: "airsim"'))
        
    cfg = resolve_flight_config(config_dir, FlightConfig)
    if cfg.adapter_type == 'airsim':
        print('Test D passed: HW profile allows airsim and only logs error')
    else:
        print('Failed Test D')
        sys.exit(1)
finally:
    with open(config_dir / 'flight.yaml', 'w') as f:
        f.write(orig)
