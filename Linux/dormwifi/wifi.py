"""Joining a wireless network by name, through NetworkManager.

``nmcli`` rather than a D-Bus client, deliberately. It is the tool Linux users
already understand, it reports NetworkManager's own errors in its own words, and
every desktop that has NetworkManager has it. The macOS build does the same thing
with ``networksetup``, and the Windows original with ``netsh wlan connect``.

Only networks NetworkManager has already saved can be rejoined from here. That
is a real limit rather than a shortcut: joining a network needs its password, and
the whole point of this app is to run without the user watching. So the fallback
network has to be joined by hand once before the safety net can use it.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Literal

from . import default_route
from .activity_log import ActivityLog
from .settings import Settings

JoinKind = Literal["joined", "no_interface", "not_saved", "failed"]

#: How long to wait for an association, in seconds. Generous: the wait is one
#: subprocess call, and a join that is going to work usually takes a few seconds.
_JOIN_TIMEOUT = 30


@dataclass(frozen=True)
class JoinOutcome:
    kind: JoinKind
    reason: str = ""

    @property
    def joined(self) -> bool:
        return self.kind == "joined"

    @property
    def summary(self) -> str:
        if self.kind == "joined":
            return "joined"
        if self.kind == "no_interface":
            return "there is no Wi-Fi interface on this machine"
        if self.kind == "not_saved":
            return (
                f"{self.reason} is not one of this machine's saved networks. Join it by "
                "hand once, then this can rejoin it from then on"
            )
        return self.reason or "the join did not report why it failed"


def interface_name() -> str | None:
    """The Wi-Fi device, usually `wlan0` or `wlp2s0`."""
    output = _run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"], timeout=10)
    if output is None:
        return None
    for line in output.splitlines():
        columns = _split(line)
        if len(columns) >= 2 and columns[1] in ("wifi", "802-11-wireless"):
            return columns[0]
    return None


def saved_networks() -> list[str]:
    """Networks this machine has saved, most preferred first."""
    output = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout=10)
    if output is None:
        return []

    found: list[str] = []
    for line in output.splitlines():
        columns = _split(line)
        if len(columns) < 2:
            continue
        name, connection_type = columns[0], columns[1]
        if connection_type not in ("802-11-wireless", "wifi"):
            continue
        ssid = _connection_ssid(name) or name
        if ssid and ssid not in found:
            found.append(ssid)
    return found


def join(ssid: str) -> JoinOutcome:
    """Rejoin a network this machine has already saved."""
    if interface_name() is None:
        return JoinOutcome("no_interface")

    connection = _connection_named(ssid)
    if connection is None:
        return JoinOutcome("not_saved", reason=ssid)

    output = _run_or_error(
        ["nmcli", "--wait", "25", "connection", "up", "id", connection],
        timeout=_JOIN_TIMEOUT,
    )
    if output is None:
        return JoinOutcome("failed", reason="nmcli did not run")
    if isinstance(output, _Failure):
        return JoinOutcome("failed", reason=output.text)
    return JoinOutcome("joined")


def current_ssid() -> str | None:
    """Which network we are on, when NetworkManager will say.

    Only ever an optimisation: it saves a pointless rejoin when we are already
    where we want to be. Joining is what actually guarantees the network.
    """
    output = _run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"], timeout=15)
    if output is None:
        return None
    for line in output.splitlines():
        columns = _split(line)
        if len(columns) >= 2 and columns[0] == "yes" and columns[1]:
            return columns[1]
    return None


def _connection_named(ssid: str) -> str | None:
    """The NetworkManager connection whose SSID is `ssid`."""
    output = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout=10)
    if output is None:
        return None
    for line in output.splitlines():
        columns = _split(line)
        if len(columns) < 2 or columns[1] not in ("802-11-wireless", "wifi"):
            continue
        name = columns[0]
        if _connection_ssid(name) == ssid or name == ssid:
            return name
    return None


def _connection_ssid(name: str) -> str | None:
    output = _run(
        ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name], timeout=10
    )
    if output is None:
        return None
    text = output.strip()
    return text or None


@dataclass(frozen=True)
class _Failure:
    text: str


def _run(arguments: list[str], timeout: float) -> str | None:
    """Standard output of `arguments`, or None if it could not be run."""
    result = _run_or_error(arguments, timeout)
    if isinstance(result, _Failure) or result is None:
        return None
    return result


def _run_or_error(arguments: list[str], timeout: float) -> str | _Failure | None:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        complaint = (completed.stderr or completed.stdout or "").strip()
        return _Failure(complaint or f"{arguments[0]} exited with {completed.returncode}")
    return completed.stdout


def _split(line: str) -> list[str]:
    """Split one ``nmcli -t`` line, honouring its backslash escapes.

    ``nmcli -t`` writes one record per line with a colon between fields, and
    escapes a colon inside a value as ``\\:`` and a backslash as ``\\\\``.
    Splitting on the colon alone turns the SSID "Cafe: Guest" into two fields.
    """
    fields: list[str] = []
    current: list[str] = []
    escaped = False

    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)

    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


class WiFiJoiner:
    """Puts this machine back on the hotspot when the router stops answering.

    Joining takes a few seconds and drops the link while it happens, so a
    failing router does not get to trigger one every pass.
    """

    #: How long to leave a network alone between join attempts, in seconds.
    COOLDOWN_SECONDS = 30

    def __init__(
        self,
        settings: Settings,
        log: ActivityLog,
        cancel: threading.Event | None = None,
    ) -> None:
        self._settings = settings
        self._log = log
        self._last_attempt: float | None = None
        self._cancel = cancel or threading.Event()

    @property
    def settings(self) -> Settings:
        return self._settings

    def use(self, settings: Settings) -> None:
        self._settings = settings

    def join_configured_network(self) -> None:
        settings = self._settings
        if not settings.auto_connect or not settings.wifi_name:
            return
        self._join(settings.wifi_name, respecting_cooldown=True)

    def join_fallback_network(self) -> bool:
        """Join the safety net network.

        No cooldown and no ``auto_connect`` check. This runs when the internet
        has already been gone for fifteen seconds, which is the one moment where
        waiting politely is the wrong thing to do. ``auto_connect`` governs
        joining the hotspot we are trying to use; setting a fallback network is
        itself the permission to join that one.
        """
        wanted = self._settings.fallback_wifi_name
        if not wanted:
            return False
        return self._join(wanted, respecting_cooldown=False)

    def _join(self, wanted: str, *, respecting_cooldown: bool) -> bool:
        # Free when NetworkManager allows it, None when it does not.
        if current_ssid() == wanted:
            return True

        now = time.monotonic()
        if (
            respecting_cooldown
            and not self._settings.check_wifi_every_time
            and self._last_attempt is not None
            and now - self._last_attempt < self.COOLDOWN_SECONDS
        ):
            return False
        self._last_attempt = now

        self._log.info(f"Joining {wanted}.")
        outcome = join(wanted)

        if not outcome.joined:
            self._log.warning(f"Could not join {wanted}: {outcome.summary}")
            return False

        self._log.success(f"Joined {wanted}.")

        # Associating is not the same as being on the network. Wait for DHCP to
        # install a route, or the very next step asks which router we are on,
        # gets no answer, and calls it "no network at all".
        #
        # Running out is not an error and must not be reported as one: the caller
        # checks again, and on the run that produced the "handed out no address"
        # warning the address turned up seven seconds later and the login went
        # through.
        gateway = self._wait_for_gateway(up_to=45)
        if gateway:
            self._log.info(f"Network ready, router is {gateway}.")
        else:
            self._log.info(f"{wanted} has not handed out an address yet. Carrying on.")
        return True

    def _wait_for_gateway(self, up_to: int) -> str | None:
        for _ in range(max(up_to, 1)):
            found = default_route.gateway()
            if found:
                return found
            if self._cancel.wait(1):
                return None
        return default_route.gateway()
