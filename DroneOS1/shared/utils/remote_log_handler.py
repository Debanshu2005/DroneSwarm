import logging
import asyncio
import time
from typing import Dict, Tuple
from DroneOS1.shared.protocol.messages import StatusMessage

class RemoteLogHandler(logging.Handler):
    def __init__(self, node_id: str, network, loop: asyncio.AbstractEventLoop, window_sec: float = 5.0):
        super().__init__(level=logging.ERROR)
        self.node_id = node_id
        self.network = network
        self.loop = loop
        self.window_sec = window_sec
        self.queue = asyncio.Queue()
        self._running = True
        self.rate_limit_state: Dict[str, Tuple[int, float]] = {}

    def emit(self, record: logging.LogRecord):
        if not self._running:
            return
        # Ensure thread-safe non-blocking queueing
        self.loop.call_soon_threadsafe(self.queue.put_nowait, record)

    async def drain_task(self):
        while self._running:
            try:
                record = await self.queue.get()
                current_time = time.time()
                
                # Rate limit key based on logger name, line number, and prefix (first 30 chars of msg)
                msg_str = str(record.getMessage())
                prefix = msg_str[:30]
                key = f"{record.name}:{record.lineno}:{prefix}"
                
                count, last_time = self.rate_limit_state.get(key, (0, 0.0))
                
                if current_time - last_time < self.window_sec:
                    # Suppress
                    self.rate_limit_state[key] = (count + 1, last_time)
                else:
                    # Emit
                    if count > 0:
                        msg_str += f" (+{count} similar suppressed)"
                    
                    self.rate_limit_state[key] = (0, current_time)
                    
                    severity = "critical" if record.levelno >= logging.CRITICAL else "error"
                    
                    status_msg = StatusMessage(
                        sender_id=self.node_id,
                        timestamp=current_time,
                        status_text=msg_str,
                        severity=severity
                    )
                    
                    await self.network.broadcast_message(status_msg)
            except asyncio.CancelledError:
                break
            except Exception:
                # Do not block or spam logs on internal failure
                pass

    def stop(self):
        self._running = False
