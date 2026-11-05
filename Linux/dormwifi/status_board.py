"""What the watcher is doing right now, in the words the window shows.

Whatever is displaying the state reads this and never asks the router itself.
Two probes running side by side eventually disagree, and then the window and the
terminal are telling the user different stories about one connection.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import error_text
from .settings import support_directory

STARTING = "starting"
SIGNED_IN = "signedIn"
SIGNING_IN = "signingIn"
WRONG_NETWORK = "wrongNetwork"
ON_FALLBACK = "onFallback"
STOPPED = "stopped"
STOPPING = "stopping"


@dataclass
class WatcherStatus:
    """One snapshot of the watcher's state."""

    connection: str = STARTING

    #: The payload some states carry: the reason the router went quiet, or the
    #: name of the network the safety net put us on.
    detail: str = ""

    #: When the router said this session ends as a Unix timestamp, or None when
    #: it did not say.
    #:
    #: A moment, not a duration. The watcher can sleep a full minute between
    #: checks, so a stored "745 seconds left" is wrong by up to a minute by the
    #: time anyone reads it. A deadline stays true however long ago it was set.
    expires_at: float | None = None

    router_ip: str = ""
    network: str = ""
    has_internet: bool = False
    last_message: str = ""
    updated: float = field(default_factory=time.time)

    # MARK: - Reading

    @property
    def remaining_now(self) -> int | None:
        """How long this session has left, worked out now rather than when asked.

        Rounded, not truncated: truncating turns 744.99 seconds into 744 and the
        window reads one second short of the truth from the moment it is set.
        """
        if self.expires_at is None:
            return None
        return max(round(self.expires_at - time.time()), 0)

    @property
    def headline(self) -> str:
        """The one line at the top of the window."""
        if self.connection == STARTING:
            return "Starting"
        if self.connection == SIGNED_IN:
            left = self.remaining_now
            if left is None:
                return "Signed in"
            return f"Signed in, {error_text.duration(left)} left"
        if self.connection == SIGNING_IN:
            return "Signing in"
        if self.connection == WRONG_NETWORK:
            return "Not on the hotspot" if not self.network else f"Not on {self.network}"
        if self.connection == ON_FALLBACK:
            return f"On {self.detail}"
        if self.connection == STOPPED:
            return "Paused"
        if self.connection == STOPPING:
            return "Stopping"
        return "Starting"

    @property
    def is_stale(self) -> bool:
        """Too old to believe.

        A status is a snapshot, and the only thing that keeps it true is a loop
        writing a new one. When nothing is watching, ``expires_at`` keeps
        counting down all the same, so a screen that trusts an abandoned status
        ends up insisting "Signed in, 0s left" forever. The watcher writes at
        least once a minute, so nothing older than two is worth showing as
        current.
        """
        return time.time() - self.updated > 120

    @property
    def is_healthy(self) -> bool:
        """Whether the connection is currently good, for the window's indicator."""
        if self.connection == SIGNED_IN:
            return True
        if self.connection == ON_FALLBACK:
            return self.has_internet
        return False

    def differs_materially_from(self, other: WatcherStatus) -> bool:
        """Whether anything showing `other` would now be showing the wrong thing.

        Not plain inequality. The watcher rewrites ``expires_at`` on every
        heartbeat with the same deadline measured a minute later, so comparing
        whole statuses would call every heartbeat a change. What a bystander
        notices is the state changing, or a new session starting, and a new
        session moves the deadline by minutes rather than by the second or two
        that a fresh reading of the same session moves it.
        """
        if self.connection != other.connection:
            return True

        if self.expires_at is None and other.expires_at is None:
            return False
        if self.expires_at is not None and other.expires_at is not None:
            return abs(self.expires_at - other.expires_at) > 60
        return True

    def copy(self) -> WatcherStatus:
        duplicate = WatcherStatus(**asdict(self))
        return duplicate


class StatusBoard:
    """Broadcasts the watcher's state to whoever is displaying it.

    Writers publish, readers subscribe, and a late reader can ask for the
    current value rather than waiting for the next change. A window that is
    opened between two updates needs exactly that.
    """

    def __init__(self) -> None:
        self._current = WatcherStatus()
        self._subscribers: dict[int, Callable[[WatcherStatus], None]] = {}
        self._next_subscriber = 0
        self._lock = threading.RLock()

    def publish(self, status: WatcherStatus) -> WatcherStatus:
        stamped = status.copy()
        stamped.updated = time.time()
        with self._lock:
            self._current = stamped
            subscribers = list(self._subscribers.values())

        for callback in subscribers:
            try:
                callback(stamped)
            except Exception:  # noqa: BLE001 - a broken listener is not the board's problem
                continue
        return stamped

    def snapshot(self) -> WatcherStatus:
        with self._lock:
            return self._current.copy()

    def subscribe(self, callback: Callable[[WatcherStatus], None]) -> Callable[[], None]:
        """A live feed of changes, starting with the current value."""
        with self._lock:
            identifier = self._next_subscriber
            self._next_subscriber += 1
            self._subscribers[identifier] = callback
            current = self._current.copy()

        # Handed the current value straight away, because a window that opened
        # between two updates would otherwise show nothing until the next one.
        # Guarded like every other delivery: a listener that throws is that
        # listener's problem, not the board's.
        try:
            callback(current)
        except Exception:  # noqa: BLE001
            pass

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(identifier, None)

        return unsubscribe


class StatusFile:
    """The watcher's state, put where another process can read it.

    The window and the watcher are one process when the window runs it, so the
    board alone would do. They are not one process when the watcher runs as a
    systemd user service and the window is opened to look at it, and an
    in-process broadcast reaches nothing there. The file is what makes the
    window useful as a viewer.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else shared_path()

    def write(self, status: WatcherStatus) -> None:
        payload = asdict(status)
        payload["updated"] = time.time()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass

    def read(self) -> WatcherStatus | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return WatcherStatus(
            connection=str(payload.get("connection", STARTING)),
            detail=str(payload.get("detail", "")),
            expires_at=payload.get("expires_at"),
            router_ip=str(payload.get("router_ip", "")),
            network=str(payload.get("network", "")),
            has_internet=bool(payload.get("has_internet", False)),
            last_message=str(payload.get("last_message", "")),
            updated=float(payload.get("updated", 0.0)),
        )

    @property
    def changed_at(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None


def shared_path() -> Path:
    return support_directory() / "status.json"
