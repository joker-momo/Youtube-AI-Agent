from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import threading
import time
from typing import Iterator


class FileLockTimeout(TimeoutError):
    """Raised when a file lock cannot be acquired within the timeout."""


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def file_lock(lock_path: Path, timeout_sec: float = 30.0) -> Iterator[None]:
    """Acquire an exclusive lock for ``lock_path``.

    The in-process lock covers threads, while ``flock`` covers separate worker
    processes on the same Mac/Linux host.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(lock_path)
    deadline = time.monotonic() + max(0.0, timeout_sec)

    while True:
        if thread_lock.acquire(blocking=False):
            break
        if time.monotonic() >= deadline:
            raise FileLockTimeout(f"Timed out acquiring lock: {lock_path}")
        time.sleep(0.01)

    handle = None
    try:
        handle = lock_path.open("a+b")
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(f"Timed out acquiring lock: {lock_path}") from exc
                time.sleep(0.01)
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        thread_lock.release()
