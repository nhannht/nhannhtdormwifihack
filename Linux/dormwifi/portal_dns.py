"""Asks the router what a name resolves to.

The sign-in talks to two addresses, and only one of them is a name. The router is
reached by IP, so steps 1 and 3 need no resolver at all. The portal is named:
``portal_fallback_url`` is ``http://v1.awingconnect.vn``, and every call in step 2
is built on that host. So the one lookup the sign-in cannot skip is the portal's.

That lookup normally goes to whatever resolver the machine is configured with.
On a laptop that enforces DNS through Tailscale, that resolver is a tailnet
AdGuard node, and the machine stops asking the router anything. While a session
is open this works. The moment the session lapses the machine is captive, the
WireGuard path to that resolver is outside the walled garden, and every query
stalls until it times out — NetworkManager's own connectivity check included,
which is why it reports "no internet" rather than "captive portal" and no popup
appears. Step 2 then has no address to POST to while steps 1 and 3, which need no
name, keep answering, so the failure looks like the portal's fault.

The router is inside the garden, so its DNS answers there. Asking it what its own
portal's name means is asking the device that built the garden, which is the only
source that is right in that state. The answer is used by connecting to the
address it gave while keeping the name in the request: the ``Host`` header, the
SNI name and certificate verification all stay the name, so nothing downstream
can tell the difference.

On an open link the router forwards upstream and returns the same public address
the system resolver would, so a healthy connection behaves exactly as before.
No dnspython here: the system Python has no pip, and this is one query type.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
from typing import Callable

#: Only A records. The portal is reached over plain HTTP and has no IPv6 path.
_TYPE_A = 1
_CLASS_IN = 1
_HEADER = struct.Struct("!HHHHHH")
#: type, class, ttl, rdlength — everything between the answer's name and its data.
_ANSWER_FIXED = struct.Struct("!HHIH")
#: `rcode` in the low four bits of the second header word.
_RCODE_MASK = 0x000F
#: Recursion desired. The client never asks a second question to finish this one.
_RD_FLAG = 0x0100


class Unreachable(Exception):
    """The server did not answer at all, as opposed to answering "no such name"."""


def query_a(
    server: str, name: str, timeout: float = 1.5, port: int = 53
) -> list[str]:
    """Every A record for `name`, asked of exactly one server.

    An empty list means the server answered and the name has no address. A
    timeout or an unroutable server raises :class:`Unreachable`, because the two
    call for different behaviour: one is about this name, the other about every
    name.
    """
    transaction = int.from_bytes(os.urandom(2), "big")
    try:
        packet = build_query(name, transaction)
    except (ValueError, UnicodeError):
        return []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (server, port))
            data, _ = sock.recvfrom(4096)
    except OSError as error:
        raise Unreachable(str(error)) from error

    return answers(data, transaction)


def build_query(name: str, transaction: int) -> bytes:
    qname = wire_name(name)
    return (
        _HEADER.pack(transaction, _RD_FLAG, 1, 0, 0, 0)
        + qname
        + struct.pack("!HH", _TYPE_A, _CLASS_IN)
    )


def wire_name(name: str) -> bytes:
    """`v1.awingconnect.vn` as length-prefixed labels, ending in a zero byte."""
    text = name.rstrip(".")
    labels = text.split(".")
    if not text or any(not label for label in labels):
        raise ValueError(f"Not a usable hostname: {name!r}")

    encoded: list[bytes] = []
    for label in labels:
        raw = label.encode("idna")
        if len(raw) > 63:
            raise ValueError(f"Hostname label too long: {label!r}")
        encoded.append(bytes([len(raw)]) + raw)

    wire = b"".join(encoded) + b"\x00"
    if len(wire) > 255:
        raise ValueError(f"Hostname too long: {name!r}")
    return wire


def answers(data: bytes, transaction: int) -> list[str]:
    """The A records in one reply. Pure, so a test can hand it bytes.

    Anything malformed, truncated, addressed to another transaction or carrying
    an error code comes back as an empty list rather than as an exception. This
    parses a reply from a device on a network anyone can join, so every offset is
    bounds-checked and the reader stops at the first byte it cannot follow.
    """
    if len(data) < _HEADER.size:
        return []

    got_id, flags, questions, records, _, _ = _HEADER.unpack_from(data, 0)
    if got_id != transaction or flags & _RCODE_MASK:
        return []

    offset = _HEADER.size
    for _ in range(questions):
        offset = _after_name(data, offset)
        if offset + 4 > len(data):
            return []
        offset += 4

    found: list[str] = []
    for _ in range(records):
        offset = _after_name(data, offset)
        if offset + _ANSWER_FIXED.size > len(data):
            return found
        record_type, record_class, _ttl, length = _ANSWER_FIXED.unpack_from(data, offset)
        offset += _ANSWER_FIXED.size
        if offset + length > len(data):
            return found
        # A CNAME chain puts its A records in the answer section too, owned by
        # the target name, so collecting every A here follows the chain without
        # having to read the aliases.
        if record_type == _TYPE_A and record_class == _CLASS_IN and length == 4:
            found.append(str(ipaddress.IPv4Address(data[offset : offset + 4])))
        offset += length

    return found


def _after_name(data: bytes, offset: int) -> int:
    """The offset just past a (possibly compressed) domain name."""
    while 0 <= offset < len(data):
        length = data[offset]
        if length & 0xC0 == 0xC0:
            return offset + 2
        if length == 0:
            return offset + 1
        offset += 1 + length
    return offset


class GatewayResolver:
    """Resolves names through the router first, and the system second.

    The router is asked once per host and the answer is kept for the life of the
    client, which is one login attempt. A host the router has nothing to say
    about is left to the system resolver by returning None, so the ordinary path
    is untouched. One timeout is taken as the router's DNS being unreachable
    from here, and nothing more is asked of it: a second host is not going to
    fare better, and each attempt would cost the caller its timeout.
    """

    def __init__(
        self,
        server_ip: str,
        timeout: float = 1.5,
        query: Callable[..., list[str]] = query_a,
        on_answer: Callable[[str, str], None] | None = None,
    ) -> None:
        self._server = server_ip
        self._timeout = timeout
        self._query = query
        self._on_answer = on_answer
        #: Host names already settled, including the ones settled as "nothing".
        self._settled: dict[str, str | None] = {}
        #: What the router answered, for the log and for tests.
        self.answered: dict[str, str] = {}
        self._gave_up = False

    @property
    def server(self) -> str:
        return self._server

    def address_for(self, host: str) -> str | None:
        """Where to connect for `host`, or None to let the system resolver decide."""
        name = host.strip().lower()
        if not name or _is_address(name):
            return None
        if name in self._settled:
            return self._settled[name]

        address = None if self._gave_up else self._ask(name)
        self._settled[name] = address

        if address is not None:
            self.answered[name] = address
            if self._on_answer is not None:
                self._on_answer(name, address)
        return address

    def _ask(self, name: str) -> str | None:
        try:
            candidates = self._query(self._server, name, timeout=self._timeout)
        except (Unreachable, OSError):
            # The router's DNS is not answering this machine, so it will not
            # answer the next name either. Stop asking and let the system
            # resolver have every host from here on.
            self._gave_up = True
            return None

        for candidate in candidates:
            if _usable(candidate):
                return candidate
        return None


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _usable(address: str) -> bool:
    """Skip answers no client can connect to.

    A filtering resolver answers a blocked name with ``0.0.0.0`` or a loopback
    address, and connecting there means connecting to this machine. Those are
    the resolver saying "no", so they are treated as no answer at all.
    """
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (found.is_unspecified or found.is_loopback or found.is_multicast)
