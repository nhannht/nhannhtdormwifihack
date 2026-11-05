"""Signs in to a captive-portal WiFi and keeps the session alive.

A Linux port of the macOS app in this repository: the same three step MikroTik
CHAP sign-in, the same session watcher, and a GTK 4 window in place of the menu
bar icon. Standard library plus PyGObject, nothing else.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
