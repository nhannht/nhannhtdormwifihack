"""One line of the running commentary, and the file it is mirrored to."""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

Level = Literal["info", "success", "warning", "failure"]

#: How much history to keep on disk, in bytes.
_LONGEST_KEPT_FILE = 512 * 1024

@dataclass(frozen=True)
class LogEntry:
    date: dt.datetime
    level: Level
    message: str

    @property
    def formatted(self) -> str:
        """`[16:57:55] Gateway answered with a login page`"""
        return f"[{self.date:%H:%M:%S}] {self.message}"

    @property
    def for_file(self) -> str:
        """The same line as written to disk: `s [16:57:55] Logged in.`"""
        return f"{self.level[0]} {self.formatted}"

    @classmethod
    def parse_file_line(cls, line: str, on_day: dt.date | None = None) -> LogEntry | None:
        """Read a line back, in either the current shape or the older one.

        Returns None for anything that is not a log line, such as the
        ``--- session started at ... ---`` banner.
        """
        rest = line
        level: Level = "info"

        if len(rest) > 2 and rest[1] == " ":
            first = rest[0]
            match = {
                "i": "info",
                "s": "success",
                "w": "warning",
                "f": "failure",
            }.get(first)
            if match:
                level = match  # type: ignore[assignment]
                rest = rest[2:]

        if not rest.startswith("[") or "]" not in rest:
            return None

        clock = rest[1 : rest.index("]")]
        try:
            time = dt.datetime.strptime(clock, "%H:%M:%S").time()
        except ValueError:
            return None

        message = rest[rest.index("]") + 1 :].strip()
        if not message:
            return None

        # The file stores a clock time and no date, so put it on `on_day`.
        # Reading yesterday's tail would otherwise stamp every line with the
        # epoch and sort the list into nonsense.
        day = on_day or dt.date.today()
        return cls(date=dt.datetime.combine(day, time), level=level, message=message)


class ActivityLog:
    """Collects what the app is doing, for the window and for the log file.

    Safe to write from the watcher's thread while the window reads it: every
    public method takes the lock, and subscriber callbacks are invoked after it
    is released.
    """

    def __init__(
        self,
        file_path: Path | None = None,
        limit: int = 2000,
        truncate_file: bool = True,
    ) -> None:
        self._file_path = Path(file_path) if file_path else None
        self._limit = limit
        self._entries: list[LogEntry] = []
        self._subscribers: dict[int, Callable[[LogEntry], None]] = {}
        self._next_subscriber = 0
        self._lock = threading.RLock()

        if self._file_path is not None:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            if truncate_file:
                header = f"--- session started at {dt.datetime.now()} ---\n"
                self._file_path.write_text(header, encoding="utf-8")
            else:
                # Keeping the file means it grows forever, so drop the oldest
                # part of it now rather than letting one long-running install
                # fill the disk a line at a time.
                _trim(self._file_path, _LONGEST_KEPT_FILE)
                header = f"\n--- session started at {dt.datetime.now()} ---\n"
                with self._file_path.open("a", encoding="utf-8") as stream:
                    stream.write(header)

    # MARK: - Writing

    def log(self, level: Level, message: str) -> None:
        entry = LogEntry(date=dt.datetime.now(), level=level, message=message)

        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._limit:
                del self._entries[: len(self._entries) - self._limit]
            subscribers = list(self._subscribers.values())

        self._append_to_file(entry)

        for callback in subscribers:
            try:
                callback(entry)
            except Exception:  # noqa: BLE001 - a broken listener is not the log's problem
                continue

    def info(self, message: str) -> None:
        self.log("info", message)

    def success(self, message: str) -> None:
        self.log("success", message)

    def warning(self, message: str) -> None:
        self.log("warning", message)

    def failure(self, message: str) -> None:
        self.log("failure", message)

    # MARK: - Reading

    @property
    def all(self) -> list[LogEntry]:
        with self._lock:
            return list(self._entries)

    def recent(self, count: int) -> list[LogEntry]:
        with self._lock:
            return self._entries[-count:]

    def subscribe(self, callback: Callable[[LogEntry], None]) -> Callable[[], None]:
        """A live feed of entries logged from now on, plus a way to stop it."""
        with self._lock:
            identifier = self._next_subscriber
            self._next_subscriber += 1
            self._subscribers[identifier] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(identifier, None)

        return unsubscribe

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    def _append_to_file(self, entry: LogEntry) -> None:
        path = self._file_path
        if path is None:
            return
        try:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(entry.for_file + "\n")
        except OSError:
            # A log that cannot be written is not a reason to stop watching.
            pass


def entries_in_file(path: Path, limit: int = 300) -> list[LogEntry]:
    """The last `limit` lines of a log file, as entries."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    parsed = [LogEntry.parse_file_line(line) for line in lines[-limit * 2 :]]
    return [entry for entry in parsed if entry is not None][-limit:]


def last_run_offset(path: Path) -> int:
    """Where the currently running session's banner starts, in characters."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    marker = text.rfind("--- session started at")
    return marker if marker >= 0 else 0


def _trim(path: Path, keep_bytes: int) -> None:
    """Keep only the last `keep_bytes` of the file, starting at a line boundary."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= keep_bytes:
        return

    with path.open("rb") as stream:
        stream.seek(size - keep_bytes)
        tail = stream.read()

    newline = tail.find(b"\n")
    kept = tail[newline + 1 :] if newline >= 0 else tail
    path.write_bytes(kept)
