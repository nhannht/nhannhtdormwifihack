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
        self._build()

        self._unsubscribe_log = self._log.subscribe(self._on_log_entry)
        self._unsubscribe_board = self._board.subscribe(self._on_status)

        self._load_history()
        self._configured_wifi = self._settings_wifi_name()
        self._network_value.set_text(self._configured_wifi or "—")
        if autostart:
            self._start_watcher()
        else:
            # Built for a test, or opened to look at: no lock, no watcher.
            self._populate_hotspots(self._settings_wifi_name())
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

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._toggle_button = Gtk.Button(label="Pause auto-connect")
        self._toggle_button.connect("clicked", self._on_toggle_watcher)
        buttons.append(self._toggle_button)

        self._sign_in_button = Gtk.Button(label="Sign in now")
        self._sign_in_button.connect("clicked", self._on_sign_in_now)
        buttons.append(self._sign_in_button)

        self._logout_button = Gtk.Button(label="Log out")
        self._logout_button.connect("clicked", self._on_logout)
        buttons.append(self._logout_button)

        outer.append(buttons)

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
            self._toggle_button.set_sensitive(False)
            self._sign_in_button.set_sensitive(False)
            self._logout_button.set_sensitive(False)
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
        self._say("Watching. Signing in if the session has lapsed.", "info")
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

        if not self._observing:
            if status.connection == STOPPING:
                self._toggle_button.set_label("Stopping…")
            elif status.connection == STOPPED:
                self._toggle_button.set_label("Start auto-connect")
            else:
                self._toggle_button.set_label("Pause auto-connect")

        return False

    def _tick(self) -> bool:
        """Redraw the headline each second so the countdown moves as you read it."""
        if not self._observing:
            self._headline.set_text(self._status.headline)
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
        if current and current not in choices:
            # The name in the settings file is the one that counts, so it has to
            # be visible even when the machine has not saved it. It drops off
            # when the hotspot is renamed, which is the very moment someone opens
            # this wondering why nothing is connecting.
            choices.insert(0, f"{current} (not saved)")

        self._hotspot_suppressed = True
        self._hotspot_dropdown.set_model(Gtk.StringList.new(choices or ["No saved networks"]))
        selected = 0
        if current:
            for index, name in enumerate(choices):
                if name == current:
                    selected = index
                    break
        self._hotspot_dropdown.set_selected(selected)
        self._hotspot_suppressed = False
        return False

    # MARK: - Actions

    def _on_toggle_watcher(self, _button: Gtk.Button) -> None:
        if self._scheduler is None:
            self._start_watcher()
        else:
            self._stop_watcher()

    def _on_sign_in_now(self, _button: Gtk.Button) -> None:
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
            result = LoginEngine(settings, self._log).run()
            GLib.idle_add(self._finish_sign_in, result.summary, result.is_connected)

        threading.Thread(target=work, name="sign-in", daemon=True).start()

    def _finish_sign_in(self, summary: str, connected: bool) -> bool:
        self._progress_done()
        self._say(summary, "ok" if connected else "error", persistent=not connected)
        return False

    def _progress_done(self) -> None:
        self._sign_in_button.set_sensitive(True)
        self._sign_in_button.set_label("Sign in now")

    def _on_logout(self, _button: Gtk.Button) -> None:
        try:
            settings = Settings.load(self._settings_path).resolving_gateway()
        except (OSError, ValueError) as error:
            self._say(f"Cannot read the settings file: {error_text.short(error)}", "error")
            return

        self._logout_button.set_sensitive(False)
        self._say("Ending the session…", "info", persistent=True)

        def work() -> None:
            target = settings.logout_url or f"http://{settings.gateway_ip}/logout"
            try:
                response = PortalClient().get(target, timeout=5)
                message = f"Logged out (HTTP {response.status_code})."
                ok = response.is_success
            except Exception as error:  # noqa: BLE001 - reported, never raised
                message = f"Could not reach {target}: {error_text.short(error)}"
                ok = False
            GLib.idle_add(self._finish_logout, message, ok)

        threading.Thread(target=work, name="logout", daemon=True).start()

    def _finish_logout(self, message: str, ok: bool) -> bool:
        self._logout_button.set_sensitive(True)
        self._say(message, "ok" if ok else "error", persistent=not ok)
        return False

    def _on_hotspot_selected(self, dropdown: Gtk.DropDown, _param) -> None:
        if self._hotspot_suppressed:
            return

        selected = dropdown.get_selected_item()
        if selected is None:
            return
        name = selected.get_string()
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

    # MARK: - Shutdown

    def shutdown(self) -> None:
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
    def __init__(self, settings_path: Path | None) -> None:
        super().__init__(application_id=APPLICATION_ID, flags=0)
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
            self._window.connect("close-request", self._on_close)
        self._window.present()

    def do_shutdown(self) -> None:
        if self._window is not None:
            self._window.shutdown()
        Gtk.Application.do_shutdown(self)

    def _on_close(self, *_args) -> bool:
        # Closing the window stops the watcher: the user asked for it, and
        # leaving a hidden process holding the singleton lock would make the next
        # `dormwifi run` fail for no visible reason.
        self.quit()
        return False

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dormwifi gui", description="The Dorm WiFi window.")
    parser.add_argument("--settings", "-s", metavar="PATH", help="Path to the settings file.")
    options, remaining = parser.parse_known_args(argv)

    application = DormWiFiApplication(
        Path(options.settings).expanduser() if options.settings else None
    )
    extra = [a for a in (remaining or []) if not a.startswith("--gapplication")]
    return application.run([sys.argv[0], *extra])
