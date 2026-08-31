import asyncio
import inspect
from typing import Callable, Coroutine, Any, Optional
from DroneOS.shared.protocol.messages import TelemetryData
from DroneOS.shared.utils.logger import setup_logger

logger = setup_logger("GpsMonitor")

class GpsMonitor:
    """
    Monitors GPS validity and triggers callbacks on degraded/restored transitions.
    """
    def __init__(self, check_interval: float = 1.0):
        self.check_interval = check_interval
        self.on_gps_degraded: Callable[[], Coroutine[Any, Any, None]] = None
        self.on_gps_restored: Callable[[], Coroutine[Any, Any, None]] = None
        self._running = False
        self._gps_degraded = False

    async def evaluate_telemetry(self, telemetry: Optional[TelemetryData]) -> None:
        if telemetry is None or getattr(telemetry, "gps_valid", None) is None:
            return

        if not telemetry.gps_valid:
            if not self._gps_degraded:
                self._gps_degraded = True
                logger.warning("GPS degraded.")
                if self.on_gps_degraded:
                    await self.on_gps_degraded()
        elif self._gps_degraded:
            self._gps_degraded = False
            logger.info("GPS restored.")
            if self.on_gps_restored:
                await self.on_gps_restored()

    async def start(self, get_telemetry: Callable[[], Any]) -> None:
        self._running = True
        self._gps_degraded = False
        logger.info("GPS Monitor started.")

        while self._running:
            try:
                telemetry = get_telemetry()
                if inspect.isawaitable(telemetry):
                    telemetry = await telemetry
                await self.evaluate_telemetry(telemetry)
            except asyncio.CancelledError:
                logger.info("GPS Monitor loop cancelled.")
                break
            except Exception as e:
                logger.exception(f"Error checking GPS: {e}")

            await asyncio.sleep(self.check_interval)

    def stop(self) -> None:
        self._running = False
        logger.info("GPS Monitor stopped.")
