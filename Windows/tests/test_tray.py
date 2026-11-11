"""The tray icon's small, testable surface.

Starting a real icon is a Windows-shell operation; the live check drives that.
These cover the parts that must not depend on a notification area.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dormwifi import tray


class TrayTests(unittest.TestCase):
    def test_a_tooltip_is_capped_at_the_shells_limit(self):
        self.assertEqual(len(tray.tooltip_text("x" * 500)), 127)
        self.assertEqual(tray.tooltip_text("Dorm WiFi"), "Dorm WiFi")

    def test_every_menu_command_has_its_own_id(self):
        ids = [
            tray.CMD_SHOW,
            tray.CMD_TOGGLE,
            tray.CMD_SIGNIN,
            tray.CMD_LOG,
            tray.CMD_QUIT,
        ]
        self.assertEqual(len(ids), len(set(ids)))

    def test_an_icon_object_starts_idle(self):
        icon = tray.TrayIcon(Path("data/dormwifi.ico"))
        self.assertFalse(icon.available)
        self.assertEqual(icon.poll(), [])
        icon.remove()


if __name__ == "__main__":
    unittest.main()
