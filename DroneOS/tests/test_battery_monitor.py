from unittest.mock import AsyncMock
import pytest

from DroneOS.sensors.battery_monitor import BatteryMonitor

@pytest.mark.asyncio
async def test_transient_low_battery_dip_does_not_fire():
    now = [0.0]
    monitor = BatteryMonitor(debounce_seconds=2.0, clock=lambda: now[0])
    monitor.on_low_battery = AsyncMock()

    await monitor.evaluate_level(19.0)
    now[0] = 3.0
    await monitor.evaluate_level(50.0)

    monitor.on_low_battery.assert_not_awaited()

@pytest.mark.asyncio
async def test_sustained_low_battery_fires_once_until_recovery():
    now = [0.0]
    monitor = BatteryMonitor(debounce_seconds=2.0, clock=lambda: now[0])
    monitor.on_low_battery = AsyncMock()

    await monitor.evaluate_level(19.0)
    now[0] = 2.1
    await monitor.evaluate_level(19.0)
    now[0] = 4.0
    await monitor.evaluate_level(19.0)

    monitor.on_low_battery.assert_awaited_once()

@pytest.mark.asyncio
async def test_low_battery_recovery_resets_latch():
    now = [0.0]
    monitor = BatteryMonitor(debounce_seconds=2.0, clock=lambda: now[0])
    monitor.on_low_battery = AsyncMock()

    await monitor.evaluate_level(19.0)
    now[0] = 2.1
    await monitor.evaluate_level(19.0)
    await monitor.evaluate_level(50.0)
    now[0] = 10.0
    await monitor.evaluate_level(19.0)
    now[0] = 12.1
    await monitor.evaluate_level(19.0)

    assert monitor.on_low_battery.await_count == 2

@pytest.mark.asyncio
async def test_sustained_critical_battery_fires_once():
    now = [0.0]
    monitor = BatteryMonitor(debounce_seconds=2.0, clock=lambda: now[0])
    monitor.on_critical_battery = AsyncMock()

    await monitor.evaluate_level(9.0)
    now[0] = 2.1
    await monitor.evaluate_level(9.0)
    now[0] = 4.0
    await monitor.evaluate_level(9.0)

    monitor.on_critical_battery.assert_awaited_once()
