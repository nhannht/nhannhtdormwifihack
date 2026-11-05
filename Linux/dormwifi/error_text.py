"""Turns networking errors into something worth reading.

A traceback from urllib wraps the one word that matters in a nested chain of
exception types. In a log that scrolls past every two seconds, that buries it.
"""

from __future__ import annotations

import errno
import socket
import ssl
import urllib.error


def short(error: BaseException) -> str:
    """A few words for `error`, for a log line."""
    # Our own errors already say what went wrong in plain words.
    for attribute in ("description",):
        own = getattr(error, attribute, None)
        if isinstance(own, str) and own and type(error).__module__.startswith("dormwifi"):
            return own

    if isinstance(error, urllib.error.URLError) and not isinstance(
        error, urllib.error.HTTPError
    ):
        return short(error.reason) if isinstance(error.reason, BaseException) else str(error.reason)

    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timed out"
    if isinstance(error, ConnectionRefusedError):
        return "nothing is listening there"
    if isinstance(error, socket.gaierror):
        return "that name does not resolve"
    if isinstance(error, ssl.SSLError):
        return "the secure connection failed"
    if isinstance(error, ssl.CertificateError):
        return "the certificate was not trusted"
    if isinstance(error, OSError):
        if error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            return "this machine has no network"
        if error.errno == errno.ECONNRESET:
            return "the connection dropped"

    return str(error) or type(error).__name__


def duration(seconds: int) -> str:
    """`77 days`, `4h 20m`, `45s`. For telling someone how long has passed."""
    seconds = max(int(seconds), 0)

    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"

    days = seconds // 86400
    return "1 day" if days == 1 else f"{days} days"
