from unittest.mock import AsyncMock
import pytest

from DroneOS1.sensors.health_monitor import HealthMonitor

@pytest.mark.asyncio
async def test_connection_lost_and_restored_callbacks_fire_once_per_transition():
    monitor = HealthMonitor(timeout_seconds=5.0)
    monitor.on_connection_lost = AsyncMock()
    monitor.on_connection_restored = AsyncMock()
    monitor.last_heartbeat_time = 10.0

    await monitor.evaluate_connection(now=16.0)
    await monitor.evaluate_connection(now=17.0)

    monitor.on_connection_lost.assert_awaited_once()
    monitor.on_connection_restored.assert_not_awaited()

    monitor.last_heartbeat_time = 18.0
    await monitor.evaluate_connection(now=19.0)
    await monitor.evaluate_connection(now=20.0)

    monitor.on_connection_restored.assert_awaited_once()
