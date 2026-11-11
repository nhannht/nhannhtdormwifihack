"""The router this machine sends its traffic through.

The captive portal always sits on it, and its address is different on every
network. Asking the system each time is what lets one settings file work on all
of them without being edited in between, which a fixed address cannot do.

On Windows the answer comes from ``GetAdaptersAddresses``, the same table the
network status page reads, through ``ctypes`` so there is nothing to install.
When no adapter reports a gateway, the fallback works the address out from the
one the network handed this machine.

Whatever comes back is only ever a candidate, and nothing here trusts it. The
next thing that happens to this address is that the session probe asks it for
``/status``, and an address that does not answer is reported as such. So a wrong
guess shows up immediately as "nothing answered at 10.0.0.1" rather than as a
mysterious login failure, and ``gateway_ip`` in the settings file overrides the
whole question.
"""

from __future__ import annotations

import ctypes
import ipaddress
import os
import socket
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass, field

#: ``IfOperStatusUp``. Not ``IF_OPER_STATUS_OPERATIONAL`` (5), which is an NDIS
#: internal value and would make every adapter look down.
_IF_OPER_STATUS_UP = 1
#: ``IF_TYPE_SOFTWARE_LOOPBACK``.
_IF_TYPE_LOOPBACK = 24


@dataclass
class _Adapter:
    metric: int
    gateways: list[str] = field(default_factory=list)
    unicasts: list[tuple[str, int]] = field(default_factory=list)


if os.name == "nt":
    class _SOCKADDR(ctypes.Structure):
        _fields_ = [
            ("sa_family", ctypes.c_ushort),
            ("sa_data", ctypes.c_char * 14),
        ]

    class _SOCKADDR_IN(ctypes.Structure):
        _fields_ = [
            ("sin_family", ctypes.c_ushort),
            ("sin_port", ctypes.c_ushort),
            ("sin_addr", ctypes.c_ubyte * 4),
            ("sin_zero", ctypes.c_char * 8),
        ]

    class _SOCKET_ADDRESS(ctypes.Structure):
        _fields_ = [
            ("lpSockaddr", ctypes.POINTER(_SOCKADDR)),
            ("iSockaddrLength", ctypes.c_int),
        ]

    class _IP_ADAPTER_UNICAST_ADDRESS_LH(ctypes.Structure):
        pass

    _IP_ADAPTER_UNICAST_ADDRESS_LH._fields_ = [
        # The real struct starts with a union of Alignment and Length/IfIndex,
        # both 8 bytes, so a 64-bit integer keeps every later offset right.
        ("Alignment", ctypes.c_ulonglong),
        ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS_LH)),
        ("Address", _SOCKET_ADDRESS),
        ("PrefixOrigin", ctypes.c_int),
        ("SuffixOrigin", ctypes.c_int),
        ("DadState", ctypes.c_int),
        ("ValidLifetime", wintypes.ULONG),
        ("PreferredLifetime", wintypes.ULONG),
        ("LeaseLifetime", wintypes.ULONG),
        ("OnLinkPrefixLength", ctypes.c_ubyte),
    ]

    class _IP_ADAPTER_GATEWAY_ADDRESS_LH(ctypes.Structure):
        pass

    _IP_ADAPTER_GATEWAY_ADDRESS_LH._fields_ = [
        ("Alignment", ctypes.c_ulonglong),
        ("Next", ctypes.POINTER(_IP_ADAPTER_GATEWAY_ADDRESS_LH)),
        ("Address", _SOCKET_ADDRESS),
    ]

    class _IP_ADAPTER_ADDRESSES_LH(ctypes.Structure):
        pass

    _IP_ADAPTER_ADDRESSES_LH._fields_ = [
        ("Alignment", ctypes.c_ulonglong),
        ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES_LH)),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS_LH)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_wchar_p),
        ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.ULONG),
        ("Flags", wintypes.ULONG),
        ("Mtu", wintypes.ULONG),
        ("IfType", wintypes.ULONG),
        ("OperStatus", ctypes.c_int),
        ("Ipv6IfIndex", wintypes.ULONG),
        ("ZoneIndices", wintypes.ULONG * 16),
        ("FirstPrefix", ctypes.c_void_p),
        ("TransmitLinkSpeed", ctypes.c_ulonglong),
        ("ReceiveLinkSpeed", ctypes.c_ulonglong),
        ("FirstWinsServerAddress", ctypes.c_void_p),
        ("FirstGatewayAddress", ctypes.POINTER(_IP_ADAPTER_GATEWAY_ADDRESS_LH)),
        ("Ipv4Metric", wintypes.ULONG),
        ("Ipv6Metric", wintypes.ULONG),
        ("Luid", ctypes.c_ulonglong),
        ("Dhcpv4Server", _SOCKET_ADDRESS),
        ("CompartmentId", wintypes.ULONG),
        ("NetworkGuid", ctypes.c_ubyte * 16),
        ("ConnectionType", ctypes.c_int),
        ("TunnelType", ctypes.c_int),
        ("Dhcpv6Server", _SOCKET_ADDRESS),
        ("Dhcpv6ClientDuid", ctypes.c_ubyte * 130),
        ("Dhcpv6ClientDuidLength", wintypes.ULONG),
        ("Dhcpv6Iaid", wintypes.ULONG),
        ("FirstDnsSuffix", ctypes.c_void_p),
    ]

    _AF_INET = 2
    _GAA_FLAG_INCLUDE_GATEWAYS = 0x0080
    _ERROR_BUFFER_OVERFLOW = 111
    _NO_ERROR = 0


def gateway() -> str | None:
    """The gateway of the default route, or None when this machine has no network."""
    return _system_gateway() or first_host_of_local_subnet()


def wait_for_gateway(up_to: int = 20) -> str | None:
    """Wait for a default route to exist, for up to ``up_to`` seconds.

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


def _system_gateway() -> str | None:
    """The first gateway Windows reports, preferring the lowest-metric adapter."""
    best: tuple[int, str] | None = None
    for adapter in _adapters():
        for address in adapter.gateways:
            if best is None or adapter.metric < best[0]:
                best = (adapter.metric, address)
    return best[1] if best else None


def first_host_of_local_subnet() -> str | None:
    """The first usable address of the network this machine is on.

    A convention rather than a rule, so on a network that puts its router
    somewhere else this returns an address that will not answer. The app says
    exactly that when it happens, and ``gateway_ip`` is there to name the real
    one.
    """
    best: tuple[int, str] | None = None
    for adapter in _adapters():
        for address, prefix in adapter.unicasts:
            found = _first_host_from_prefix(address, prefix)
            if found and (best is None or adapter.metric < best[0]):
                best = (adapter.metric, found)
    return best[1] if best else None


def _first_host_from_prefix(address: str, prefix: int) -> str | None:
    if prefix <= 0 or prefix >= 31:
        return None
    host_mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    try:
        packed = struct.unpack("I", socket.inet_aton(address))[0]
    except OSError:
        return None
    return first_host(packed, socket.htonl(host_mask))


def first_host(address: int, mask: int) -> str | None:
    """Network address plus one, from two addresses in network byte order.

    Both arguments are put into host order before anything is done to them, so
    the arithmetic is done on the number a person would write down.

    A mask of /31 or narrower has no room for a router and a host, and a mask of
    /0 describes no network to be the first host of.
    """
    host_address = socket.ntohl(address)
    host_mask = socket.ntohl(mask)

    if host_mask in (0x00000000, 0xFFFFFFFF, 0xFFFFFFFE):
        return None

    network = host_address & host_mask
    return _text(socket.htonl(network + 1))


def _adapters() -> list[_Adapter]:
    if os.name != "nt":
        return _posix_adapters()

    iphlpapi = ctypes.WinDLL("iphlpapi")
    get_adapters = iphlpapi.GetAdaptersAddresses
    get_adapters.argtypes = [
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        ctypes.POINTER(_IP_ADAPTER_ADDRESSES_LH),
        ctypes.POINTER(wintypes.ULONG),
    ]
    get_adapters.restype = wintypes.ULONG

    size = wintypes.ULONG(15_000)
    for _ in range(4):
        # A uint64 array keeps the buffer 8-byte aligned, which the adapter
        # table requires; a byte buffer only happens to be aligned.
        buffer = (ctypes.c_uint64 * ((size.value + 7) // 8))()
        pointer = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES_LH))
        result = get_adapters(
            _AF_INET,
            _GAA_FLAG_INCLUDE_GATEWAYS,
            None,
            pointer,
            ctypes.byref(size),
        )
        if result == _ERROR_BUFFER_OVERFLOW:
            continue
        if result != _NO_ERROR:
            return []

        found: list[_Adapter] = []
        adapter = pointer
        while adapter:
            current = adapter.contents
            adapter = current.Next
            if (
                current.OperStatus != _IF_OPER_STATUS_UP
                or current.IfType == _IF_TYPE_LOOPBACK
            ):
                continue

            entry = _Adapter(metric=int(current.Ipv4Metric))

            gateway_address = current.FirstGatewayAddress
            while gateway_address:
                gateway_entry = gateway_address.contents
                gateway_address = gateway_entry.Next
                text = _ipv4_from_sockaddr(gateway_entry.Address.lpSockaddr)
                if _usable(text):
                    entry.gateways.append(text)

            unicast = current.FirstUnicastAddress
            while unicast:
                unicast_entry = unicast.contents
                unicast = unicast_entry.Next
                text = _ipv4_from_sockaddr(unicast_entry.Address.lpSockaddr)
                if _usable(text):
                    entry.unicasts.append((text, int(unicast_entry.OnLinkPrefixLength)))

            found.append(entry)
        return found

    return []


def _usable(text: str | None) -> bool:
    """A gateway or address worth asking, rather than a DHCP place-holder.

    While a network is coming up Windows can report the link-local
    ``169.254.0.1`` as a gateway for a moment. Treating that as the router makes
    the watcher ask the wrong address three times and then rejoin a network that
    was about to be ready.
    """
    if not text:
        return False
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return (
        address.version == 4
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
    )


def _ipv4_from_sockaddr(sockaddr) -> str | None:
    if not sockaddr:
        return None
    if sockaddr.contents.sa_family != _AF_INET:
        return None
    inet = ctypes.cast(sockaddr, ctypes.POINTER(_SOCKADDR_IN)).contents
    return socket.inet_ntoa(bytes(inet.sin_addr))


def _posix_adapters() -> list[_Adapter]:
    """Enough of an answer for the tests on a non-Windows machine.

    The Windows build only ever runs this on Windows; the branch exists so the
    route arithmetic can be tested anywhere. It reads ``ip route`` when that is
    available and otherwise says there is nothing to report.
    """
    try:
        import subprocess

        completed = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    adapters: list[_Adapter] = []
    for line in completed.stdout.splitlines():
        columns = line.split()
        if "via" in columns:
            position = columns.index("via")
            if position + 1 < len(columns):
                adapters.append(_Adapter(metric=0, gateways=[columns[position + 1]]))
    return adapters


def _text(address: int) -> str | None:
    """``192.168.200.1`` from an integer in network byte order."""
    try:
        return socket.inet_ntoa(struct.pack("I", address))
    except OSError:
        return None
