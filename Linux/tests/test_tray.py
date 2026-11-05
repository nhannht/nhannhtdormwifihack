"""The tray icon, up to the edge of the bus.

The D-Bus objects are driven through the same handlers the desktop calls, with a
stand-in for the invocation, so the marshalling — the part that broke twice while
this was being written — is covered without needing a tray host to look at.
"""

from __future__ import annotations

import time
import unittest

from gi.repository import GLib

from dormwifi import tray
from dormwifi.status_board import WatcherStatus


def signed_in(**overrides) -> WatcherStatus:
    status = WatcherStatus(
        connection="signedIn",
        expires_at=time.time() + 745,
        network="INET - Free WiFi",
        router_ip="192.168.200.1",
        has_internet=True,
    )
    for key, value in overrides.items():
        setattr(status, key, value)
    return status


class FakeInvocation:
    def __init__(self) -> None:
        self.value: GLib.Variant | None = None
        self.error: str | None = None

    def return_value(self, value) -> None:
        self.value = value

    def return_error_literal(self, _domain, _code, message) -> None:
        self.error = message


class IconTests(unittest.TestCase):
    def test_every_state_draws_and_the_size_matches_what_is_announced(self):
        for connection in (
            "signedIn",
            "starting",
            "signingIn",
            "wrongNetwork",
            "onFallback",
            "stopped",
            "stopping",
        ):
            with self.subTest(connection=connection):
                pixels = tray.icon_pixmap(connection, has_internet=True)
                self.assertEqual(len(pixels), 22 * 22 * 4)

    def test_the_picture_changes_with_the_state(self):
        # A tray icon that looks the same in every state is decoration.
        pictures = {
            tray.icon_pixmap(connection, has_internet=True)
            for connection in ("signedIn", "signingIn", "wrongNetwork", "onFallback", "stopped")
        }
        self.assertEqual(len(pictures), 5)

    def test_internet_already_down_changes_the_picture(self):
        self.assertNotEqual(
            tray.icon_pixmap("signedIn", True), tray.icon_pixmap("signedIn", False)
        )

    def test_the_shape_carries_the_state_without_the_colour(self):
        # Colour is not the only signal: the outlines differ too, so a
        # monochrome or colour-blind rendering still says something.
        def alpha_outline(pixels: bytes) -> int:
            return sum(1 for index in range(0, len(pixels), 4) if pixels[index] > 128)

        painted = {
            connection: alpha_outline(tray.icon_pixmap(connection, True))
            for connection in ("signedIn", "signingIn", "wrongNetwork", "stopped")
        }
        self.assertEqual(len(set(painted.values())), len(painted), painted)

    def test_a_state_this_build_does_not_know_still_gets_a_picture(self):
        self.assertEqual(len(tray.icon_pixmap("something-new", True)), 22 * 22 * 4)

    def test_every_state_has_a_theme_icon_name_too(self):
        for connection in ("signedIn", "starting", "signingIn", "wrongNetwork", "onFallback"):
            with self.subTest(connection=connection):
                self.assertTrue(tray.icon_name_for(connection).endswith("-symbolic"))


class MenuTests(unittest.TestCase):
    def labels(self, entries) -> list[str]:
        return [entry.label for entry in entries if not entry.separator]

    def test_the_menu_leads_with_the_live_countdown(self):
        entries = tray.menu_entries(signed_in(), running=True)
        self.assertEqual(entries[0].label, "Signed in, 12m 25s left")
        self.assertFalse(entries[0].enabled)

    def test_network_router_and_internet_are_all_there(self):
        labels = self.labels(tray.menu_entries(signed_in(), running=True))
        self.assertIn("Network: INET - Free WiFi", labels)
        self.assertIn("Router:  192.168.200.1", labels)
        self.assertIn("Internet:  up", labels)

    def test_the_toggle_flips_with_the_state(self):
        running = self.labels(tray.menu_entries(signed_in(), running=True))
        paused = self.labels(tray.menu_entries(signed_in(), running=False))
        self.assertIn("Pause auto-connect", running)
        self.assertIn("Start auto-connect", paused)

    def test_a_watcher_owned_by_another_process_is_not_offered_as_a_button(self):
        # A systemd unit holds the lock, so the window is only a viewer. The menu
        # must not offer a Pause that cannot work.
        entries = tray.menu_entries(signed_in(), running=True, controlled=False)
        toggle = next(entry for entry in entries if entry.id == 6)
        sign_in = next(entry for entry in entries if entry.id == 7)
        self.assertEqual(toggle.label, "Watched by another process")
        self.assertFalse(toggle.enabled)
        self.assertFalse(sign_in.enabled)

    def test_the_menu_has_two_separators_and_unique_ids(self):
        entries = tray.menu_entries(signed_in(), running=True)
        self.assertEqual(sum(1 for entry in entries if entry.separator), 2)
        self.assertEqual(len({entry.id for entry in entries}), len(entries))

    def test_an_empty_status_still_says_something(self):
        entries = tray.menu_entries(WatcherStatus(), running=False)
        self.assertEqual(entries[0].label, "Starting")
        self.assertIn("Network: —", self.labels(entries))

    def test_only_a_missing_router_asks_for_attention(self):
        self.assertTrue(tray.needs_attention(signed_in(connection="wrongNetwork")))
        for connection in ("signedIn", "stopped", "signingIn"):
            with self.subTest(connection=connection):
                self.assertFalse(tray.needs_attention(signed_in(connection=connection)))


class BusHandlerTests(unittest.TestCase):
    """The marshalling, driven the way the desktop drives it."""

    def setUp(self) -> None:
        self.pressed: list[str] = []
        self.subject = tray.TrayIcon(
            "Dorm WiFi",
            on_activate=lambda: self.pressed.append("activate"),
            on_secondary_activate=lambda: self.pressed.append("secondary"),
            on_toggle=lambda: self.pressed.append("toggle"),
            on_sign_in=lambda: self.pressed.append("sign-in"),
            on_quit=lambda: self.pressed.append("quit"),
        )
        self.subject.update(signed_in(), running=True)

    def call(self, method: str, parameters: GLib.Variant) -> FakeInvocation:
        invocation = FakeInvocation()
        self.subject._on_menu_call(
            None, None, tray.PATH_MENU, tray.INTERFACE_MENU, method, parameters, invocation
        )
        return invocation

    def test_get_layout_returns_a_menu_the_desktop_can_read(self):
        invocation = self.call("GetLayout", GLib.Variant("(iias)", (0, -1, [])))

        self.assertIsNone(invocation.error)
        self.assertIsNotNone(invocation.value)
        self.assertEqual(invocation.value.get_type_string(), "(u(ia{sv}av))")
        text = invocation.value.print_(True)
        self.assertIn("Signed in, 12m 25s left", text)
        self.assertIn("Pause auto-connect", text)
        # Two output arguments: the revision, then the root node.
        self.assertEqual(invocation.value.n_children(), 2)

    def test_get_group_properties_returns_one_row_per_item(self):
        invocation = self.call("GetGroupProperties", GLib.Variant("(aias)", ([1, 6], [])))

        self.assertIsNone(invocation.error)
        self.assertEqual(invocation.value.get_type_string(), "(a(ia{sv}))")
        rows = invocation.value.unpack()[0]
        self.assertEqual([row[0] for row in rows], [1, 6])

    def test_get_property_answers_for_a_label(self):
        invocation = self.call("GetProperty", GLib.Variant("(is)", (6, "label")))

        self.assertIsNone(invocation.error)
        self.assertEqual(invocation.value.unpack()[0], "Pause auto-connect")

    def test_clicking_an_item_runs_its_callback(self):
        for item_id, expected in (
            (6, "toggle"),
            (7, "sign-in"),
            (8, "activate"),
            (10, "quit"),
        ):
            with self.subTest(item_id=item_id):
                self.pressed.clear()
                invocation = self.call(
                    "Event", GLib.Variant("(isvu)", (item_id, "clicked", GLib.Variant("s", ""), 0))
                )
                self.assertIsNone(invocation.error)
                self.assertEqual(self.pressed, [expected])

    def test_clicking_the_headline_does_nothing(self):
        self.call("Event", GLib.Variant("(isvu)", (1, "clicked", GLib.Variant("s", ""), 0)))
        self.assertEqual(self.pressed, [])

    def test_an_event_group_runs_every_click(self):
        self.call(
            "EventGroup",
            GLib.Variant("(a(isvu))", ([(6, "clicked", GLib.Variant("s", ""), 0)],)),
        )
        self.assertEqual(self.pressed, ["toggle"])

    def test_an_unknown_method_is_an_error_not_a_crash(self):
        invocation = self.call("NotAMethod", GLib.Variant("()", ()))
        self.assertIsNotNone(invocation.error)

    def test_the_item_properties_answer_for_everything_the_desktop_asks(self):
        for name in (
            "Category",
            "Id",
            "Title",
            "Status",
            "IconName",
            "IconPixmap",
            "ToolTip",
            "ItemIsMenu",
            "Menu",
        ):
            with self.subTest(name=name):
                value = self.subject._on_sni_property(
                    None, None, tray.PATH_SNI, tray.INTERFACE_SNI, name
                )
                self.assertIsNotNone(value)

    def test_the_tooltip_carries_the_countdown(self):
        value = self.subject._on_sni_property(
            None, None, tray.PATH_SNI, tray.INTERFACE_SNI, "ToolTip"
        )
        self.assertIn("12m 25s left", value.print_(True))

    def test_the_status_asks_for_attention_when_the_router_is_gone(self):
        self.subject.update(signed_in(connection="wrongNetwork"), running=True)
        value = self.subject._on_sni_property(
            None, None, tray.PATH_SNI, tray.INTERFACE_SNI, "Status"
        )
        self.assertEqual(value.unpack(), "NeedsAttention")

    def test_the_menu_offers_no_controls_while_another_process_watches(self):
        self.subject.update(signed_in(), running=True, controlled=False)
        text = self.call("GetLayout", GLib.Variant("(iias)", (0, -1, []))).value.print_(True)
        self.assertIn("Watched by another process", text)

    def test_stopping_an_icon_that_never_started_is_harmless(self):
        tray.TrayIcon("x", lambda: None, lambda: None, lambda: None, lambda: None, lambda: None).stop()


if __name__ == "__main__":
    unittest.main()
