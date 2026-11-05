"""Everything the app needs to know, stored as JSON on disk.

The key names match the ``settings.json`` the Windows and macOS builds already
write, so an existing file loads unchanged and the two can run side by side
during a changeover. Keys an older file does not have fall back to the defaults
here, and keys this build does not recognise are carried through untouched.

It lives in ``$XDG_CONFIG_HOME/nhannhtdormwifihack/settings.json``, next to the
log, the status file and the run lock.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import default_route

APP_DIRECTORY_NAME = "nhannhtdormwifihack"

#: The key names this build writes for itself.
DECLARED_KEYS = frozenset(
    {
        "wifi_name",
        "gateway_ip",
        "interval",
        "check_interval_secs",
        "fallback_wifi_name",
        "portal_fallback_url",
        "portal_endpoints",
        "destination_url",
        "logout_url",
        "auto_connect",
        "auto_start",
        "check_wifi_every_time",
        "last_run_timestamp",
        "selected_version",
        "version_meta",
        "gemini_api_key",
        "ai_model",
    }
)


@dataclass
class PortalEndpoints:
    """The portal's API paths. Defaults match the portal this was built against."""

    verify_url: str = "/Home/VerifyUrl"
    get_customer: str = "/Content/GetCustomer"
    get_campaign_html: str = "/Content/GetCampaignHtml"
    analytic: str = "/Analytic/Send"

    _WIRE_NAMES = {
        "verify_url": "verifyURL",
        "get_customer": "getCustomer",
        "get_campaign_html": "getCampaignHTML",
        "analytic": "analytic",
    }

    @classmethod
    def from_json(cls, payload: object) -> PortalEndpoints:
        default = cls()
        if not isinstance(payload, dict):
            return default
        return cls(
            verify_url=_string(payload.get("verifyURL"), default.verify_url),
            get_customer=_string(payload.get("getCustomer"), default.get_customer),
            get_campaign_html=_string(payload.get("getCampaignHTML"), default.get_campaign_html),
            analytic=_string(payload.get("analytic"), default.analytic),
        )

    def to_json(self) -> dict[str, str]:
        return {
            "verifyURL": self.verify_url,
            "getCustomer": self.get_customer,
            "getCampaignHTML": self.get_campaign_html,
            "analytic": self.analytic,
        }


@dataclass
class Settings:
    """Everything the watcher reads, once per pass."""

    #: The SSID to look for, and to join when `auto_connect` is on.
    wifi_name: str = ""

    #: The router's address. Everything in the login sequence starts here.
    #:
    #: Empty, which is the default, means ask this machine which router it is
    #: currently using. Every network has a different one, so a fixed address
    #: stops working the moment you join a different hotspot.
    gateway_ip: str = ""

    #: How long a session lasts, in minutes. The router decides this; the value
    #: here has to match.
    interval: int = 15

    #: How often to check that the connection is still up, in seconds.
    check_interval_secs: int = 2

    #: A network to fall back to when the internet has been gone for a while.
    #:
    #: This is the safety net, and setting it is what switches the net on. When
    #: the connection has been dead for fifteen seconds and nothing this app is
    #: doing explains it, it joins this network instead. A phone's hotspot is the
    #: intended use.
    #:
    #: It has to be a network this machine has already saved in NetworkManager,
    #: or it cannot be rejoined from here. Empty means there is no net and a
    #: failed join leaves you offline.
    fallback_wifi_name: str = ""

    #: Where to send the login when the router's page carries no form action.
    portal_fallback_url: str = ""

    #: The portal's JSON endpoints, relative to whichever host is in play.
    portal_endpoints: PortalEndpoints = field(default_factory=PortalEndpoints)

    #: Where to ask the router to send us once it lets us through. Apple's
    #: connectivity check page, which is also what the Mac build uses.
    destination_url: str = "http://captive.apple.com/hotspot-detect.html"

    #: Hitting this logs the current session out. Optional.
    logout_url: str = ""

    #: Join `wifi_name` automatically when it is not the current network.
    auto_connect: bool = True

    #: Launch at login. Written by `install`, read by nobody else.
    auto_start: bool = False

    #: Check the network before every login rather than trusting the last check.
    check_wifi_every_time: bool = False

    #: When the last successful login happened, as a Unix timestamp.
    last_run_timestamp: float = 0.0

    #: The script the version picker currently has selected. Empty means use the
    #: built-in login path.
    selected_version: str = ""

    #: Display name and pinned state for each script, keyed by file name.
    version_meta: dict[str, dict] = field(default_factory=dict)

    gemini_api_key: str = ""
    ai_model: str = "gemini-2.5-flash"

    #: Keys found in the file that this build does not know about. The Windows
    #: build writes keys this one has never heard of, and re-encoding only the
    #: declared keys deleted them without a word.
    unknown_keys: dict[str, object] = field(default_factory=dict)

    # MARK: - Derived

    @property
    def session_timeout_seconds(self) -> int:
        """How long a session lasts, in seconds, clamped to one day.

        `interval` comes out of a file that can say anything.
        """
        return min(max(self.interval, 1), 1440) * 60

    # MARK: - The router in use right now

    def resolving_gateway(self) -> Settings:
        """A copy with `gateway_ip` filled in from the live default route.

        Call this once per pass rather than caching it. Switching networks
        changes the answer, and noticing that within one cycle is the whole
        point.
        """
        if self.gateway_ip:
            return self
        return replace(self, gateway_ip=default_route.gateway() or "")

    def resolving_gateway_waiting_up_to(self, seconds: int) -> Settings:
        """The same, but giving a network that is still coming up time to finish.

        Use this right after joining a network, or on a one-shot command that
        has no next pass to try again on.
        """
        if self.gateway_ip:
            return self
        return replace(self, gateway_ip=default_route.wait_for_gateway(seconds) or "")

    # MARK: - Storage

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """Load from disk, or hand back defaults when there is nothing there yet."""
        target = Path(path) if path else default_path()
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"{target} does not hold a JSON object")

        default = cls()
        endpoints = PortalEndpoints.from_json(payload.get("portal_endpoints"))

        return cls(
            wifi_name=_string(payload.get("wifi_name"), default.wifi_name),
            gateway_ip=_string(payload.get("gateway_ip"), default.gateway_ip),
            interval=_int(payload.get("interval"), default.interval),
            check_interval_secs=_int(
                payload.get("check_interval_secs"), default.check_interval_secs
            ),
            fallback_wifi_name=_string(
                payload.get("fallback_wifi_name"), default.fallback_wifi_name
            ),
            portal_fallback_url=_string(
                payload.get("portal_fallback_url"), default.portal_fallback_url
            ),
            portal_endpoints=endpoints,
            destination_url=_string(payload.get("destination_url"), default.destination_url),
            logout_url=_string(payload.get("logout_url"), default.logout_url),
            auto_connect=_bool(payload.get("auto_connect"), default.auto_connect),
            auto_start=_bool(payload.get("auto_start"), default.auto_start),
            check_wifi_every_time=_bool(
                payload.get("check_wifi_every_time"), default.check_wifi_every_time
            ),
            last_run_timestamp=_float(
                payload.get("last_run_timestamp"), default.last_run_timestamp
            ),
            selected_version=_string(payload.get("selected_version"), default.selected_version),
            version_meta=_dict(payload.get("version_meta"), default.version_meta),
            gemini_api_key=_string(payload.get("gemini_api_key"), default.gemini_api_key),
            ai_model=_string(payload.get("ai_model"), default.ai_model),
            unknown_keys={key: value for key, value in payload.items() if key not in DECLARED_KEYS},
        )

    def to_json(self) -> dict[str, object]:
        """The file this build writes, unknown keys included."""
        payload: dict[str, object] = {
            "wifi_name": self.wifi_name,
            "gateway_ip": self.gateway_ip,
            "interval": self.interval,
            "check_interval_secs": self.check_interval_secs,
            "fallback_wifi_name": self.fallback_wifi_name,
            "portal_fallback_url": self.portal_fallback_url,
            "portal_endpoints": self.portal_endpoints.to_json(),
            "destination_url": self.destination_url,
            "logout_url": self.logout_url,
            "auto_connect": self.auto_connect,
            "auto_start": self.auto_start,
            "check_wifi_every_time": self.check_wifi_every_time,
            "last_run_timestamp": self.last_run_timestamp,
            "selected_version": self.selected_version,
            "version_meta": self.version_meta,
            "gemini_api_key": self.gemini_api_key,
            "ai_model": self.ai_model,
        }
        # Then whatever else was in the file. A declared key always wins, so a
        # stale entry here can never shadow a real setting.
        for key, value in self.unknown_keys.items():
            if key not in DECLARED_KEYS:
                payload.setdefault(key, value)
        return payload

    def save(self, path: Path | None = None) -> Path:
        """Write the file, atomically and readable only by its owner."""
        target = Path(path) if path else default_path()
        target.parent.mkdir(parents=True, exist_ok=True)

        text = json.dumps(self.to_json(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

        # Written to one side and moved into place, so a reader never sees half
        # a file.
        handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".settings-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

        return target

    def touch_last_run(self) -> None:
        self.last_run_timestamp = time.time()


def support_directory() -> Path:
    """Where the settings, the log, the status file and the lock live."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / APP_DIRECTORY_NAME


def default_path() -> Path:
    return support_directory() / "settings.json"


def _string(value: object, fallback: str) -> str:
    return value if isinstance(value, str) else fallback


def _int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return fallback


def _float(value: object, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


def _bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _dict(value: object, fallback: dict) -> dict:
    return value if isinstance(value, dict) else dict(fallback)
