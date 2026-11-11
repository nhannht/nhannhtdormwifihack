"""Guarantees only one watcher runs at a time.

Two watchers is not a harmless duplicate. While a session is open both just read
``/status`` and agree, but at the moment one lapses both see "signed out" and
both post to ``/login``, and the router answers the second with its
too-many-sessions rejection.

On Windows this is a one-byte ``msvcrt.locking`` lock on a file, held for as long
as the descriptor is open. Windows drops it when the handle closes, however the
process ends, crash included. A file holding a process id would go stale on a
crash and lock the user out of their own tool.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .settings import support_directory

try:
    import msvcrt
except ImportError:  # pragma: no cover - the tests also run on a non-Windows box
    msvcrt = None

#: Windows locks are byte ranges on a file handle. Two handles in this *same*
#: process are allowed to take the same range in some cases, so the process also
#: remembers the paths it holds. The file lock is what stops another process;
#: this is what stops a second window or a test.
_HELD: set[str] = set()
_HELD_LOCK = threading.Lock()


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
        key = str(target.resolve())

        with _HELD_LOCK:
            if key in _HELD:
                return None
            _HELD.add(key)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            with _HELD_LOCK:
                _HELD.discard(key)
            return None

        try:
            # `msvcrt.locking` locks one byte from the current position. Make
            # sure the file has that byte, then lock byte zero.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if msvcrt is None:
                raise OSError("msvcrt is not available")
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(descriptor)
            with _HELD_LOCK:
                _HELD.discard(key)
            return None

        return cls(descriptor, target)

    def release(self) -> None:
        descriptor = getattr(self, "_descriptor", None)
        if descriptor is None:
            return
        self._descriptor = None
        key = str(self.path.resolve())
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if msvcrt is not None:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            os.close(descriptor)
            with _HELD_LOCK:
                _HELD.discard(key)


def default_path() -> Path:
    return support_directory() / "watcher.lock"
