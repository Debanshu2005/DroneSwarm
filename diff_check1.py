import urllib.request
import difflib

url = "https://raw.githubusercontent.com/Debanshu2005/Autonomous_Drone_IIT/main/onboard_edge/trajectory_engine.py"
local_file = r"d:\CityGrid\my-project\PhoneOS_Swarm\DroneOS\shared\nlp\trajectory_engine.py"

try:
    with urllib.request.urlopen(url) as response:
        original = response.read().decode('utf-8').splitlines()
except Exception as e:
    print(f"Failed to fetch original: {e}")
    original = []

try:
    with open(local_file, "r", encoding="utf-8") as f:
        local = f.read().splitlines()
except Exception as e:
    print(f"Failed to read local: {e}")
    local = []

if original and local:
    diff = list(difflib.unified_diff(original, local, fromfile="original", tofile="local", n=0))
    if diff:
        print("Diff found:")
        for line in diff:
            print(line)
    else:
        print("Files are identical!")
