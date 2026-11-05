"""Asks the router whether we are signed in, and for how much longer.

This has to run before anything else. On this hotspot ``/login`` does not answer
at all while a session is open: it accepts the connection and then hangs until
the client gives up. Only ``/status`` is safe to ask first, and it replies in
about 15ms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from . import error_text
from .portal_client import PortalClient

#: The largest value the Swift original can hold in an `Int`. Anything bigger is
#: treated as unreadable rather than as a duration, so a page that says
#: "999999999999999999999s" cannot make the watcher sleep for a century.
_MAX_DURATION = 2**63 - 1

# Both the Vietnamese page this was built against and the stock MikroTik English
# one, so a firmware or language change does not blind it.
_IP_LABELS = ("địa chỉ ip", "ip address", "ip")
_MAC_LABELS = ("địa chỉ mac", "mac address", "mac")
_CONNECTED_LABELS = ("thời gian kết nối", "connected", "uptime")
_REMAINING_LABELS = ("thời gian còn lại", "time left", "session time left")

_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SessionInfo:
    """What the router says about the current session."""

    ip_address: str | None = None
    mac_address: str | None = None
    #: How long this session has been up, in seconds.
    connected_for: int | None = None
    #: How long it has left, in seconds. The router publishes this, so there is
    #: no need to guess from a local timer.
    remaining: int | None = None


@dataclass(frozen=True)
class Status:
    """What one look at ``/status`` found."""

    kind: Literal["active", "expired", "unknown", "unreachable"]
    info: SessionInfo | None = None
    why: str = ""

    @property
    def is_active(self) -> bool:
        return self.kind == "active"

    @property
    def is_expired(self) -> bool:
        return self.kind == "expired"


def status(
    gateway_ip: str, client: PortalClient, timeout: float = 5.0
) -> Status:
    if not gateway_ip:
        return Status("unreachable", why="Cannot build a status URL for an empty address")

    try:
        response = client.get(f"http://{gateway_ip}/status", timeout=timeout)
    except Exception as error:  # noqa: BLE001 - every failure is "not there"
        # A transport failure is the router not being there, which is a
        # different problem from a page that carries no session.
        return Status("unreachable", why=error_text.short(error))

    if not response.is_success:
        return Status("unreachable", why=f"Status page answered HTTP {response.status_code}")
    return interpret(response.body, response.final_url)


def interpret(body: str, final_url: str) -> Status:
    """Read one status page."""
    # Being sent to the login page settles it immediately.
    if "login" in urlsplit(final_url).path.lower():
        return Status("expired")

    # Script and style bodies go before anything is read out of the page, so a
    # status page whose JavaScript happens to name the challenge is not read as
    # a login page. Getting that wrong reported an open session as signed out on
    # every pass, which sent the watcher into a login loop.
    visible = without_scripts_and_styles(body)

    lower = visible.lower()
    if "chap-challenge" in lower or "chap-id" in lower:
        return Status("expired")

    fields = text_fields(visible)
    info = SessionInfo(
        ip_address=_value_for(_IP_LABELS, fields),
        mac_address=_value_for(_MAC_LABELS, fields),
        connected_for=duration(_value_for(_CONNECTED_LABELS, fields)),
        remaining=duration(_value_for(_REMAINING_LABELS, fields)),
    )

    # A session page names at least one of these. A login page names none.
    if info.remaining is not None or info.connected_for is not None or info.mac_address is not None:
        return Status("active", info=info)

    if "uptime" in lower or "bytes in" in lower:
        return Status("active", info=info)
    if "<form" in lower and "password" in lower:
        return Status("expired")

    return Status("unknown", why="Status page did not identify itself")


def without_scripts_and_styles(html: str) -> str:
    """Everything between a `script` or `style` tag and its close, removed."""
    return _SCRIPT_OR_STYLE.sub(" ", html)


def text_fields(html: str) -> list[str]:
    """The page is a two column table, so the value is the cell after the label."""
    text = _ANY_TAG.sub("\x01", without_scripts_and_styles(html))
    return [field.strip() for field in text.split("\x01") if field.strip()]


def duration(text: str | None) -> int | None:
    """`12m25s`, `1h2m3s`, `45s`, `1d2h3m4s` into seconds.

    Every step is bounds-checked and a number too large to hold is rejected.
    This text arrives from the router over plain HTTP, so a run of twenty digits
    is something the page can genuinely say, and an unchecked answer would make
    the watcher sleep forever.
    """
    if not text:
        return None

    total = 0
    number = 0
    saw_any_unit = False

    for character in text.lower():
        if character.isdigit():
            digit = int(character)
            number = number * 10 + digit
            if number > _MAX_DURATION:
                return None
        elif character in _UNITS:
            scaled = number * _UNITS[character]
            total += scaled
            if total > _MAX_DURATION:
                return None
            number = 0
            saw_any_unit = True
        elif not character.isspace():
            return None

    return total if saw_any_unit else None


def _value_for(labels: tuple[str, ...], fields: list[str]) -> str | None:
    for index, field_text in enumerate(fields):
        normalized = field_text.lower().strip(": \t")
        if normalized in labels and index + 1 < len(fields):
            return fields[index + 1]
    return None
