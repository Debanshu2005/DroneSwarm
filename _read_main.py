for path in ['DroneOS/main.py','DroneOS1/main.py','DroneOS2/main.py','DroneOS3/main.py']:
    with open(path) as f:
        lines = f.readlines()
    print(f"=== {path} ===")
    for i, l in enumerate(lines[125:135], start=126):
        print(f"{i}: {l}", end='')
    print()
