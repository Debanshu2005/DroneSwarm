import asyncio
import time
from typing import Callable, Coroutine, Any, Optional
from DroneOS.shared.utils.logger import setup_logger

logger = setup_logger("BatteryMonitor")

class BatteryMonitor:
    """
    Monitors battery levels and triggers callbacks if it falls below critical thresholds.
    """
    def __init__(
        self,
        check_interval: float = 1.0,
        low_threshold: float = 20.0,
        critical_threshold: float = 10.0,
        debounce_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.check_interval = check_interval
        self.low_threshold = low_threshold
        self.critical_threshold = critical_threshold
        self.debounce_seconds = debounce_seconds
        self.clock = clock
        self.on_low_battery: Callable[[], Coroutine[Any, Any, None]] = None
        self.on_critical_battery: Callable[[], Coroutine[Any, Any, None]] = None
        self._running = False
        self._low_since: Optional[float] = None
        self._critical_since: Optional[float] = None
        self._low_triggered = False
        self._critical_triggered = False

    async def evaluate_level(self, level: Optional[float]) -> None:
        if level is None:
            return

        now = self.clock()
        if level > self.low_threshold:
            self._low_since = None
            self._critical_since = None
            self._low_triggered = False
            self._critical_triggered = False
            return

        if level <= self.critical_threshold:
            self._low_since = None
            if self._critical_since is None:
                self._critical_since = now
            if not self._critical_triggered and now - self._critical_since >= self.debounce_seconds:
                self._critical_triggered = True
                logger.warning(f"CRITICAL BATTERY: {level}%")
                if self.on_critical_battery:
                    await self.on_critical_battery()
            return

        self._critical_since = None
        self._critical_triggered = False
        if self._low_since is None:
            self._low_since = now
        if not self._low_triggered and now - self._low_since >= self.debounce_seconds:
            self._low_triggered = True
            logger.warning(f"LOW BATTERY: {level}%")
            if self.on_low_battery:
                await self.on_low_battery()
        
    async def start(self, get_battery_level: Callable[[], Optional[float]]) -> None:
        self._running = True
        logger.info("Battery Monitor started.")
        
        while self._running:
            try:
                level = get_battery_level()
                await self.evaluate_level(level)
            except asyncio.CancelledError:
                logger.info("Battery Monitor loop cancelled.")
                break
            except Exception as e:
                logger.exception(f"Error checking battery: {e}")
                
            await asyncio.sleep(self.check_interval)
            
    def stop(self) -> None:
        self._running = False
        logger.info("Battery Monitor stopped.")
