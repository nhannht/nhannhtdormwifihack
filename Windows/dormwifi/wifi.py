"""Joining a wireless network by name, through ``netsh wlan``.

``netsh`` rather than the WLAN API, deliberately. It is the tool Windows users
already understand, it reports the WLAN service's own errors in its own words,
and every Windows install has it. The Linux build does the same thing with
``nmcli``, and the macOS build with ``networksetup``.

Only networks Windows has already saved can be rejoined from here. That is a
real limit rather than a shortcut: joining a network needs its password, and the
whole point of this app is to run without the user watching. So the fallback
network has to be joined by hand once before the safety net can use it.
"""

from __future__ import annotations

import os
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

#: Keeps `netsh` from flashing a console window when the watcher has none.
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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
    """The Wi-Fi device, usually ``Wi-Fi``."""
    output = _run(["wlan", "show", "interfaces"], timeout=15)
    if output is None:
        return None
    for line in output.splitlines():
        columns = line.strip().split(":", 1)
        if len(columns) == 2 and columns[0].strip().lower() == "name":
            name = columns[1].strip()
            if name:
                return name
    return None


def saved_networks() -> list[str]:
    """Networks this machine has saved, in the order Windows lists them."""
    output = _run(["wlan", "show", "profiles"], timeout=15)
    if output is None:
        return []
    return parse_profiles(output)


def parse_profiles(output: str) -> list[str]:
    """The profile names out of ``netsh wlan show profiles``.

    Every real profile line is ``<label> : <name>``. The interface header also
    ends in a colon but has nothing after it, and the section headings have no
    colon at all.
    """
    found: list[str] = []
    for line in output.splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        value = value.strip()
        if not value:
            continue
        label = label.strip().lower()
        if label.startswith("profiles on interface") or label.startswith("interface name"):
            continue
        if not any(existing.lower() == value.lower() for existing in found):
            found.append(value)
    return found


def current_ssid() -> str | None:
    """Which network we are on, when Windows will say.

    Only ever an optimisation: it saves a pointless rejoin when we are already
    where we want to be. Joining is what actually guarantees the network.
    """
    output = _run(["wlan", "show", "interfaces"], timeout=15)
    if output is None:
        return None
    return current_ssid_from(output)


def current_ssid_from(output: str) -> str | None:
    for line in output.splitlines():
        trimmed = line.strip()
        if not trimmed.startswith("SSID") or trimmed.startswith("BSSID"):
            continue
        columns = trimmed.split(":", 1)
        if len(columns) == 2:
            value = columns[1].strip()
            if value:
                return value
    return None


def join(ssid: str) -> JoinOutcome:
    """Rejoin a network this machine has already saved."""
    interfaces = _run(["wlan", "show", "interfaces"], timeout=15)
    if interfaces is None or not _interface_present(interfaces):
        return JoinOutcome("no_interface")

    profile = _connection_named(ssid)
    if profile is None:
        return JoinOutcome("not_saved", reason=ssid)

    output = _run_or_error(
        [
            "wlan",
            "connect",
            # No embedded quotes: subprocess quotes the whole argument because it
            # contains a space, and netsh then sees `name=INET - Free WiFi`. With
            # quotes inside the argument netsh takes them literally and answers
            # with its usage text instead of joining.
            f"name={profile}",
            f"ssid={ssid}",
        ],
        timeout=_JOIN_TIMEOUT,
    )
    if output is None:
        return JoinOutcome("failed", reason="netsh did not run")
    if isinstance(output, _Failure):
        return JoinOutcome("failed", reason=output.text)
    return JoinOutcome("joined")


def _connection_named(ssid: str) -> str | None:
    for saved in saved_networks():
        if saved.lower() == ssid.lower():
            return saved
    return None


def _interface_present(output: str) -> bool:
    for line in output.splitlines():
        trimmed = line.strip().lower()
        if trimmed.startswith(("name", "description", "ssid", "state")):
            return True
    return False


@dataclass(frozen=True)
class _Failure:
    text: str


def _run(arguments: list[str], timeout: float) -> str | None:
    """Standard output of ``arguments``, or None if it could not be run."""
    result = _run_or_error(arguments, timeout)
    if isinstance(result, _Failure) or result is None:
        return None
    return result


def _run_or_error(arguments: list[str], timeout: float) -> str | _Failure | None:
    try:
        completed = subprocess.run(
            ["netsh", *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if completed.returncode != 0:
        complaint = (completed.stderr or completed.stdout or "").strip()
        return _Failure(complaint or f"netsh exited with {completed.returncode}")
    return completed.stdout


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
        # Free when Windows allows it, None when it does not.
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
        # Running out is not an error and must not be reported as one: the
        # caller checks again, and on the run that produced the "handed out no
        # address" warning the address turned up seven seconds later and the
        # login went through.
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
