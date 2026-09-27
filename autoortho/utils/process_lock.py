"""Advisory file locks shared by every AutoOrtho process.

The operating system releases these locks when the owning process exits, so a
crashed parent or mount worker can never leave a stale lock behind.
"""

import os
import threading
import time
from typing import Optional

if os.name == "nt":
    import msvcrt
else:
    import fcntl

# Blocking acquisition polls because the standard library has no portable
# cross-process wait primitive. Callers only block from background threads.
_RETRY_INTERVAL_SEC = 0.25


class ProcessLock:
    """Exclusive, non-reentrant lock on ``path`` across processes.

    Two instances conflict even inside one process, because the lock belongs
    to the open file handle. Do not share an instance between threads.
    """

    def __init__(self, path: str):
        self.path = path
        self._file = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(
        self,
        blocking: bool = True,
        timeout: Optional[float] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> bool:
        """Take the lock; return False on timeout, stop request or contention."""
        if self._file is not None:
            raise RuntimeError(f"ProcessLock already held: {self.path}")
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        lock_file = open(self.path, "a+b")
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                if self._try_lock(lock_file):
                    self._file = lock_file
                    return True
                if not blocking:
                    break
                wait = _RETRY_INTERVAL_SEC
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    wait = min(wait, remaining)
                if stop_event is not None:
                    if stop_event.wait(wait):
                        break
                else:
                    time.sleep(wait)
        except BaseException:
            lock_file.close()
            raise
        lock_file.close()
        return False

    def release(self) -> None:
        lock_file, self._file = self._file, None
        if lock_file is None:
            return
        try:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            lock_file.close()

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    @staticmethod
    def _try_lock(lock_file) -> bool:
        try:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
