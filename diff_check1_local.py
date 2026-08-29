import difflib

local_file = r"d:\CityGrid\my-project\PhoneOS_Swarm\DroneOS\shared\nlp\trajectory_engine.py"
ref_file = r"d:\CityGrid\my-project\Autonomous_Drone_IIT\onboard_edge\trajectory_engine.py"

try:
    with open(ref_file, "r", encoding="utf-8") as f:
        original = f.read().splitlines()
except Exception as e:
    print(f"Failed to read ref: {e}")
    original = []

try:
    with open(local_file, "r", encoding="utf-8") as f:
        local = f.read().splitlines()
except Exception as e:
    print(f"Failed to read local: {e}")
    local = []

if original and local:
    diff = list(difflib.unified_diff(original, local, fromfile="Autonomous_Drone_IIT", tofile="PhoneOS_Swarm", n=0))
    if diff:
        print("Diff found:")
        for line in diff:
            print(line)
    else:
        print("Files are identical!")
