import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from DroneOS.core.flight_pipeline import FlightPipeline
from DroneOS.core.flight_state import FlightStateStore
from DroneOS.core.intents import FlightIntent, IntentSource, IntentAction
from DroneOS.shared.protocol.messages import TelemetryData

@pytest.fixture
def mock_deps():
    state_store = FlightStateStore()
    
    fc = MagicMock()
    fc.get_telemetry = AsyncMock(return_value=TelemetryData(flight_mode="GUIDED", gps_valid=True))
    fc.hover = AsyncMock()
    
    config = MagicMock()
    config.pipeline_hz = 20.0
    
    decision_engine = MagicMock()
    # Mock evaluate_tick to simulate a fast non-blocking operation
    decision_engine.evaluate_tick = AsyncMock()
    
    return state_store, fc, config, decision_engine

@pytest.mark.asyncio
async def test_pipeline_timing_non_blocking(mock_deps):
    state_store, fc, config, decision_engine = mock_deps
    
    # We want to prove that the pipeline ticks at approximately 20 Hz
    pipeline = FlightPipeline(state_store, fc, config, decision_engine)
    
    # Let's run it for a duration that yields ~5 ticks based on actual configured hz
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
