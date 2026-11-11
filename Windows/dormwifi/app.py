"""The window, drawn with Tkinter.

Tkinter ships with Python, so the window needs nothing installed and nothing
built. That is the same reason the Linux port uses GTK 4: the engine is the
interesting part, and the window is a view of it.

The watcher runs on a thread of its own and pushes its state and its log to a
``queue.Queue``. Tkinter is not thread-safe, so nothing off that thread touches a
widget: the main loop drains the queue every half second, reads the status board,
and redraws. That is also what makes the countdown move while the watcher sleeps.
"""

from __future__ import annotations

import argparse
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import error_text
from .activity_log import ActivityLog, LogEntry, entries_in_file
from .login_engine import LoginEngine
from .run_lock import RunLock
from .session_scheduler import SessionScheduler
from .settings import Settings, default_path, support_directory
from .status_board import (
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
from .tray import CMD_LOG, CMD_QUIT, CMD_SHOW, CMD_SIGNIN, CMD_TOGGLE, TrayIcon
from .wifi import saved_networks

TITLE = "Dorm WiFi"

#: What the picker says when the settings file names no hotspot. Without it the
#: list silently preselected whichever saved network sorted first, which reads
#: as "it is going to use that one" when it is going to use none.
_NO_HOTSPOT = "— no hotspot chosen —"

_STATE_COLOURS = {
    SIGNED_IN: "#2e7d32",
    SIGNING_IN: "#1565c0",
    STARTING: "#1565c0",
    WRONG_NETWORK: "#e65100",
    ON_FALLBACK: "#1565c0",
    STOPPED: "#616161",
    STOPPING: "#b71c1c",
}

_LEVEL_COLOURS = {
    "info": "#37474f",
    "success": "#2e7d32",
    "warning": "#e65100",
    "failure": "#b71c1c",
}

_MESSAGE_COLOURS = {
    "ok": ("#e6f4ea", "#1b5e20"),
    "error": ("#fdecea", "#b71c1c"),
    "info": ("#e8f0fe", "#0d47a1"),
}


class DormWiFiWindow:
    """The one window: state at the top, actions in the middle, log at the bottom."""

    def __init__(
        self,
        root: tk.Tk,
        settings_path: Path | None = None,
        autostart: bool = True,
        tray: bool = True,
    ) -> None:
        self.root = root
        self.settings_path = settings_path
        self.board = StatusBoard()
        self.status_file = StatusFile()
        self.log_path = support_directory() / "activity.log"
        self.log = ActivityLog(file_path=self.log_path, truncate_file=False)

        self._events: queue.Queue = queue.Queue()
        self._status = WatcherStatus()
        self._scheduler: SessionScheduler | None = None
        self._worker: threading.Thread | None = None
        self._lock: RunLock | None = None
        self._observing = False
        self._pending_restart = False
        self._message_until: float | None = None
        self._configured_wifi = ""
        self._log_offset = 0
        self._hotspot_suppressed = False
        self._signing_in = False
        self._tray = None
        self._told_about_hiding = False
        self._shut_down = False
        self.follow = tk.BooleanVar(value=True)

        self._build()
        if tray:
            # The Tk toplevel has a placeholder handle until it is mapped, and
            # a tray icon attached to that handle never receives a message.
            # Realize the window first, then attach the icon to the final one.
            self.root.update_idletasks()
            self.root.update()
            self._start_tray()
        self.log.subscribe(lambda entry: self._events.put(("log", entry)))
        self.board.subscribe(lambda status: self._events.put(("status", status)))

        self._load_history()

        if autostart:
            self._start_control()
        else:
            self._configured_wifi = self._settings_wifi_name()
            self._set_network_value()
            self._populate_hotspots(self._configured_wifi)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._tick)

    # MARK: - Construction

    def _build(self) -> None:
        self.root.title(TITLE)
        self.root.geometry("940x700")
        self.root.minsize(760, 560)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=TITLE, font=("Segoe UI", 20, "bold")).pack(side="left")
        for label, command in (
            ("Quit", self._quit),
            ("Log", self._on_open_log),
            ("Settings", self._on_open_settings),
        ):
            ttk.Button(header, text=label, command=command).pack(
                side="right", padx=(6, 0)
            )

        state_row = ttk.Frame(outer)
        state_row.pack(fill="x", pady=(14, 4))
        self._state_dot = tk.Label(
            state_row, text="●", fg=_STATE_COLOURS[STARTING], font=("Segoe UI", 18)
        )
        self._state_dot.pack(side="left", padx=(0, 8))
        self._headline = ttk.Label(
            state_row, text="Starting", font=("Segoe UI", 17, "bold")
        )
        self._headline.pack(side="left")

        grid = ttk.Frame(outer)
        grid.pack(fill="x", pady=(6, 0))
        self._network_value = self._info_row(grid, 0, "Network")
        self._router_value = self._info_row(grid, 1, "Router")
        self._internet_value = self._info_row(grid, 2, "Internet")
        self._safety_value = self._info_row(grid, 3, "Safety net")

        self._message_holder = ttk.Frame(outer)
        self._message_holder.pack(fill="x")
        self._message = tk.Label(
            self._message_holder,
            text="",
            anchor="w",
            justify="left",
            padx=10,
            pady=8,
            wraplength=760,
        )
        # Packed into place only when there is something to say.

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 2))
        self._toggle_button = ttk.Button(
            actions, text="Pause auto-connect", command=self._toggle_watcher
        )
        self._toggle_button.pack(side="left")
        self._sign_in_button = ttk.Button(
            actions, text="Sign in now", command=self._sign_in_now
        )
        self._sign_in_button.pack(side="left", padx=(8, 0))

        self._hint = ttk.Label(
            outer,
            text="Pause stops the automatic sign-in. Sign in now tries once, immediately.",
            foreground="#546e7a",
            wraplength=820,
        )
        self._hint.pack(fill="x", pady=(2, 10))

        hotspot = ttk.Frame(outer)
        hotspot.pack(fill="x", pady=(0, 10))
        ttk.Label(hotspot, text="Hotspot network", width=16).pack(side="left")
        self._hotspot = ttk.Combobox(hotspot, state="readonly", values=[])
        self._hotspot.pack(side="left", fill="x", expand=True)
        self._hotspot.bind("<<ComboboxSelected>>", self._on_hotspot_selected)
        ttk.Button(hotspot, text="Refresh", command=self._on_refresh_networks).pack(
            side="left", padx=(8, 0)
        )

        frame = ttk.LabelFrame(outer, text="Log", padding=8)
        frame.pack(fill="both", expand=True)

        log_area = ttk.Frame(frame)
        log_area.pack(fill="both", expand=True)
        self._log_view = tk.Text(
            log_area,
            height=12,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            borderwidth=1,
            relief="solid",
        )
        scrollbar = ttk.Scrollbar(
            log_area, orient="vertical", command=self._log_view.yview
        )
        self._log_view.configure(yscrollcommand=scrollbar.set)
        self._log_view.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for level, colour in _LEVEL_COLOURS.items():
            self._log_view.tag_configure(level, foreground=colour)

        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(
            footer, text="Follow new lines", variable=self.follow
        ).pack(side="left")
        ttk.Button(footer, text="Open log file", command=self._on_open_log).pack(
            side="right"
        )
        self._count_label = ttk.Label(footer, text="0 lines this run")
        self._count_label.pack(side="right", padx=(0, 10))

        self._refresh_safety_net()

    def _info_row(self, parent: ttk.Frame, row: int, title: str) -> ttk.Label:
        ttk.Label(parent, text=title, width=16, foreground="#546e7a").grid(
            row=row, column=0, sticky="w", pady=1
        )
        value = ttk.Label(parent, text="—")
        value.grid(row=row, column=1, sticky="w", pady=1)
        return value

    # MARK: - The watcher

    def _settings_wifi_name(self) -> str:
        try:
            return Settings.load(self.settings_path).wifi_name
        except (OSError, ValueError):
            return ""

    def _start_control(self) -> None:
        """Run the watcher here, or follow one that is already running."""
        try:
            settings = Settings.load(self.settings_path)
        except (OSError, ValueError) as error:
            self._say(
                f"Cannot read the settings file: {error_text.short(error)}",
                "error",
                persistent=True,
            )
            return

        if self._lock is None:
            self._lock = RunLock.acquire()

        if self._lock is None:
            # A task or a terminal already holds the singleton lock. Two
            # watchers would both post to /login the moment a session lapses,
            # and the router rejects the second. So this window becomes a
            # viewer instead of fighting for it.
            self._observing = True
            self._hint.configure(
                text=(
                    "Another watcher is running — a terminal, or the installed task — so this "
                    "window only shows its status. Stop that one to control the watcher from here."
                )
            )
            self._toggle_button.state(["disabled"])
            self._sign_in_button.state(["disabled"])
            self._say(
                "Another watcher is already running, so this window is only watching it. "
                f"Its status is read from {self.status_file.path}.",
                "info",
                persistent=True,
            )
            return

        self._configured_wifi = settings.wifi_name
        self._set_network_value()
        self._refresh_safety_net()
        self._launch_scheduler(settings)
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

    def _launch_scheduler(self, settings: Settings) -> None:
        scheduler = SessionScheduler(
            settings,
            self.log,
            board=self.board,
            status_file=self.status_file,
        )
        self._scheduler = scheduler

        def work() -> None:
            scheduler.run()

        self._worker = threading.Thread(target=work, name="watcher", daemon=True)
        self._worker.start()
        self._toggle_button.configure(text="Pause auto-connect")
        self._toggle_button.state(["!disabled"])

    def _toggle_watcher(self) -> None:
        if self._observing:
            self._say(
                "Another watcher is running, so this window cannot pause it.",
                "error",
                persistent=True,
            )
            return

        if self._worker is not None and self._worker.is_alive():
            if self._scheduler is not None:
                self._scheduler.stop()
            self._toggle_button.state(["disabled"])
            self._toggle_button.configure(text="Stopping…")
            self._say(
                "Stopping, waiting for the last step to finish.",
                "info",
                persistent=True,
            )
            return

        self._start_watcher()

    def _start_watcher(self) -> None:
        try:
            settings = Settings.load(self.settings_path)
        except (OSError, ValueError) as error:
            self._say(
                f"Cannot read the settings file: {error_text.short(error)}",
                "error",
                persistent=True,
            )
            return
        self._configured_wifi = settings.wifi_name
        self._set_network_value()
        self._refresh_safety_net()
        self._launch_scheduler(settings)
        self._say("Watching. Signing in if the session has lapsed.", "info")
        self._populate_hotspots(settings.wifi_name)

    def _reap_worker(self) -> None:
        if self._worker is not None and not self._worker.is_alive():
            self._worker = None
            if self._scheduler is not None:
                self._scheduler = None
            if not self._observing:
                self._toggle_button.state(["!disabled"])
                self._toggle_button.configure(text="Start auto-connect")
                if not self._pending_restart:
                    self._say(
                        "Stopped. Nothing is keeping the session alive.",
                        "info",
                        persistent=True,
                    )

        if self._pending_restart:
            self._pending_restart = False
            self._start_watcher()

    # MARK: - Feedback

    def _say(self, text: str, kind: str, *, persistent: bool = False) -> None:
        """Answer whatever was just pressed, in words.

        Every control in this window calls this. A press that changes nothing
        visible reads as broken, and the user has no way to tell the difference.
        """
        background, foreground = _MESSAGE_COLOURS.get(kind, _MESSAGE_COLOURS["info"])
        self._message.configure(text=text, bg=background, fg=foreground)
        if not self._message.winfo_manager():
            self._message.pack(fill="x", pady=(10, 0))
        self._message_until = None if persistent else time.time() + 9

    def _clear_message(self) -> None:
        if self._message_until is not None and time.time() >= self._message_until:
            self._message_until = None
            self._message.pack_forget()

    # MARK: - State

    def _apply_status(self, status: WatcherStatus) -> None:
        self._status = status
        self._state_dot.configure(
            fg=_STATE_COLOURS.get(status.connection, _STATE_COLOURS[STARTING])
        )
        self._headline.configure(text=status.headline)
        self._set_network_value()
        self._router_value.configure(text=status.router_ip or "—")
        self._internet_value.configure(text="up" if status.has_internet else "down")

    def _set_network_value(self) -> None:
        network = self._status.network or self._configured_wifi or "—"
        self._network_value.configure(text=network)

    def _refresh_safety_net(self) -> None:
        try:
            settings = Settings.load(self.settings_path)
        except (OSError, ValueError):
            self._safety_value.configure(text="—")
            return
        self._safety_value.configure(text=settings.fallback_wifi_name or "off")

    def _tick(self) -> None:
        if self._shut_down:
            return
        self._drain_events()
        if self._tray is not None:
            for command in self._tray.poll():
                self._tray_command(command)
                if self._shut_down:
                    return
        self._reap_worker()
        if self._observing:
            self._load_observer_status()
            self._tail_log_file()
        self._headline.configure(text=self._status.headline)
        self._set_network_value()
        self._update_tray()
        self._clear_message()
        self.root.after(500, self._tick)

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                return
            if kind == "status":
                self._apply_status(payload)
            elif kind == "log":
                self._append_entry(payload)
            elif kind == "networks":
                self._apply_hotspots(payload)
            elif kind == "signin":
                summary, connected = payload
                self._finish_sign_in(summary, connected)

    # MARK: - Observer mode

    def _load_observer_status(self) -> None:
        status = self.status_file.read()
        if status is not None:
            self._apply_status(status)

    # MARK: - Log

    def _load_history(self) -> None:
        for entry in entries_in_file(self.log_path, 150):
            self._append_entry(entry, scroll=False)
        try:
            self._log_offset = self.log_path.stat().st_size
        except OSError:
            self._log_offset = 0

    def _append_entry(self, entry: LogEntry, scroll: bool = True) -> None:
        self._log_view.configure(state="normal")
        self._log_view.insert("end", entry.formatted + "\n", entry.level)
        self._log_view.configure(state="disabled")
        lines = int(self._log_view.index("end-1c").split(".")[0]) - 1
        self._count_label.configure(text=f"{max(lines, 0)} lines this run")
        if scroll and self.follow.get():
            self._log_view.see("end")

    def _tail_log_file(self) -> None:
        """Read whatever another process appended to the log since last time."""
        try:
            size = self.log_path.stat().st_size
        except OSError:
            return
        if size < self._log_offset:
            self._log_offset = 0
        if size == self._log_offset:
            return
        try:
            with self.log_path.open("rb") as stream:
                stream.seek(self._log_offset)
                chunk = stream.read()
        except OSError:
            return
        self._log_offset = size
        for line in chunk.decode("utf-8", "replace").splitlines():
            entry = LogEntry.parse_file_line(line)
            if entry is not None:
                self._append_entry(entry, scroll=False)
        if self.follow.get():
            self._log_view.see("end")

    # MARK: - Networks

    def _populate_hotspots(self, current: str) -> None:
        def work() -> None:
            self._events.put(("networks", (saved_networks(), current)))

        threading.Thread(target=work, name="networks", daemon=True).start()

    def _apply_hotspots(self, payload: tuple[list[str], str]) -> None:
        networks, current = payload
        choices = list(networks)

        if not current:
            # Nothing is configured, and the list must say so. Preselecting a
            # saved network here made "no hotspot chosen" look like "will use
            # the first one", which is what it did instead: nothing.
            choices.insert(0, _NO_HOTSPOT)
            selected = 0
        elif current not in choices:
            # The name in the settings file is the one that counts, so it has to
            # be visible even when the machine has not saved it.
            choices.insert(0, f"{current} (not saved)")
            selected = 0
        else:
            selected = choices.index(current)

        self._hotspot_suppressed = True
        self._hotspot.configure(values=choices or ["Windows has no saved networks"])
        self._hotspot.current(selected)
        self._hotspot_suppressed = False

    def _on_hotspot_selected(self, _event=None) -> None:
        if self._hotspot_suppressed:
            return

        name = self._hotspot.get()
        if not name:
            return
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
                f"{name.removesuffix(' (not saved)')} is not saved by Windows, so it cannot "
                "be rejoined from here. Join it by hand once, with 'Connect automatically' "
                "ticked.",
                "error",
                persistent=True,
            )
            return

        try:
            settings = Settings.load(self.settings_path)
            if settings.wifi_name == name:
                self._say(f"Already watching {name}.", "info")
                return
            settings.wifi_name = name
            settings.save(self.settings_path)
            self._configured_wifi = name
        except (OSError, ValueError) as error:
            self._say(f"Could not record the choice: {error_text.short(error)}", "error")
            return

        self._set_network_value()
        self._refresh_safety_net()
        self._say(f"Hotspot set to {name}. Restarting the watcher.", "ok")

        if self._worker is not None and self._worker.is_alive():
            self._pending_restart = True
            if self._scheduler is not None:
                self._scheduler.stop()
            self._toggle_button.state(["disabled"])
            self._toggle_button.configure(text="Stopping…")

    def _on_refresh_networks(self) -> None:
        self._say("Asking Windows for its saved networks…", "info")
        self._populate_hotspots(self._settings_wifi_name())

    # MARK: - Actions

    def _sign_in_now(self) -> None:
        if self._observing or self._signing_in:
            return
        try:
            settings = Settings.load(self.settings_path).resolving_gateway_waiting_up_to(10)
        except (OSError, ValueError) as error:
            self._say(
                f"Cannot read the settings file: {error_text.short(error)}", "error"
            )
            return

        if not settings.gateway_ip:
            self._say(
                "No router address: this machine is not on a network. Join one first.",
                "error",
                persistent=True,
            )
            return

        self._signing_in = True
        self._sign_in_button.state(["disabled"])
        self._sign_in_button.configure(text="Signing in…")
        self._say(f"Signing in through {settings.gateway_ip}…", "info", persistent=True)

        def work() -> None:
            # The button is disabled while this runs, and only the event below
            # enables it again. A sign-in that raised would therefore leave a
            # button that never comes back, which reads as a broken window long
            # after the real failure is forgotten.
            try:
                result = LoginEngine(settings, self.log).run()
            except Exception as error:  # noqa: BLE001 - always answer the button
                self._events.put(
                    (
                        "signin",
                        (f"Sign-in stopped unexpectedly: {error_text.short(error)}", False),
                    )
                )
                return
            self._events.put(("signin", (result.summary, result.is_connected)))

        threading.Thread(target=work, name="sign-in", daemon=True).start()

    def _finish_sign_in(self, summary: str, connected: bool) -> None:
        self._signing_in = False
        self._sign_in_button.state(["!disabled"])
        self._sign_in_button.configure(text="Sign in now")
        self._say(summary, "ok" if connected else "error", persistent=not connected)

    # MARK: - The tray icon

    def _start_tray(self) -> None:
        icon = Path(__file__).resolve().parent.parent / "data" / "dormwifi.ico"
        tray = TrayIcon(icon, "Dorm WiFi")
        if tray.start():
            self._tray = tray
            self._update_tray()
        else:
            self._tray = None
            self.log.info(
                "No tray icon: closing the window will quit instead of hiding it."
            )

    def _tray_menu(self) -> list[tuple[int, str, bool]]:
        running = self._worker is not None and self._worker.is_alive()
        stopping = (
            running and self._scheduler is not None and self._scheduler.stopped
        )
        if self._observing:
            toggle, toggle_enabled, signin_enabled = (
                "Pause auto-connect",
                False,
                False,
            )
        elif stopping:
            toggle, toggle_enabled, signin_enabled = "Stopping…", False, False
        elif running:
            toggle, toggle_enabled, signin_enabled = (
                "Pause auto-connect",
                True,
                True,
            )
        else:
            toggle, toggle_enabled, signin_enabled = (
                "Start auto-connect",
                True,
                True,
            )

        return [
            (CMD_SHOW, "Show Dorm WiFi", True),
            (CMD_TOGGLE, toggle, toggle_enabled),
            (CMD_SIGNIN, "Sign in now", signin_enabled),
            (CMD_LOG, "Open log file", True),
            (CMD_QUIT, "Quit", True),
        ]

    def _tray_command(self, command: int) -> None:
        if command == CMD_SHOW:
            self._show_window()
        elif command == CMD_TOGGLE:
            self._toggle_watcher()
        elif command == CMD_SIGNIN:
            self._sign_in_now()
        elif command == CMD_LOG:
            self._on_open_log()
        elif command == CMD_QUIT:
            self._quit()

    def _show_window(self) -> None:
        self.log.info("Showing the window from the tray.")
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _update_tray(self) -> None:
        if self._tray is not None and self._tray.available:
            self._tray.set_menu(self._tray_menu())
            self._tray.update(f"Dorm WiFi - {self._status.headline}")

    # MARK: - Files

    def _on_open_log(self) -> None:
        self._open_path(self.log_path)

    def _on_open_settings(self) -> None:
        path = Path(self.settings_path) if self.settings_path else default_path()
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
            os.startfile(str(path))  # type: ignore[attr-defined]
            self._say(f"Opened {path}.", "ok")
        except OSError as error:
            self._say(f"Could not open {path}: {error}", "error", persistent=True)

    # MARK: - Closing

    def _on_close(self) -> None:
        """Hide, leaving the watcher running, when there is a tray icon.

        Without an icon there would be no way back to the window, so closing an
        icon-less window still quits.
        """
        if self._tray is not None and self._tray.available:
            self.root.withdraw()
            if not self._told_about_hiding:
                self._told_about_hiding = True
                network = self._configured_wifi or "the hotspot"
                self._tray.notify(
                    "Dorm WiFi is still watching",
                    f"Keeping {network} signed in. Double-click the tray icon to reopen, "
                    "or use Quit there.",
                )
            return
        self._quit()

    def _quit(self) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        if self._tray is not None:
            self._tray.remove()
            self._tray = None
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._worker is not None:
            self._worker.join(timeout=5)
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dormwifi gui", description="The Dorm WiFi window.")
    parser.add_argument("--settings", "-s", metavar="PATH", help="Path to the settings file.")
    options, _remaining = parser.parse_known_args(argv)

    root = tk.Tk()
    window = DormWiFiWindow(
        root,
        Path(options.settings).expanduser() if options.settings else None,
    )
    try:
        root.mainloop()
    finally:
        # `mainloop` returns when the window is destroyed; make sure the tray,
        # the worker and the lock are gone even if a test/embedding never called
        # the close handler.
        window.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
