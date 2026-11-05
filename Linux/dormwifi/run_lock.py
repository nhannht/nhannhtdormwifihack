"""Guarantees only one watcher runs at a time.

Two watchers is not a harmless duplicate. While a session is open both just read
``/status`` and agree, but at the moment one lapses both see "signed out" and
both post to ``/login``, and the router answers the second with its
too-many-sessions rejection.

This uses ``flock``, so the lock belongs to the process and the kernel drops it
however the process ends, crash included. A file holding a process id would go
stale on a crash and lock the user out of their own tool.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .settings import support_directory


class RunLock:
    """An exclusive lock on a file, held for as long as this object lives."""

    def __init__(self, descriptor: int, path: Path) -> None:
        self._descriptor = descriptor
        self.path = path

    def __del__(self) -> None:
        self.release()

    @classmethod
    def acquire(cls, path: Path | None = None) -> RunLock | None:
        """Take the lock, or return None when another watcher already holds it."""
        target = Path(path) if path else default_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return None

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            return None

        return cls(descriptor, target)

    def release(self) -> None:
        descriptor = getattr(self, "_descriptor", None)
        if descriptor is None:
            return
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def default_path() -> Path:
    return support_directory() / "watcher.lock"
