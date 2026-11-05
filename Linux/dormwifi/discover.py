"""Reading the router's login page for the one thing it may not say outright.

Some MikroTik templates put the portal's address in the login form's ``action``,
and then the sign-in needs no configuration at all. Others put it nowhere: the
form has no action, and the page expects a browser to be carried there by a
script or by the user clicking something. On those the three step login has no
address to go to, and the settings file has to be told one by hand.

This module reads a page the app already fetched and says what it holds: whether
it is a hotspot login page, how big the challenge is, where the form posts, and
what URLs appear anywhere in it — with the gateway's own URLs separated from
anything on another host, because only another host can be the portal.

Nothing here makes a decision the app cannot justify: it prints candidates and the
command that sets one. It never sets it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import chap
from .html_scraper import form_action, inputs_in, value_identified_by

_URL = re.compile(r"https?://[^\s\"'<>()\\]+", re.IGNORECASE)

#: Hosts that answer "is there internet?" and are never the portal. The router
#: puts one of these in the login page as the place it will send you afterwards.
_PROBE_HOSTS = (
    "msftconnecttest.com",
    "msftncsi.com",
    "captive.apple.com",
    "connectivitycheck.gstatic.com",
    "connectivitycheck.android.com",
    "gstatic.com",
    "detectportal.firefox.com",
    "connectivity-check.ubuntu.com",
    "generate_204",
    "nmcheck.gnome.org",
)

#: Extensions that are never a portal, and are noise in a list of candidates.
_ASSET_SUFFIXES = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
)


@dataclass
class Finding:
    """What one hotspot login page turned out to hold."""

    looks_like_login_page: bool = False
    chap_id: bool = False
    challenge_bytes: int | None = None
    form_action: str | None = None
    #: Every URL on the page, as `(url, where it was found)`.
    urls: list[tuple[str, str]] = field(default_factory=list)
    #: URLs on a host other than the gateway, reduced to `scheme://host`.
    candidates: list[str] = field(default_factory=list)

    @property
    def unreachable_challenge(self) -> str | None:
        """Why the challenge could not be read, if it could not."""
        if not self.chap_id:
            return "the page carries no chap-id"
        if self.challenge_bytes is None:
            return "the chap-challenge is malformed"
        return None


def inspect(page: str, gateway_ip: str) -> Finding:
    """Read one login page. Pure: no network, no settings."""
    finding = Finding()
    finding.looks_like_login_page = "chap-id" in page or "username" in page.lower()

    chap_id = value_identified_by("chap-id", page)
    challenge = value_identified_by("chap-challenge", page)
    finding.chap_id = chap_id is not None
    if challenge is not None:
        try:
            finding.challenge_bytes = len(chap.decode(challenge))
        except chap.ParseError:
            finding.challenge_bytes = None

    finding.form_action = form_action(page)

    seen: set[str] = set()
    for candidate in inputs_in(page):
        if not candidate.value:
            continue
        for match in _URL.findall(candidate.value):
            url = _tidy(match)
            if url in seen:
                continue
            seen.add(url)
            finding.urls.append((url, f"input {candidate.key or '(unnamed)'}"))

    for match in _URL.findall(page):
        url = _tidy(match)
        if url in seen:
            continue
        seen.add(url)
        finding.urls.append((url, "page"))

    finding.candidates = _ranked_candidates(finding.urls, gateway_ip)
    return finding


def _ranked_candidates(urls, gateway_ip: str) -> list[str]:
    """Which URLs on the page could be the portal, best first.

    Two things make a URL unlikely, and both come from what these templates
    actually do. The router always names itself — the login form it posts to, the
    script it loads — so its own host is not the answer. And the value of a
    hidden field is usually where the router will *send you afterwards*, one of
    the browsers' connectivity checks; the portal is what the page's own script
    or link points at. Hence: URLs found in the markup before URLs found in
    field values.
    """
    from_markup: list[str] = []
    from_fields: list[str] = []

    for url, where in urls:
        if _is_asset(url) or _host(url) == gateway_ip or _is_probe_host(url):
            continue
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        bucket = from_fields if where.startswith("input") else from_markup
        if root not in bucket:
            bucket.append(root)

    return from_markup + [root for root in from_fields if root not in from_markup]


def _is_probe_host(url: str) -> bool:
    host = _host(url).lower()
    path = urlsplit(url).path.lower()
    return any(host == probe or host.endswith("." + probe) for probe in _PROBE_HOSTS) or any(
        probe in path for probe in _PROBE_HOSTS
    )


def render(finding: Finding, gateway_ip: str) -> list[str]:
    """The report a person reads, line by line."""
    lines = [f"Router:     {gateway_ip or '(none)'}"]

    if not finding.looks_like_login_page:
        lines.append("Login page: no — this does not look like a hotspot login page.")
        lines.append(
            "            Either this machine is on the wrong network, or this network has no "
            "sign-in page at all."
        )
        return lines

    lines.append("Login page: yes")
    lines.append(
        f"Challenge:  {'present, ' + str(finding.challenge_bytes) + ' bytes' if finding.chap_id else 'missing'}"
        + ("" if finding.unreachable_challenge is None else f" ({finding.unreachable_challenge})")
    )
    lines.append(
        "Form posts: "
        + (finding.form_action or "nowhere — the page names no portal, so one has to be set")
    )

    same_host = [(url, where) for url, where in finding.urls if _host(url) == gateway_ip]
    if same_host:
        lines.append("On this router:")
        for url, where in same_host[:8]:
            lines.append(f"  {url}   ({where})")

    if finding.candidates:
        lines.append("On another host (the portal is one of these):")
        for candidate in finding.candidates:
            lines.append(f"  {candidate}")
        lines.append("")
        lines.append("Set it with:")
        lines.append(f'  dormwifi config set portal_fallback_url "{finding.candidates[0]}"')
    else:
        lines.append("")
        lines.append("No portal address appears anywhere on the page.")
        lines.append(
            "Open the login page in a browser and copy the address it is served from, then:"
        )
        lines.append('  dormwifi config set portal_fallback_url "http://that.address"')

    return lines


def _tidy(url: str) -> str:
    return url.rstrip(".,;:)]}\\")


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def _is_asset(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(_ASSET_SUFFIXES)
