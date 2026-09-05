import re

for drone, hz in [("DroneOS", 20.0), ("DroneOS1", 5.0), ("DroneOS2", 20.0)]:
    path = f"{drone}/tests/test_pipeline_timing.py"
    with open(path, "r") as f:
        content = f.read()

    # Add config.pipeline_hz = ...
    content = content.replace("    config = MagicMock()", f"    config = MagicMock()\n    config.pipeline_hz = {hz}")

    # Replace the test body
    old_body = """    # Let's run it for a short duration (0.25 seconds = 5 ticks)
    task = asyncio.create_task(pipeline.run_pipeline_loop())
    
    start = time.monotonic()
    await asyncio.sleep(0.25)
    pipeline.stop()
    await task
    end = time.monotonic()
    
    duration = end - start
    
    # Check that fc.get_telemetry and decision_engine.evaluate_tick were called multiple times
    call_count = fc.get_telemetry.call_count
    
    # At 20 Hz, 0.25 seconds should yield approximately 5 ticks.
    # We check for a reasonable bounds to account for async overhead
    assert 4 <= call_count <= 6, f"Pipeline blocked or ran too fast! Ticks: {call_count}"
    assert decision_engine.evaluate_tick.call_count == call_count, "Decision engine not evaluated every tick!"
"""

    new_body = """    # Let's run it for a duration that yields ~5 ticks based on actual configured hz
    duration = 5.0 / config.pipeline_hz
    task = asyncio.create_task(pipeline.run_pipeline_loop())
    
    start = time.monotonic()
    await asyncio.sleep(duration)
    pipeline.stop()
    await task
    end = time.monotonic()
    
    # Check that fc.get_telemetry and decision_engine.evaluate_tick were called multiple times
    call_count = fc.get_telemetry.call_count
    
    # At pipeline_hz, duration should yield approximately 5 ticks.
    # We check for a reasonable bounds to account for async overhead
    expected_ticks = int(duration * config.pipeline_hz)
    assert expected_ticks - 1 <= call_count <= expected_ticks + 1, f"Pipeline blocked or ran too fast! Ticks: {call_count}"
    assert decision_engine.evaluate_tick.call_count == call_count, "Decision engine not evaluated every tick!"
"""

    content = content.replace(old_body, new_body)

    with open(path, "w") as f:
        f.write(content)
