"""The window's wiring, without a screen.

Constructing GTK widgets needs a display connection but not a visible window, so
this builds the real window unmapped, pokes the real handlers, and reads what
came out of the labels. Skipped where there is no display at all.

Fixing the settings directory into a temporary one keeps the test out of the
real `~/.config`: the window appends a banner to the log the moment it starts.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HAVE_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    HAVE_GTK = True
except (ImportError, ValueError):  # pragma: no cover - depends on the machine
    HAVE_GTK = False


_APPLICATION = None


def shared_application():
    """One registered application for the whole file.

    A second Gtk.Application with the same id cannot register in the same
    process, so the tests share one and give each a fresh window.
    """
    global _APPLICATION
    if _APPLICATION is None:
        from dormwifi.app import DormWiFiApplication

        _APPLICATION = DormWiFiApplication(None)
        _APPLICATION.register(None)
    return _APPLICATION


@unittest.skipUnless(HAVE_GTK and HAVE_DISPLAY, "needs GTK 4 and a display")
class WindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_home = Path(self.directory.name)
        self.settings_path = self.config_home / "nhannhtdormwifihack" / "settings.json"

        self.patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_home)})
        self.patch.start()

        from dormwifi.settings import Settings

        Settings(wifi_name="Dorm WiFi", fallback_wifi_name="Phone").save(self.settings_path)

        from dormwifi.app import DormWiFiWindow

        self.application = shared_application()
        self.window = DormWiFiWindow(self.application, self.settings_path, autostart=False)

    def tearDown(self) -> None:
        self.window.shutdown()
        self.patch.stop()
        self.directory.cleanup()

    def test_the_window_opens_on_the_settings_it_was_given(self):
        self.assertEqual(self.window._network_value.get_text(), "Dorm WiFi")
        self.assertEqual(self.window._safety_value.get_text(), "Phone")

    def test_a_status_update_reaches_the_labels(self):
        from dormwifi.status_board import WatcherStatus

        self.window._apply_status(
            WatcherStatus(
                connection="signedIn",
                expires_at=time.time() + 745,
                router_ip="192.168.1.1",
                network="Dorm WiFi",
                has_internet=True,
            )
        )

        self.assertEqual(self.window._headline.get_text(), "Signed in, 12m 25s left")
        self.assertEqual(self.window._router_value.get_text(), "192.168.1.1")
        self.assertEqual(self.window._internet_value.get_text(), "up")
        self.assertEqual(self.window._toggle_button.get_label(), "Pause auto-connect")

    def test_a_paused_status_moves_the_button_back(self):
        from dormwifi.status_board import WatcherStatus

        self.window._apply_status(WatcherStatus(connection="stopped"))
        self.assertEqual(self.window._headline.get_text(), "Paused")
        self.assertEqual(self.window._toggle_button.get_label(), "Start auto-connect")

    def test_saying_something_shows_it_and_colours_it(self):
        # Every button in this window calls `_say`. A press whose only result is
        # a line in the log reads as broken.
        self.assertFalse(self.window._message.get_visible())

        self.window._say("Logged out (HTTP 200).", "ok")

        self.assertTrue(self.window._message.get_visible())
        self.assertEqual(self.window._message.get_text(), "Logged out (HTTP 200).")
        self.assertTrue(self.window._message.has_css_class("message-ok"))

        self.window._say("Could not reach the router.", "error")
        self.assertFalse(self.window._message.has_css_class("message-ok"))
        self.assertTrue(self.window._message.has_css_class("message-error"))

    def test_a_message_is_hidden_once_it_is_stale_but_a_newer_one_survives(self):
        self.window._say("first", "info")
        self.window._say("second", "info")

        # The first message's timer fires and must not clear the second.
        self.window._clear_message("first")
        self.assertTrue(self.window._message.get_visible())

        self.window._clear_message("second")
        self.assertFalse(self.window._message.get_visible())

    def test_a_log_line_appears_in_the_view_and_in_the_count(self):
        from dormwifi.activity_log import LogEntry

        before = self.window._log_buffer.get_line_count()
        self.window._append_entry(
            LogEntry(date=dt.datetime.now(), level="failure", message="the router said no")
        )

        self.assertEqual(self.window._log_buffer.get_line_count(), before + 1)
        text = self.window._log_buffer.get_text(
            self.window._log_buffer.get_start_iter(),
            self.window._log_buffer.get_end_iter(),
            True,
        )
        self.assertIn("the router said no", text)
        self.assertIn("lines this run", self.window._count_label.get_text())

    def test_an_unset_hotspot_is_shown_as_unset_not_as_the_first_saved_network(self):
        # Preselecting a saved network made "no hotspot chosen" look like "will
        # use this one", and then nothing was ever joined.
        self.window._apply_hotspots(["Dorm One", "Dorm Two"], "")

        model = self.window._hotspot_dropdown.get_model()
        self.assertEqual(model.get_string(0), "— no hotspot chosen —")
        self.assertEqual(self.window._hotspot_dropdown.get_selected(), 0)

    def test_the_hotspot_list_shows_a_network_networkmanager_does_not_know(self):
        # The name in the settings file is the one that counts, so it has to stay
        # visible even when it is not in the saved list. It drops off the list
        # exactly when the hotspot is renamed, which is when someone opens this
        # wondering why nothing connects.
        self.window._apply_hotspots(["Some Other Network"], "Dorm WiFi")

        model = self.window._hotspot_dropdown.get_model()
        names = [model.get_string(index) for index in range(model.get_n_items())]
        self.assertEqual(names[0], "Dorm WiFi (not saved)")
        self.assertIn("Some Other Network", names)
        self.assertEqual(self.window._hotspot_dropdown.get_selected(), 0)

    def test_selecting_the_network_already_set_says_so_instead_of_restarting(self):
        self.window._hotspot_suppressed = False
        model = Gtk.StringList.new(["Dorm WiFi"])
        self.window._hotspot_dropdown.set_model(model)

        self.window._on_hotspot_selected(self.window._hotspot_dropdown, None)

        self.assertIn("Already watching Dorm WiFi", self.window._message.get_text())

    def test_a_network_networkmanager_has_not_saved_is_refused_with_a_next_step(self):
        from dormwifi.settings import Settings

        self.window._hotspot_suppressed = False
        self.window._hotspot_dropdown.set_model(Gtk.StringList.new(["Nowhere (not saved)"]))

        self.window._on_hotspot_selected(self.window._hotspot_dropdown, None)

        self.assertIn("not saved", self.window._message.get_text())
        self.assertIn("Join it by hand", self.window._message.get_text())
        self.assertEqual(Settings.load(self.settings_path).wifi_name, "Dorm WiFi")

    def test_a_window_that_cannot_get_the_lock_becomes_a_viewer(self):
        # A systemd service or another terminal already watching means this
        # window must not start a second watcher: both would post to /login the
        # moment a session lapses, and the router rejects the second.
        from dormwifi.app import DormWiFiWindow
        from dormwifi.run_lock import RunLock

        holder = RunLock.acquire()
        self.assertIsNotNone(holder)
        observer = DormWiFiWindow(self.application, self.settings_path, autostart=True)
        try:
            self.assertTrue(observer._observing)
            self.assertIsNone(observer._scheduler)
            self.assertFalse(observer._toggle_button.get_sensitive())
            self.assertIn("Another watcher is already running", observer._message.get_text())
        finally:
            observer.shutdown()
            holder.release()

    def test_choosing_the_placeholder_explains_itself_and_writes_nothing(self):
        from dormwifi.settings import Settings

        self.window._hotspot_suppressed = False
        self.window._hotspot_dropdown.set_model(Gtk.StringList.new(["— no hotspot chosen —"]))

        self.window._on_hotspot_selected(self.window._hotspot_dropdown, None)

        self.assertIn("No hotspot chosen yet", self.window._message.get_text())
        self.assertEqual(Settings.load(self.settings_path).wifi_name, "Dorm WiFi")

    def test_choosing_another_network_is_written_to_the_settings_file(self):
        from dormwifi.settings import Settings

        self.window._hotspot_suppressed = False
        self.window._hotspot_dropdown.set_model(Gtk.StringList.new(["Another Dorm"]))

        self.window._on_hotspot_selected(self.window._hotspot_dropdown, None)

        self.assertEqual(Settings.load(self.settings_path).wifi_name, "Another Dorm")
        self.assertIn("Another Dorm", self.window._message.get_text())


if __name__ == "__main__":
    unittest.main()
