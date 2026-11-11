"""Signs in to a captive-portal WiFi and keeps the session alive.

A Windows port of the macOS app in this repository: the same three step MikroTik
CHAP sign-in, the same session watcher, and a Tkinter window in place of the
menu bar icon. The standard library only: no PyGObject, no pip install, nothing
to build.
"""

from __future__ import annotations

__version__ = "1.1.0"

__all__ = ["__version__"]
