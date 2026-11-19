"""
Cross-platform operating system process locking for the Global Fusion Service (GFS).
Supports native Windows (msvcrt) and POSIX (fcntl).
"""

import os
import sys
import time
from pathlib import Path


class LockContentionError(Exception):
    """Raised when an exclusive process lock cannot be acquired."""
    pass


class CrossPlatformProcessLock:
    """
    Acquires an exclusive OS-level advisory lock on a lockfile.
    Automatically reclaims lock on process abnormal termination/kill.
    """
    def __init__(self, lock_file_path: str):
        self.lock_path = str(Path(lock_file_path).resolve())
        self._fp = None
        self._acquired = False

    def acquire(self, blocking: bool = False, timeout_s: float = 0.0) -> bool:
        """
        Attempts to acquire an exclusive lock.
        If non-blocking, returns True on success, False on contention.
        If acquisition fails, immediately closes file descriptor to prevent leaks on Windows.
        """
        # Ensure parent directory exists
        Path(self.lock_path).parent.mkdir(parents=True, exist_ok=True)

        if self._fp is None:
            self._fp = open(self.lock_path, "a+b")
            self._fp.seek(0, os.SEEK_END)
            # Ensure file has at least 1 byte so Windows byte locking operates on a valid byte range
            if self._fp.tell() == 0:
                self._fp.write(b"\x00")
                self._fp.flush()

        self._fp.seek(0)
        t_start = time.monotonic()

        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._acquired = True
                return True
            except (OSError, IOError):
                if not blocking or (time.monotonic() - t_start >= timeout_s):
                    # Clean up file descriptor immediately to prevent leaked handles on Windows
                    try:
                        self._fp.close()
                    except Exception:
                        pass
                    self._fp = None
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        """
        Releases the held exclusive lock. Idempotent.
        """
        if not self._acquired or self._fp is None:
            return

        try:
            self._fp.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        finally:
            self._acquired = False
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None

    def is_acquired(self) -> bool:
        return self._acquired

    def __enter__(self) -> "CrossPlatformProcessLock":
        if not self.acquire(blocking=False):
            raise LockContentionError(f"Could not acquire exclusive process lock on {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

