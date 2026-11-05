"""Is there real internet, right now?

This opens a TCP connection to a public DNS server rather than fetching a web
page. A captive portal answers HTTP requests with its own page, so an HTTP check
can come back looking healthy while the connection is still locked. A TCP
handshake on port 53 either completes or it does not, and it settles in a
fraction of the time a page fetch takes.
"""

from __future__ import annotations

import socket

# Google first, Cloudflare second, so one operator being unreachable does not
# read as the whole connection being down.
DEFAULT_TARGETS: tuple[tuple[str, int], ...] = (("8.8.8.8", 53), ("1.1.1.1", 53))


def has_internet(
    timeout: float = 1.5,
    targets: tuple[tuple[str, int], ...] = DEFAULT_TARGETS,
) -> bool:
    return any(can_connect(host, port, timeout) for host, port in targets)


def can_connect(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
