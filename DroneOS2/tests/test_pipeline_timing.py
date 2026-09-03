import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from DroneOS2.core.flight_pipeline import FlightPipeline
from DroneOS2.core.flight_state import FlightStateStore
from DroneOS2.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS2.shared.protocol.messages import TelemetryData

@pytest.fixture
def mock_deps():
    state_store = FlightStateStore()
    
    fc = MagicMock()
    fc.get_telemetry = AsyncMock(return_value=TelemetryData(flight_mode="GUIDED", gps_valid=True))
    fc.hover = AsyncMock()
    
    config = MagicMock()
    
    decision_engine = MagicMock()
    # Mock evaluate_tick to simulate a fast non-blocking operation
    decision_engine.evaluate_tick = AsyncMock()
    
    return state_store, fc, config, decision_engine

@pytest.mark.asyncio
async def test_pipeline_timing_non_blocking(mock_deps):
    state_store, fc, config, decision_engine = mock_deps
    
    # We want to prove that the pipeline ticks at approximately 20 Hz
    pipeline = FlightPipeline(state_store, fc, config, decision_engine)
    
    # Let's run it for a short duration (0.25 seconds = 5 ticks)
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

