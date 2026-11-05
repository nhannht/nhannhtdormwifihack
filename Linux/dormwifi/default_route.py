"""The router this machine sends its traffic through.

The captive portal always sits on it, and its address is different on every
network. Asking the system each time is what lets one settings file work on all
of them without being edited in between, which a fixed address cannot do.

On Linux the answer is in ``/proc/net/route``, which is the kernel's own IPv4
routing table and needs no process launch. When that table has no default route
there is nothing to read, and the fallback works the address out from the one
the network handed this machine.

Whatever comes back is only ever a candidate, and nothing here trusts it. The
next thing that happens to this address is that the session probe asks it for
``/status``, and an address that does not answer is reported as such. So a wrong
guess shows up immediately as "nothing answered at 10.0.0.1" rather than as a
mysterious login failure, and ``gateway_ip`` in the settings file overrides the
whole question.
"""

from __future__ import annotations

import fcntl
import socket
import struct
import time

_PROCFS_ROUTE = "/proc/net/route"
_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B
_RTF_UP = 0x0001
_RTF_GATEWAY = 0x0002


def gateway() -> str | None:
    """The gateway of the default route, or None when this machine has no network."""
    return _procfs_gateway() or first_host_of_local_subnet()


def wait_for_gateway(up_to: int = 20) -> str | None:
    """Wait for a default route to exist, for up to `up_to` seconds.

    Joining a network returns as soon as the radio associates, which is several
    seconds before DHCP hands out an address and installs a route. Asking for
    the router inside that gap gets nothing, and reporting "no network at all"
    then is wrong: the network is a few seconds away from being ready.
    """
    for _ in range(max(up_to, 1)):
        found = gateway()
        if found:
            return found
        time.sleep(1)
    return gateway()


def _procfs_gateway() -> str | None:
    """Walk the IPv4 routing table and pick the gateway of the default route.

    The table is plain text: a header, then one line per route. Addresses are
    little-endian hex, so ``0101A8C0`` is 192.168.1.1.
    """
    try:
        with open(_PROCFS_ROUTE, "r", encoding="ascii", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None

    best: tuple[int, str] | None = None
    for line in lines[1:]:
        columns = line.split()
        if len(columns) < 8:
            continue
        # Interface Destination Gateway Flags RefCnt Use Metric Mask ...
        try:
            destination = int(columns[1], 16)
            gateway_raw = int(columns[2], 16)
            flags = int(columns[3], 16)
            metric = int(columns[6], 16)
        except ValueError:
            continue

        # Destination 0.0.0.0 with the gateway flag set is what makes this the
        # default route rather than a route to one particular network.
        if destination != 0 or not flags & _RTF_GATEWAY or not flags & _RTF_UP:
            continue
        text = _text(gateway_raw)
        if text is None or text == "0.0.0.0":
            continue
        if best is None or metric < best[0]:
            best = (metric, text)

    return best[1] if best else None


def first_host_of_local_subnet() -> str | None:
    """The first usable address of the network this machine is on.

    A convention rather than a rule, so on a network that puts its router
    somewhere else this returns an address that will not answer. The app says
    exactly that when it happens, and ``gateway_ip`` is there to name the real
    one.
    """
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return None

    for name in names:
        if name == "lo" or name.startswith(("tun", "tap", "wg", "docker", "veth", "br-")):
            continue
        address = _interface_address(name)
        mask = _interface_netmask(name)
        if address is None or mask is None:
            continue
        found = first_host(address, mask)
        if found:
            return found

    return None


def first_host(address: int, mask: int) -> str | None:
    """Network address plus one, from two addresses in network byte order.

    The ioctl calls hand back the bytes of a `sockaddr_in`, so an address read
    off this machine is 0x0101A8C0 for 192.168.1.1. Both arguments are put into
    host order before anything is done to them, so the arithmetic is done on the
    number a person would write down.

    A mask of /31 or narrower has no room for a router and a host, and a mask of
    /0 describes no network to be the first host of.
    """
    host_address = socket.ntohl(address)
    host_mask = socket.ntohl(mask)

    if host_mask in (0x00000000, 0xFFFFFFFF, 0xFFFFFFFE):
        return None

    network = host_address & host_mask
    return _text(socket.htonl(network + 1))


def _interface_address(name: str) -> int | None:
    return _ioctl_int(name, _SIOCGIFADDR)


def _interface_netmask(name: str) -> int | None:
    return _ioctl_int(name, _SIOCGIFNETMASK)


def _ioctl_int(name: str, request: int) -> int | None:
    """Read one IPv4 field out of an interface, as a host-order integer."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            packed = fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name[:15].encode()))
        except OSError:
            return None
    # The reply is a `struct ifreq`: 16 bytes of name, then `struct sockaddr_in`,
    # whose address sits at offset 20.
    return struct.unpack("I", packed[20:24])[0]


def _text(address: int) -> str | None:
    """`192.168.200.1` from an integer in network byte order."""
    try:
        return socket.inet_ntoa(struct.pack("I", address))
    except OSError:
        return None
