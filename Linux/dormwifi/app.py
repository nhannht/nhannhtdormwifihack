"""The window, drawn with GTK 4.

It is one window rather than a menu bar icon because GTK 4 removed the status
icon and so did most of the desktops that ship it. What the macOS build puts in
a drop-down is here laid out flat: the state at the top, the three actions under
it, then the log.

The watcher runs on a thread of its own and pushes its state to a
:class:`~dormwifi.status_board.StatusBoard`; this module subscribes and hands the
updates to the main loop with ``GLib.idle_add``. GTK is not thread-safe, so
nothing off the worker thread touches a widget, and everything here assumes it is
on the main loop.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402  - after require_version

from . import error_text  # noqa: E402
from .activity_log import ActivityLog, LogEntry, entries_in_file  # noqa: E402
from .login_engine import LoginEngine  # noqa: E402
from .portal_client import PortalClient  # noqa: E402
from .run_lock import RunLock  # noqa: E402
from .session_scheduler import SessionScheduler  # noqa: E402
from .settings import Settings, default_path, support_directory  # noqa: E402
from .status_board import (  # noqa: E402
    ON_FALLBACK,
    SIGNED_IN,
    SIGNING_IN,
    STARTING,
    STOPPED,
    STOPPING,
    WRONG_NETWORK,
    StatusBoard,
    StatusFile,
    WatcherStatus,
)
from .tray import TrayIcon  # noqa: E402
from .wifi import saved_networks  # noqa: E402

APPLICATION_ID = "vn.io.nhannht.dormwifihack"

_CSS = """
.headline { font-size: 20pt; font-weight: 600; }
.detail { opacity: 0.75; }
.message { padding: 8px 12px; border-radius: 6px; }
.message-ok { background: alpha(#2ec27e, 0.15); }
.message-error { background: alpha(#e01b24, 0.15); }
.message-info { background: alpha(#3584e4, 0.15); }
.log-view { font-family: monospace; font-size: 10pt; }
.len-warning { color: #e5a50a; }
.len-failure { color: #e01b24; }
.len-success { color: #2ec27e; }
.len-info { color: alpha(currentColor, 0.72); }
.count { opacity: 0.7; font-size: 9pt; }
"""

#: A state's icon, chosen so the answer is readable without relying on colour.
_DEFAULT_ICON = "view-refresh-symbolic"

_STATE_ICONS = {
    SIGNED_IN: "emblem-ok-symbolic",
    STARTING: "view-refresh-symbolic",
    SIGNING_IN: "view-refresh-symbolic",
    WRONG_NETWORK: "dialog-warning-symbolic",
    ON_FALLBACK: "network-wireless-symbolic",
    STOPPED: "media-playback-pause-symbolic",
    STOPPING: "process-stop-symbolic",
}


#: What the picker says when the settings file names no hotspot. Without it the
#: dialog silently preselected whichever saved network sorted first, which reads
#: as "it is going to use that one" when it is going to use none.
_NO_HOTSPOT = "— no hotspot chosen —"


class DormWiFiWindow(Gtk.ApplicationWindow):
    """The one window: state at the top, actions in the middle, log at the bottom."""

    def __init__(
        self,
        application: Gtk.Application,
        settings_path: Path | None,
        autostart: bool = True,
    ) -> None:
        super().__init__(application=application, title="Dorm WiFi")
        self.set_default_size(900, 660)

        self._settings_path = settings_path
        self._board = StatusBoard()
        self._status_file = StatusFile()
        self._log_path = support_directory() / "activity.log"
        self._log = ActivityLog(file_path=self._log_path, truncate_file=False)

        self._scheduler: SessionScheduler | None = None
        self._worker: threading.Thread | None = None
        self._lock: RunLock | None = None
        self._observing = False
        self._status = WatcherStatus()
        self._log_offset = 0
        self._configured_wifi = ""
        self._tray: TrayIcon | None = None
        self._told_about_hiding = False
        self._build()

        self._unsubscribe_log = self._log.subscribe(self._on_log_entry)
        self._unsubscribe_board = self._board.subscribe(self._on_status)

        if autostart:
            self._start_tray()

        self._load_history()
        self._configured_wifi = self._settings_wifi_name()
        self._network_value.set_text(self._configured_wifi or "—")
        if autostart:
            self._start_watcher()
        else:
            # Built for a test, or opened to look at: no lock, no watcher.
            self._populate_hotspots(self._settings_wifi_name())
        self.connect("close-request", self._on_close)
        GLib.timeout_add(1000, self._tick)

    # MARK: - Construction

    def _build(self) -> None:
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        self._menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Open settings file", "win.open-settings")
        menu.append("Open log file", "win.open-log")
        menu.append("Refresh networks", "win.refresh-networks")
        menu.append("Quit", "win.quit")
        self._menu_button.set_menu_model(menu)
        header.pack_end(self._menu_button)

        for name, callback in (
            ("open-settings", self._on_open_settings),
            ("open-log", self._on_open_log),
            ("refresh-networks", self._on_refresh_networks),
            ("quit", lambda *_: self._quit()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_margin_top(16)
        outer.set_margin_bottom(16)
        outer.set_margin_start(16)
        outer.set_margin_end(16)
        self.set_child(outer)

        # The state, in the same order the macOS menu shows it.
        state_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._state_icon = Gtk.Image.new_from_icon_name(_STATE_ICONS[STARTING])
        self._state_icon.set_pixel_size(28)
        state_box.append(self._state_icon)

        self._headline = Gtk.Label(label="Starting", xalign=0)
        self._headline.add_css_class("headline")
        state_box.append(self._headline)
        outer.append(state_box)

        grid = Gtk.Grid(column_spacing=16, row_spacing=4)
        grid.set_margin_top(4)
        self._network_value = Gtk.Label(label="—", xalign=0, selectable=True)
        self._router_value = Gtk.Label(label="—", xalign=0, selectable=True)
        self._internet_value = Gtk.Label(label="—", xalign=0, selectable=True)
        self._safety_value = Gtk.Label(label="—", xalign=0, selectable=True)
        for row, (title, value) in enumerate(
            (
                ("Network", self._network_value),
                ("Router", self._router_value),
                ("Internet", self._internet_value),
                ("Safety net", self._safety_value),
            )
        ):
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("detail")
            grid.attach(label, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
        outer.append(grid)

        # Something every action can answer with. Never a bare button press with
        # nothing to show for it.
        self._message = Gtk.Label(label="", xalign=0, wrap=True)
        self._message.add_css_class("message")
        self._message.set_visible(False)
        outer.append(self._message)

        # Two actions, both of which work on this hotspot. There used to be a
        # third, Log out: it is gone because this router stops answering /login
        # the moment a session is ended, so the button could only strand the
        # machine. Signing in again is what the app is for; leaving is not
        # something it can do safely.
        self._actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._toggle_button = Gtk.Button(label="Pause auto-connect")
        self._toggle_button.set_tooltip_text(
            "Stop or start signing in again each time the session lapses."
        )
        self._toggle_button.connect("clicked", self._on_toggle_watcher)
        self._actions.append(self._toggle_button)

        self._sign_in_button = Gtk.Button(label="Sign in now")
        self._sign_in_button.set_tooltip_text(
            "Sign in immediately, through the portal, without waiting for the session to lapse."
        )
        self._sign_in_button.connect("clicked", self._on_sign_in_now)
        self._actions.append(self._sign_in_button)

        outer.append(self._actions)

        # What the two buttons do, in one line, so a disabled control never has
        # to be decoded. The same label carries the explanation when another
        # watcher owns the connection and there is nothing here to press.
        self._action_hint = Gtk.Label(
            label="Pause stops the automatic sign-in. Sign in now tries once, immediately.",
            xalign=0,
            wrap=True,
        )
        self._action_hint.add_css_class("detail")
        outer.append(self._action_hint)

        hotspot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hotspot_label = Gtk.Label(label="Hotspot network")
        hotspot_box.append(hotspot_label)
        self._hotspot_dropdown = Gtk.DropDown.new_from_strings(["Loading…"])
        self._hotspot_dropdown.set_enable_search(True)
        self._hotspot_dropdown.set_hexpand(True)
        # `notify::selected` fires for the initial fill as well, which would
        # write a network nobody chose into the settings file.
        self._hotspot_suppressed = True
        self._hotspot_dropdown.connect("notify::selected", self._on_hotspot_selected)
        hotspot_box.append(self._hotspot_dropdown)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Ask NetworkManager for the saved networks again")
        refresh.connect("clicked", self._on_refresh_networks)
        hotspot_box.append(refresh)

        outer.append(hotspot_box)

        frame = Gtk.Frame(label="Log")
        frame.set_vexpand(True)
        frame_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame_box.set_margin_top(6)
        frame_box.set_margin_bottom(6)
        frame_box.set_margin_start(6)
        frame_box.set_margin_end(6)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._log_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self._log_view.add_css_class("log-view")
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_buffer = self._log_view.get_buffer()
        self._add_log_tags()
        scroller.set_child(self._log_view)
        self._log_scroller = scroller
        frame_box.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._follow = Gtk.CheckButton(label="Follow new lines")
        self._follow.set_active(True)
        footer.append(self._follow)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)

        self._count_label = Gtk.Label(label="0 lines")
        self._count_label.add_css_class("count")
        footer.append(self._count_label)

        open_button = Gtk.Button(label="Open log file")
        open_button.connect("clicked", self._on_open_log)
        footer.append(open_button)

        frame_box.append(footer)
        frame.set_child(frame_box)
        outer.append(frame)

        self._refresh_safety_net()

    def _add_log_tags(self) -> None:
        self._tags = {}
        for level, css in (
            ("info", "len-info"),
            ("success", "len-success"),
            ("warning", "len-warning"),
            ("failure", "len-failure"),
        ):
            tag = self._log_buffer.create_tag(level)
            self._tags[level] = tag

    # MARK: - The tray icon

    def _start_tray(self) -> None:
        """Ask the desktop for an icon, and carry on without one if it says no."""
        tray = TrayIcon(
            title="Dorm WiFi",
            on_activate=self._show_window,
            on_secondary_activate=self._toggle_watcher,
            on_toggle=self._toggle_watcher,
            on_sign_in=self._sign_in_now,
            on_quit=self._quit,
        )
        if tray.start():
            self._tray = tray
            self._update_tray()
        else:
            self._log.info(
                "No tray icon: this session has no StatusNotifierWatcher. The window is the "
                "only way to see or change the watcher."
            )

    def _update_tray(self) -> None:
        if self._tray is None:
            return
        self._tray.update(
            self._status,
            running=self._scheduler is not None,
            controlled=not self._observing,
        )

    def _show_window(self) -> None:
        self.set_visible(True)
        self.present()

    def _settings_wifi_name(self) -> str:
        try:
            return Settings.load(self._settings_path).wifi_name
        except (OSError, ValueError):
            return ""

    # MARK: - The watcher

    def _start_watcher(self) -> None:
        """Run the watcher here, or follow one that is already running."""
        try:
            settings = Settings.load(self._settings_path)
        except (OSError, ValueError) as error:
            self._say(
                f"Cannot read the settings file: {error_text.short(error)}", "error", persistent=True
            )
            return

        if self._lock is None:
            self._lock = RunLock.acquire()

        if self._lock is None:
            # A systemd service or a terminal already holds the singleton lock.
            # Two watchers would both post to /login the moment a session lapses,
            # and the router rejects the second. So this window becomes a viewer
            # instead of fighting for it.
            self._observing = True
            self._say(
                "Another watcher is already running, so this window is only watching it. "
                f"Its status is read from {self._status_file.path}.",
                "info",
                persistent=True,
            )
            # No controls, rather than three grey ones: a disabled button
            # cannot say why it is disabled, and this window cannot pause, start
            # or sign in to a watcher that is not its own.
            self._actions.set_visible(False)
            self._action_hint.set_text(
                "Another watcher is running — a terminal, or the installed service — so this "
                "window only shows its status. Stop that one to control the watcher from here."
            )
            self._load_observer_status()
            GLib.timeout_add(1000, self._poll_status_file)
            return

        scheduler = SessionScheduler(
            settings, self._log, board=self._board, status_file=self._status_file
        )
        self._scheduler = scheduler

        def work() -> None:
            scheduler.run()

        self._worker = threading.Thread(target=work, name="watcher", daemon=True)
        self._worker.start()

        self._configured_wifi = settings.wifi_name
        self._toggle_button.set_label("Pause auto-connect")
        if settings.wifi_name:
            self._say("Watching. Signing in if the session has lapsed.", "info")
        else:
            self._say(
                "Watching, but no hotspot is chosen, so this machine will never be moved onto "
                "one. Pick the dorm network in the list below.",
                "error",
                persistent=True,
            )
        self._populate_hotspots(settings.wifi_name)

    def _stop_watcher(self, after: Callable[[], None] | None = None) -> None:
        """Stop, and do not claim to be stopped until it really is.

        Cancelling returns at once while the loop may still be inside a sleep of
        up to a minute, or inside a wireless join that shells out and cannot
        notice cancellation at all. Reporting "Paused" during that would be a
        lie, and a pause the user cannot trust is worse than none.
        """
        scheduler = self._scheduler
        worker = self._worker
        if scheduler is None or worker is None:
            return

        self._scheduler = None
        self._worker = None
        self._toggle_button.set_sensitive(False)
        self._toggle_button.set_label("Stopping…")
        self._say("Stopping, waiting for the last step to finish.", "info", persistent=True)

        def finish() -> None:
            scheduler.stop()
            worker.join(timeout=90)
            if self._lock is not None:
                self._lock.release()
                self._lock = None
            GLib.idle_add(self._finish_stopping, after)

        threading.Thread(target=finish, name="stopper", daemon=True).start()

    def _finish_stopping(self, after: Callable[[], None] | None) -> bool:
        self._toggle_button.set_sensitive(True)
        self._toggle_button.set_label("Start auto-connect")
        if after is not None:
            after()
        else:
            self._say("Stopped. Nothing is keeping the session alive.", "info", persistent=True)
        return False

    # MARK: - Feedback

    def _say(self, text: str, kind: str, *, persistent: bool = False) -> None:
        """Answer whatever was just pressed, in words.

        Every control in this window calls this. A press that changes nothing
        visible reads as broken, and the user has no way to tell the difference.
        """
        self._message.set_text(text)
        for name in ("message-ok", "message-error", "message-info"):
            self._message.remove_css_class(name)
        self._message.add_css_class(f"message-{kind}")
        self._message.set_visible(True)
        if not persistent:
            GLib.timeout_add_seconds(9, self._clear_message, text)

    def _clear_message(self, expected: str) -> bool:
        if self._message.get_text() == expected:
            self._message.set_visible(False)
        return False

    # MARK: - State

    def _on_status(self, status: WatcherStatus) -> None:
        """Called from the watcher's thread. Hand it to the main loop."""
        GLib.idle_add(self._apply_status, status)

    def _apply_status(self, status: WatcherStatus) -> bool:
        self._status = status
        self._state_icon.set_from_icon_name(_STATE_ICONS.get(status.connection, _DEFAULT_ICON))
        self._headline.set_text(status.headline)

        # Falls back to the settings file, so the row says which network this is
        # for even before the watcher has reported anything.
        network = status.network or self._configured_wifi or "—"
        self._network_value.set_text(network)
        self._router_value.set_text(status.router_ip or "—")
        self._internet_value.set_text("up" if status.has_internet else "down")

        self._update_tray()

        if not self._observing:
            if status.connection == STOPPING:
                self._toggle_button.set_label("Stopping…")
            elif status.connection == STOPPED:
                self._toggle_button.set_label("Start auto-connect")
            else:
                self._toggle_button.set_label("Pause auto-connect")

        return False

    def _tick(self) -> bool:
        """Redraw the headline each second so the countdown moves as you read it.

        The tray label comes from the same deadline, so it moves with it. Asking
        to update every second costs nothing: the tray only talks to the desktop
        when the words actually changed.
        """
        if not self._observing:
            self._headline.set_text(self._status.headline)
        self._update_tray()
        return True

    def _load_observer_status(self) -> None:
        status = self._status_file.read()
        if status is not None:
            self._apply_status(status)

    def _poll_status_file(self) -> bool:
        if not self._observing:
            return False
        self._load_observer_status()
        self._tail_log_file()
        return True

    # MARK: - Log

    def _load_history(self) -> None:
        """Put the recent past in the window before the first new line arrives."""
        for entry in entries_in_file(self._log_path, 150):
            self._append_entry(entry, scroll=False)
        self._log_offset = self._log_path.stat().st_size if self._log_path.exists() else 0

    def _on_log_entry(self, entry: LogEntry) -> None:
        GLib.idle_add(self._append_entry, entry, True)

    def _append_entry(self, entry: LogEntry, scroll: bool = True) -> bool:
        self._log_buffer.insert_with_tags(
            self._log_buffer.get_end_iter(),
            entry.formatted + "\n",
            self._tags.get(entry.level, self._tags["info"]),
        )
        lines = max(self._log_buffer.get_line_count() - 1, 0)
        self._count_label.set_text(f"{lines} lines this run")
        if scroll and self._follow.get_active():
            self._scroll_to_end()
        return False

    def _tail_log_file(self) -> None:
        """Read whatever another process appended to the log since last time."""
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return
        if size < self._log_offset:
            self._log_offset = 0
        if size == self._log_offset:
            return

        try:
            with self._log_path.open("rb") as stream:
                stream.seek(self._log_offset)
                chunk = stream.read()
        except OSError:
            return
        self._log_offset = size

        for line in chunk.decode("utf-8", "replace").splitlines():
            entry = LogEntry.parse_file_line(line)
            if entry is not None:
                self._append_entry(entry, scroll=False)
        if self._follow.get_active():
            self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        def run() -> bool:
            adjustment = self._log_scroller.get_vadjustment()
            adjustment.set_value(max(adjustment.get_upper() - adjustment.get_page_size(), 0))
            return False

        GLib.idle_add(run)

    # MARK: - Networks

    def _populate_hotspots(self, current: str) -> None:
        def work() -> None:
            networks = saved_networks()
            GLib.idle_add(self._apply_hotspots, networks, current)

        threading.Thread(target=work, name="networks", daemon=True).start()

    def _apply_hotspots(self, networks: list[str], current: str) -> bool:
        choices = list(networks)

        if not current:
            # Nothing is configured, and the list must say so. Preselecting a
            # saved network here made "no hotspot chosen" look like "will use the
            # first one", which is what it did instead: nothing.
            selected = 0
            choices.insert(0, _NO_HOTSPOT)
        elif current not in choices:
            # The name in the settings file is the one that counts, so it has to
            # be visible even when the machine has not saved it. It drops off
            # when the hotspot is renamed, which is the very moment someone opens
            # this wondering why nothing is connecting.
            choices.insert(0, f"{current} (not saved)")
            selected = 0
        else:
            selected = choices.index(current)

        self._hotspot_suppressed = True
        self._hotspot_dropdown.set_model(
            Gtk.StringList.new(choices or ["NetworkManager has no saved networks"])
        )
        self._hotspot_dropdown.set_selected(selected)
        self._hotspot_suppressed = False
        return False

    # MARK: - Actions

    def _on_toggle_watcher(self, _button: Gtk.Button) -> None:
        self._toggle_watcher()

    def _toggle_watcher(self) -> None:
        """Pause or start, from the button or from the tray icon."""
        if self._observing:
            self._say(
                "Another watcher is running, so this window cannot pause it.",
                "error",
                persistent=True,
            )
            return
        if self._scheduler is None:
            self._start_watcher()
        else:
            self._stop_watcher()

    def _on_sign_in_now(self, _button: Gtk.Button) -> None:
        self._sign_in_now()

    def _sign_in_now(self) -> None:
        if self._observing:
            return
        try:
            settings = Settings.load(self._settings_path).resolving_gateway_waiting_up_to(10)
        except (OSError, ValueError) as error:
            self._say(f"Cannot read the settings file: {error_text.short(error)}", "error")
            return

        if not settings.gateway_ip:
            self._say(
                "No router address: this machine is not on a network. Join one first.",
                "error",
                persistent=True,
            )
            return

        self._sign_in_button.set_sensitive(False)
        self._sign_in_button.set_label("Signing in…")
        self._say(f"Signing in through {settings.gateway_ip}…", "info", persistent=True)

        def work() -> None:
            # The button is disabled while this runs, and only the callback below
            # enables it again. A sign-in that raised would therefore leave a
            # button that never comes back, which reads as a broken window long
            # after the real failure is forgotten.
            try:
                result = LoginEngine(settings, self._log).run()
            except Exception as error:  # noqa: BLE001 - always answer the button
                GLib.idle_add(
                    self._finish_sign_in,
                    f"Sign-in stopped unexpectedly: {error_text.short(error)}",
                    False,
                )
                return
            GLib.idle_add(self._finish_sign_in, result.summary, result.is_connected)

        threading.Thread(target=work, name="sign-in", daemon=True).start()

    def _finish_sign_in(self, summary: str, connected: bool) -> bool:
        self._progress_done()
        self._say(summary, "ok" if connected else "error", persistent=not connected)
        return False

    def _progress_done(self) -> None:
        self._sign_in_button.set_sensitive(True)
        self._sign_in_button.set_label("Sign in now")

    def _on_hotspot_selected(self, dropdown: Gtk.DropDown, _param) -> None:
        if self._hotspot_suppressed:
            return

        selected = dropdown.get_selected_item()
        if selected is None:
            return
        name = selected.get_string()
        if name == _NO_HOTSPOT:
            self._say(
                "No hotspot chosen yet, so nothing is being joined. Pick the dorm network "
                "from this list.",
                "error",
                persistent=True,
            )
            return
        if name.endswith(" (not saved)"):
            self._say(
                f"{name.removesuffix(' (not saved)')} is not saved by NetworkManager, so it "
                "cannot be rejoined from here. Join it by hand once, with 'Automatically "
                "connect' ticked.",
                "error",
                persistent=True,
            )
            return

        try:
            settings = Settings.load(self._settings_path)
            if settings.wifi_name == name:
                self._say(f"Already watching {name}.", "info")
                return
            settings.wifi_name = name
            settings.save(self._settings_path)
            self._configured_wifi = name
        except (OSError, ValueError) as error:
            self._say(f"Could not record the choice: {error_text.short(error)}", "error")
            return

        self._refresh_safety_net()
        self._say(f"Hotspot set to {name}. Restarting the watcher.", "ok")

        if self._scheduler is not None:
            self._stop_watcher(after=self._start_watcher)

    def _quit(self) -> None:
        application = self.get_application()
        if application is not None:
            application.quit()

    def _on_refresh_networks(self, *_args) -> None:
        self._say("Asking NetworkManager for its saved networks…", "info")
        try:
            current = Settings.load(self._settings_path).wifi_name
        except (OSError, ValueError):
            current = ""
        self._populate_hotspots(current)

    def _on_open_log(self, *_args) -> None:
        self._open_path(self._log_path)

    def _on_open_settings(self, *_args) -> None:
        path = Path(self._settings_path) if self._settings_path else default_path()
        if not path.exists():
            try:
                Settings().save(path)
            except OSError as error:
                self._say(f"Could not create {path}: {error}", "error")
                return
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            self._say(f"{path} does not exist yet.", "error")
            return
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
            self._say(f"Opened {path}.", "ok")
        except GLib.Error as error:
            self._say(f"Could not open {path}: {error.message}", "error", persistent=True)

    def _refresh_safety_net(self) -> None:
        try:
            settings = Settings.load(self._settings_path)
        except (OSError, ValueError):
            self._safety_value.set_text("—")
            return
        self._safety_value.set_text(settings.fallback_wifi_name or "off")

    # MARK: - Closing

    def _on_close(self, *_args) -> bool:
        """Hide rather than quit, when there is an icon to get back to.

        With a tray icon this is the normal shape of a background app: closing
        the window leaves the watcher running and the icon is the way back. The
        notification exists because a window that disappears with no word reads
        as a crash, and because someone who wanted to quit needs to know where
        Quit went.

        Without an icon there would be no way back at all, so closing quits.
        """
        if self._tray is None:
            self._quit()
            return False

        self.set_visible(False)
        if not self._told_about_hiding:
            self._told_about_hiding = True
            self._notify_hidden()
        return True

    def _notify_hidden(self) -> None:
        application = self.get_application()
        if application is None:
            return
        notification = Gio.Notification.new("Dorm WiFi is still watching")
        network = self._configured_wifi or "the hotspot"
        notification.set_body(
            f"Keeping {network} signed in. The icon in the top bar pauses it or quits."
        )
        try:
            application.send_notification("still-watching", notification)
        except GLib.Error:
            pass


    # MARK: - Shutdown

    def shutdown(self) -> None:
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        try:
            self._unsubscribe_board()
            self._unsubscribe_log()
        except AttributeError:
            pass
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._worker is not None:
            self._worker.join(timeout=5)
        if self._lock is not None:
            self._lock.release()
            self._lock = None


class DormWiFiApplication(Gtk.Application):
    def __init__(self, settings_path: Path | None, flags: int = 0) -> None:
        super().__init__(application_id=APPLICATION_ID, flags=flags)
        self._settings_path = settings_path
        self._window: DormWiFiWindow | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self) -> None:
        if self._window is None:
            self._window = DormWiFiWindow(self, self._settings_path)
        self._window.present()

    def do_shutdown(self) -> None:
        if self._window is not None:
            self._window.shutdown()
        Gtk.Application.do_shutdown(self)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dormwifi gui", description="The Dorm WiFi window.")
    parser.add_argument("--settings", "-s", metavar="PATH", help="Path to the settings file.")
    options, remaining = parser.parse_known_args(argv)

    application = DormWiFiApplication(
        Path(options.settings).expanduser() if options.settings else None
    )
    extra = [a for a in (remaining or []) if not a.startswith("--gapplication")]
    return application.run([sys.argv[0], *extra])
