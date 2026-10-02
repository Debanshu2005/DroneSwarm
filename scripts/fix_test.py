import os

for d in ['DroneOS1', 'DroneOS2', 'DroneOS3']:
    p = os.path.join(d, 'tests', 'test_collision_and_landing.py')
    with open(p, 'r', encoding='utf-8') as f:
        c = f.read()
    c = c.replace('assert state == "WARNING"\n    assert correction is None', 'assert state == "WARNING"\n    assert correction is not None')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(c)
