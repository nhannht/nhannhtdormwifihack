"""The Tkinter window's wiring, without a visible window.

The real widgets are built, the real handlers are poked, and the labels are read
back. Tkinter needs a display, so the tests skip themselves when there is none.
Fixing the settings directory into a temporary one keeps the test out of the
real `%APPDATA%`: the window appends a banner to the log the moment it starts.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk

    HAVE_TK = True
except ImportError:  # pragma: no cover - depends on the Python build
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs tkinter")
class WindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_home = Path(self.directory.name)
        self.settings_path = self.config_home / "settings.json"
        self.patch = mock.patch.dict(
            os.environ, {"DORMWIFI_CONFIG_DIR": str(self.config_home)}
        )
        self.patch.start()

        from dormwifi.settings import Settings

        Settings(wifi_name="Dorm WiFi", fallback_wifi_name="Phone").save(
            self.settings_path
        )

        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.patch.stop()
            self.directory.cleanup()
            self.skipTest("no display for tkinter")
        self.root.withdraw()

        from dormwifi.app import DormWiFiWindow

        self.networks = mock.patch(
            "dormwifi.app.saved_networks", return_value=["Dorm WiFi", "Phone"]
        )
        self.networks.start()
        self.window = DormWiFiWindow(
            self.root, self.settings_path, autostart=False, tray=False
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        try:
            self.window._quit()
        except tk.TclError:
            pass
        self.networks.stop()
        self.patch.stop()
        self.directory.cleanup()

    def test_the_window_starts_with_the_configured_network(self):
        self.assertEqual(self.window._headline.cget("text"), "Starting")
        self.assertEqual(self.window._network_value.cget("text"), "Dorm WiFi")
        self.assertEqual(self.window._safety_value.cget("text"), "Phone")

    def test_a_status_updates_the_headline_and_the_rows(self):
        from dormwifi.status_board import WatcherStatus

        self.window._apply_status(
            WatcherStatus(
                connection="signedIn",
                expires_at=time.time() + 745,
                network="Dorm WiFi",
                router_ip="192.168.200.1",
                has_internet=True,
            )
        )
        self.assertEqual(self.window._headline.cget("text"), "Signed in, 12m 25s left")
        self.assertEqual(self.window._router_value.cget("text"), "192.168.200.1")
        self.assertEqual(self.window._internet_value.cget("text"), "up")
        self.assertEqual(self.window._network_value.cget("text"), "Dorm WiFi")

    def test_the_message_answers_and_then_goes_away(self):
        self.window._say("Logged in.", "ok")
        self.root.update_idletasks()
        self.assertEqual(self.window._message.cget("text"), "Logged in.")
        self.assertTrue(self.window._message.winfo_manager())

        self.window._message_until = time.time() - 1
        self.window._clear_message()
        self.root.update_idletasks()
        self.assertEqual(self.window._message.winfo_manager(), "")

    def test_a_persistent_message_stays(self):
        self.window._say("No hotspot is chosen.", "error", persistent=True)
        self.window._message_until = None
        self.window._clear_message()
        self.assertEqual(self.window._message.winfo_manager(), "pack")

    def test_a_log_entry_is_shown_with_its_level(self):
        from dormwifi.activity_log import LogEntry

        self.window._append_entry(
            LogEntry(
                date=dt.datetime.now(),
                level="success",
                message="Logged in.",
            )
        )
        self.root.update_idletasks()
        text = self.window._log_view.get("1.0", "end")
        self.assertIn("Logged in.", text)
        self.assertEqual(self.window._log_view.tag_names("end-2c"), ("success",))

    def test_the_saved_networks_fill_the_picker(self):
        self.window._apply_hotspots((["Dorm WiFi", "Phone"], "Dorm WiFi"))
        self.assertEqual(self.window._hotspot.get(), "Dorm WiFi")
        self.assertIn("Phone", self.window._hotspot.cget("values"))

    def test_picking_a_network_writes_it_to_the_settings_file(self):
        from dormwifi.settings import Settings

        self.window._apply_hotspots((["Dorm WiFi", "Phone"], "Dorm WiFi"))
        self.window._hotspot.set("Phone")
        self.window._on_hotspot_selected()

        self.assertEqual(Settings.load(self.settings_path).wifi_name, "Phone")
        self.assertEqual(self.window._configured_wifi, "Phone")

    def test_picking_the_placeholder_writes_nothing(self):
        from dormwifi.settings import Settings

        self.window._apply_hotspots((["Dorm WiFi"], ""))
        self.window._hotspot.set("— no hotspot chosen —")
        self.window._on_hotspot_selected()

        self.assertEqual(Settings.load(self.settings_path).wifi_name, "Dorm WiFi")

    def test_closing_hides_to_the_tray_and_restores_from_it(self):
        fake = FakeTray()
        self.window._tray = fake

        self.window._on_close()

        self.assertEqual(self.root.state(), "withdrawn")
        self.assertTrue(fake.notes, "the first hide should explain where the window went")
        self.assertFalse(fake.removed)

        self.window._show_window()
        self.assertEqual(self.root.state(), "normal")

    def test_quit_removes_the_tray_icon(self):
        fake = FakeTray()
        self.window._tray = fake
        self.window._quit()
        self.assertTrue(fake.removed)

    def test_the_tray_menu_offers_start_when_nothing_is_running(self):
        labels = {label: enabled for _command, label, enabled in self.window._tray_menu()}
        self.assertIn("Start auto-connect", labels)
        self.assertTrue(labels["Sign in now"])
        self.assertTrue(labels["Show Dorm WiFi"])
        self.assertTrue(labels["Quit"])

    def test_the_tray_menu_disables_controls_while_only_watching(self):
        self.window._observing = True
        try:
            rows = {label: enabled for _command, label, enabled in self.window._tray_menu()}
            self.assertFalse(rows["Pause auto-connect"])
            self.assertFalse(rows["Sign in now"])
        finally:
            self.window._observing = False

    def test_the_tray_tooltip_follows_the_headline(self):
        from dormwifi.status_board import WatcherStatus

        fake = FakeTray()
        self.window._tray = fake
        self.window._status = WatcherStatus(connection="signedIn", expires_at=time.time() + 745)
        self.window._update_tray()
        self.assertIn("Signed in", fake.updates[-1])

    def test_the_picker_keeps_a_configured_network_windows_has_not_saved(self):
        self.window._apply_hotspots((["Phone"], "Renamed Dorm"))
        self.assertEqual(self.window._hotspot.get(), "Renamed Dorm (not saved)")


class FakeTray:
    """A tray icon that records what the window asked it to do."""

    def __init__(self) -> None:
        self.available = True
        self.notes: list[tuple[str, str]] = []
        self.updates: list[str] = []
        self.menus: list[list[tuple[int, str, bool]]] = []
        self.removed = False

    def set_menu(self, rows) -> None:
        self.menus.append(list(rows))

    def notify(self, title: str, text: str) -> None:
        self.notes.append((title, text))

    def update(self, tooltip: str) -> None:
        self.updates.append(tooltip)

    def remove(self) -> None:
        self.removed = True
        self.available = False


if __name__ == "__main__":
    unittest.main()
