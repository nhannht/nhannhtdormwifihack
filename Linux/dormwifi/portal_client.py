"""The HTTP side of talking to the router and the portal.

One instance is one browsing session: it keeps its own cookie jar and forgets
everything when it goes away. Each login attempt builds a fresh one, so a stale
cookie from a failed attempt can never leak into the next.

Standard library only, on purpose. The app ships as source that runs on the
system Python, and the only outside thing it needs is PyGObject for the window,
which comes from apt.
"""

from __future__ import annotations

import errno
import http.client
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Callable, cast

from .portal_dns import GatewayResolver

# Sent on every request. The portal serves different content to clients it does
# not recognise as a desktop browser.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


class ClientError(Exception):
    """A request that could not be made at all."""


@dataclass
class PortalResponse:
    """What came back from one request."""

    status_code: int
    final_url: str
    data: bytes

    @property
    def body(self) -> str:
        # `replace` rather than `strict`, because the router's own pages are not
        # always valid UTF-8 and a decoding error here would hide the page that
        # explains the problem.
        return self.data.decode("utf-8", "replace")

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        return json.loads(self.data.decode("utf-8", "replace"))


class _AcceptEveryStatus(urllib.request.HTTPDefaultErrorHandler):
    """Hand back 4xx and 5xx instead of raising.

    The login sequence reads status codes: a 502 from the portal is reported as
    "the portal answered VerifyUrl with HTTP 502", which is actionable, while a
    raised exception would arrive as a transport failure.

    This replaces the *default error handler*, not the error processor. The
    processor is what hands a 3xx to the redirect handler, so replacing that
    instead — which this did — silently stopped following redirects, and a
    hotspot that answers `/status` with `302 /login` then reads as "the router
    did not answer" forever. That is exactly what this hotspot does.
    """

    def http_error_default(self, req, fp, code, msg, hdrs):
        # Typeshed says this returns an `HTTPError`; the caller (urllib) uses the
        # return value as the response, which is the whole point of overriding
        # it. The cast states the intent rather than papering over the override.
        return cast(urllib.error.HTTPError, fp)


class _GatewayTrust(urllib.request.HTTPSHandler):
    """Accept whatever certificate the gateway presents.

    A captive portal's router serves a self-signed certificate for an IP
    address, which no trust chain will ever validate. Refusing it would mean
    never being able to log in. This is limited to hosts on a private network,
    so a certificate problem out on the internet still fails the way it should.
    """

    def __init__(self, resolver: GatewayResolver | None = None) -> None:
        super().__init__()
        self._resolver = resolver
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._insecure = context

    def https_open(self, req):
        host = urllib.parse.urlsplit(req.full_url).hostname or ""
        if is_private_host(host):
            return self.do_open(
                _PinnedHTTPSConnection, req, context=self._insecure, resolver=self._resolver
            )
        return self.do_open(_PinnedHTTPSConnection, req, resolver=self._resolver)


class _PortalHTTPHandler(urllib.request.HTTPHandler):
    """Plain HTTP, through the connection class that honours the resolver."""

    def __init__(self, resolver: GatewayResolver | None = None) -> None:
        super().__init__()
        self._resolver = resolver

    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req, resolver=self._resolver)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """An HTTP connection opened to the address a resolver chose.

    ``self.host`` keeps the name the request was made for. Only the address the
    socket opens to changes, which is why the ``Host`` header still carries the
    name: nothing above the socket can tell that a resolver was involved.
    """

    def __init__(self, *args, resolver: GatewayResolver | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._resolver = resolver

    def connect(self):
        address = _pinned_address(self._resolver, self.host)
        if address is None:
            return super().connect()
        self.sock = _socket_to(self, address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """The same, for TLS: the certificate is checked against the name, not the IP."""

    def __init__(
        self,
        *args,
        resolver: GatewayResolver | None = None,
        context: ssl.SSLContext | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, context=context, **kwargs)
        self._resolver = resolver
        # The constructor has already turned a `None` context into the
        # interpreter's HTTPS default, which is private and not in typeshed.
        self._tls: ssl.SSLContext = getattr(self, "_context")

    def connect(self):
        address = _pinned_address(self._resolver, self.host)
        if address is None:
            return super().connect()
        self.sock = _socket_to(self, address)
        self.sock = self._tls.wrap_socket(self.sock, server_hostname=self.host)


def _pinned_address(resolver: GatewayResolver | None, host: str) -> str | None:
    """The address to open for `host`, or None to let the system resolver decide.

    A proxy is not special-cased. When one is configured the connection opens to
    the proxy's name instead, which is resolved the same way and lands on the
    same address the system resolver would have given.
    """
    if resolver is None:
        return None
    return resolver.address_for(host)


def _socket_to(
    connection: http.client.HTTPConnection, address: str
) -> socket.socket:
    """The TCP half of ``http.client``'s ``connect``, to one given address.

    The other half is choosing the address, which is the part being replaced.
    What is left is what the standard library does: connect, then stop Nagle from
    holding a small request back.
    """
    sock = socket.create_connection(
        (address, connection.port), connection.timeout
    )
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError as error:
        if error.errno != errno.ENOPROTOOPT:
            raise
    return sock


class PortalClient:
    """One browsing session's worth of HTTP."""

    def __init__(
        self, timeout: float = 20.0, resolver: GatewayResolver | None = None
    ) -> None:
        self._timeout = timeout
        self._cookies = CookieJar()
        self._resolver = resolver
        self._opener = self._build_opener()
        self._headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        }

    def _build_opener(self) -> urllib.request.OpenerDirector:
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies),
            _GatewayTrust(self._resolver),
            _PortalHTTPHandler(self._resolver),
            _AcceptEveryStatus,
        )

    def prefer_gateway_dns(
        self, server_ip: str, on_answer: Callable[[str, str], None] | None = None
    ) -> None:
        """Let the router answer for names before the system resolver does.

        The portal is the one address in the sign-in that is a name, and step 2
        runs at the one moment the machine is captive — when the router is the
        only resolver inside the walled garden. See :mod:`dormwifi.portal_dns`
        for what goes wrong without it.

        Doing nothing is the safe case: an empty address, a router with no
        opinion, or an open link where it forwards upstream all behave exactly as
        they did before. The connection classes take their resolver when the
        opener is built, so this rebuilds it and keeps the cookie jar.
        """
        if self._resolver is not None or not server_ip:
            return
        self._resolver = GatewayResolver(server_ip, on_answer=on_answer)
        self._opener = self._build_opener()

    # MARK: - Requests

    def get(
        self, url: str, headers: dict[str, str] | None = None, timeout: float | None = None
    ) -> PortalResponse:
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        request.add_header("Upgrade-Insecure-Requests", "1")
        return self._send(request, headers, timeout)

    def post_json(
        self,
        url: str,
        body: object,
        referer: str | None,
        origin: str | None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> PortalResponse:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("Content-Type", "application/json; charset=UTF-8")
        request.add_header("Accept", "*/*")
        request.add_header("X-Requested-With", "XMLHttpRequest")
        if referer:
            request.add_header("Referer", referer)
        if origin:
            request.add_header("Origin", origin)
        return self._send(request, headers, timeout)

    def post_form(
        self,
        url: str,
        fields: list[tuple[str, str]],
        referer: str | None,
        origin: str | None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> PortalResponse:
        payload = form_encoded(fields).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Upgrade-Insecure-Requests", "1")
        if referer:
            request.add_header("Referer", referer)
        if origin:
            request.add_header("Origin", origin)
        return self._send(request, headers, timeout)

    def _send(
        self,
        request: urllib.request.Request,
        headers: dict[str, str] | None,
        timeout: float | None,
    ) -> PortalResponse:
        for field, value in self._headers.items():
            request.add_header(field, value)
        for field, value in (headers or {}).items():
            request.add_header(field, value)

        try:
            with self._opener.open(request, timeout=timeout or self._timeout) as response:
                return PortalResponse(
                    status_code=response.status,
                    final_url=response.geturl(),
                    data=response.read(),
                )
        except urllib.error.HTTPError as error:
            # Only reachable for a status the redirect handler itself turned
            # into an error, such as a redirect loop.
            return PortalResponse(
                status_code=error.code,
                final_url=error.geturl() or request.full_url,
                data=error.read() or b"",
            )
        except ValueError as error:
            raise ClientError(f"Not a usable URL: {request.full_url}") from error


def form_encoded(fields: list[tuple[str, str]]) -> str:
    """Form bodies use `+` for a space, which plain quoting will not do."""

    def escape(text: str) -> str:
        return urllib.parse.quote(text, safe="-._* ").replace(" ", "+")

    return "&".join(f"{escape(name)}={escape(value)}" for name, value in fields)


def is_private_host(host: str) -> bool:
    """Private address ranges, plus loopback. Anything else is a public host.

    Every label has to be a plain decimal octet, and there have to be exactly
    four of them. An earlier version dropped the labels that were not numbers
    before counting, so ``10.0.0.1.evil.com`` counted as four octets, matched the
    10.x range, and had any certificate it presented accepted. The portal host is
    read from the router's login page, which an attacker on the captive network
    controls, so that name is reachable.
    """
    if host in ("localhost", "::1", "[::1]"):
        return True

    labels = host.split(".")
    if len(labels) != 4:
        return False

    octets: list[int] = []
    for label in labels:
        # Digits only, so "0x0a", "+1" and "١٠" are all rejected rather than
        # being coerced into an octet.
        if not label or len(label) > 3 or not all("0" <= character <= "9" for character in label):
            return False
        value = int(label)
        if value > 255:
            return False
        octets.append(value)

    first, second = octets[0], octets[1]
    if first == 10 or first == 127:
        return True
    if (first, second) == (192, 168):
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    if (first, second) == (169, 254):
        return True
    return False
