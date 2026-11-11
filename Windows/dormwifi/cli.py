"""The command line: same engine as the window, more knobs."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import __version__, error_text, session_probe
from .activity_log import ActivityLog, LogEntry
from .login_engine import LoginEngine
from .network_probe import has_internet
from .portal_client import PortalClient
from .run_lock import RunLock
from .session_scheduler import SessionScheduler
from .settings import Settings, default_path, support_directory
from .status_board import StatusFile
from .wifi import WiFiJoiner
from .wifi import _CREATE_NO_WINDOW

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200

_SETTABLE_KEYS = (
    "wifi_name",
    "gateway_ip",
    "interval",
    "check_interval_secs",
    "fallback_wifi_name",
    "portal_fallback_url",
    "destination_url",
    "logout_url",
    "auto_connect",
    "auto_start",
    "check_wifi_every_time",
    "gemini_api_key",
    "ai_model",
    "username",
    "password",
)


class SettingsProblem(Exception):
    """A settings file, or a setting, that cannot be used as it stands."""


# MARK: - Console


def _render(entry: LogEntry) -> str:
    marker = {"info": "  ", "success": "OK", "warning": "! ", "failure": "X "}[entry.level]
    return f"{marker} {entry.formatted}"


class Console:
    """Prints log entries as they happen.

    Progress goes to stderr and results go to stdout, so `status` and `config
    show` can be piped somewhere without the running commentary mixed in.
    """

    @staticmethod
    def progress(text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    @staticmethod
    def line(text: str = "") -> None:
        print(text, flush=True)

    @staticmethod
    def attach(log: ActivityLog, verbose: bool) -> callable:
        def printer(entry: LogEntry) -> None:
            if verbose or entry.level != "info":
                Console.progress(_render(entry))

        return log.subscribe(printer)


# MARK: - Commands


def command_run(options: argparse.Namespace) -> int:
    settings = _load(options)
    _preflight(settings)

    log_path = (
        Path(options.log_file).expanduser()
        if options.log_file
        else support_directory() / "activity.log"
    )

    if options.menu:
        return _start_window_and_follow(options)

    # One watcher at a time. Two of them post to /login together the moment a
    # session lapses, and the router rejects the second for having too many
    # sessions open.
    lock = RunLock.acquire()
    if lock is None:
        raise SettingsProblem(
            "Another watcher is already running, either the window or another terminal. "
            "Stop that one first, or run with --menu to watch the one that is already going."
        )

    log = ActivityLog(file_path=log_path, truncate_file=True)
    unsubscribe = Console.attach(log, options.verbose)
    scheduler = SessionScheduler(
        settings, log, status_file=StatusFile()
    )

    def on_terminate(signum, frame) -> None:  # noqa: ARG001 - signal signature
        scheduler.stop()

    signal.signal(signal.SIGINT, on_terminate)
    signal.signal(signal.SIGTERM, on_terminate)

    Console.line("Watching. Press Control-C to stop.")
    try:
        scheduler.run()
    finally:
        unsubscribe()
        lock.release()

    return 0


def command_login(options: argparse.Namespace) -> int:
    settings = _load(options)
    _preflight(settings)

    log = ActivityLog()
    unsubscribe = Console.attach(log, options.verbose)

    try:
        WiFiJoiner(settings, log).join_configured_network()

        active = settings.resolving_gateway_waiting_up_to(20)
        if not active.gateway_ip:
            raise SettingsProblem(
                "This machine still has no network 20s after joining. Is the hotspot in range?"
            )

        result = LoginEngine(active, log).run()
        Console.line(result.summary)

        if result.is_connected:
            settings.touch_last_run()
            try:
                settings.save(options.settings_path)
            except OSError as error:
                Console.progress(f"Signed in, but the settings file was not saved: {error}")
            return 0
        return 1
    finally:
        unsubscribe()


def command_status(options: argparse.Namespace) -> int:
    settings = _load(options).resolving_gateway_waiting_up_to(5)
    if not settings.gateway_ip:
        raise SettingsProblem("This machine has no network connection at all.")

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        internet = pool.submit(has_internet, 2.0)
        session = pool.submit(session_probe.status, settings.gateway_ip, PortalClient())
        online = internet.result()
        answer = session.result()

    Console.line(f"Router:    {settings.gateway_ip}")
    Console.line(f"Internet:  {'up' if online else 'down'}")

    if answer.kind == "active" and answer.info is not None:
        info = answer.info
        Console.line("Session:   signed in")
        if info.ip_address:
            Console.line(f"  address:   {info.ip_address}")
        if info.mac_address:
            Console.line(f"  mac:       {info.mac_address}")
        if info.connected_for is not None:
            Console.line(f"  connected: {error_text.duration(info.connected_for)}")
        # The router's own countdown, not a guess from a local timer.
        if info.remaining is not None:
            Console.line(f"  expires:   in {error_text.duration(info.remaining)}")
    elif answer.kind == "expired":
        Console.line("Session:   signed out")
    elif answer.kind == "unknown":
        Console.line(f"Session:   signed out ({answer.why})")
    else:
        Console.line(f"Session:   router did not answer ({answer.why})")
        Console.line("           This machine may be on a different network.")

    return 0 if online else 1


def command_discover(options: argparse.Namespace) -> int:
    """Read the router's login page and say where the portal is, if anywhere."""
    from . import discover
    from .login_engine import LoginEngine

    settings = _load(options).resolving_gateway_waiting_up_to(5)
    if not settings.gateway_ip:
        raise SettingsProblem("This machine has no network connection at all.")

    console = Console
    client = PortalClient()
    try:
        engine = LoginEngine(settings, ActivityLog())
        # The page only: resolving the portal is the step being diagnosed.
        page = engine.fetch_login_page(client)
    except Exception as error:  # noqa: BLE001 - reported as a line, not a traceback
        console.line(f"Router:     {settings.gateway_ip}")
        console.line(f"Login page: could not be read: {error_text.short(error)}")
        if "did not answer" in error_text.short(error):
            console.line(
                "            This router stops answering /login while a session is open. If "
                "you are signed in, run 'dormwifi logout' first, or wait for the session to "
                "lapse."
            )
        return 1

    finding = discover.inspect(page.body, settings.gateway_ip)
    for line in discover.render(finding, settings.gateway_ip):
        console.line(line)
    return 0 if finding.looks_like_login_page else 1


def command_logout(options: argparse.Namespace) -> int:
    settings = _load(options).resolving_gateway()
    target = (
        settings.logout_url
        if settings.logout_url
        else f"http://{settings.gateway_ip}/logout"
    )

    try:
        response = PortalClient().get(target, timeout=5)
    except Exception as error:  # noqa: BLE001 - reported, then a failed exit code
        Console.line(f"Could not reach {target}: {error_text.short(error)}")
        return 1

    Console.line(f"Logged out (HTTP {response.status_code}).")
    return 0


def command_config(options: argparse.Namespace) -> int:
    if options.config_command == "path":
        Console.line(str(Path(options.settings_path or default_path()).expanduser()))
        return 0

    if options.config_command == "set":
        return _config_set(options)

    settings = _load(options)
    Console.line(json.dumps(settings.to_json(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def _config_set(options: argparse.Namespace) -> int:
    settings = _load(options)
    key, value = options.key, options.value

    if key not in _SETTABLE_KEYS:
        raise SettingsProblem(f"There is no setting called {key}.")

    if key in ("interval", "check_interval_secs"):
        try:
            setattr(settings, key, int(value))
        except ValueError:
            raise SettingsProblem(f"{key} needs a whole number, not {value}.") from None
    elif key in ("auto_connect", "auto_start", "check_wifi_every_time"):
        lowered = value.lower()
        if lowered in ("true", "yes", "1", "on"):
            setattr(settings, key, True)
        elif lowered in ("false", "no", "0", "off"):
            setattr(settings, key, False)
        else:
            raise SettingsProblem(f"{key} needs true or false, not {value}.")
    else:
        setattr(settings, key, value)

    settings.save(options.settings_path)

    # A secret is not echoed. This line goes to the terminal, into shell history,
    # and for the installed service into the journal.
    if key in ("gemini_api_key", "password"):
        Console.line(f"Set {key}.")
    else:
        Console.line(f"Set {key} to {value}.")
    return 0


# MARK: - Starting at login

_TASK_NAME = "nhannhtdormwifihack"
_LAUNCHER_NAME = "watch.vbs"


def _startup_directory() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def _entry_point() -> Path:
    """The launcher to put in the task: the one next to this package, or the PATH one."""
    import shutil

    sibling = Path(__file__).resolve().parent.parent / "bin" / "dormwifi.cmd"
    if sibling.is_file():
        return sibling
    found = shutil.which("dormwifi")
    return Path(found) if found else sibling


def _quote_argument(text: str) -> str:
    """Quote one path or argument for a Windows command line."""
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"' if (" " in text or '"' in text or not text) else text


def launcher_contents(executable: Path, arguments: list[str], launcher: Path) -> str:
    """The hidden VBScript that runs the watcher, and restarts it if it dies.

    ``WScript.Shell.Run`` with a window style of 0 starts a console program
    without a console. The loop is the Windows equivalent of systemd's
    ``Restart=always``. Deleting the script is how uninstall stops the loop.
    """
    command = " ".join(
        _quote_argument(part) for part in [str(executable), *arguments]
    )
    escaped_command = command.replace('"', '""')
    escaped_launcher = str(launcher).replace('"', '""')
    return "\r\n".join(
        [
            "' Starts the Dorm WiFi watcher without a console window, and restarts",
            "' it if it dies. Deleting this file is how uninstall stops the loop.",
            'Set shell = CreateObject("WScript.Shell")',
            'Set files = CreateObject("Scripting.FileSystemObject")',
            "Do",
            f'  If Not files.FileExists("{escaped_launcher}") Then Exit Do',
            f'  shell.Run "{escaped_command}", 0, True',
            "  WScript.Sleep 5000",
            "Loop",
            "",
        ]
    )


def _write_launcher(executable: Path, settings_path: str | None, verbose: bool) -> Path:
    arguments = ["run"]
    if verbose:
        arguments.append("--verbose")
    if settings_path:
        # The task starts in a system directory, so a relative path would mean
        # something else there than it did in the terminal.
        arguments.extend(
            ["--settings", str(Path(settings_path).expanduser().resolve())]
        )

    directory = support_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _LAUNCHER_NAME
    # WSH reads ANSI or UTF-16, not UTF-8 without a BOM. UTF-16 means a path
    # with a Vietnamese or otherwise non-ASCII user name still works.
    path.write_text(
        launcher_contents(executable, arguments, path), encoding="utf-16"
    )
    return path


def _is_elevated() -> bool:
    """Whether this terminal can create a logon task.

    A normal user cannot: `schtasks /Create /SC ONLOGON` answers "Access is
    denied" even with `/RL LIMITED`. The Startup folder needs no elevation, so
    that is the default on a normal terminal rather than an error followed by a
    fallback.
    """
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _report_shortcut() -> None:
    path = _create_desktop_shortcut()
    if path is not None:
        Console.line(f"Desktop launcher: {path}")


def command_install(options: argparse.Namespace) -> int:
    import subprocess

    executable = (
        Path(options.executable).expanduser() if options.executable else _entry_point()
    )
    if not executable.is_file():
        raise SettingsProblem(f"Not an executable file: {executable}")

    launcher = _write_launcher(executable, options.settings_path, options.verbose)

    if not _is_elevated():
        Console.line(f"Wrote {launcher}.")
        Console.progress(
            "Task Scheduler needs an elevated terminal; installing through the Startup folder "
            "instead."
        )
        return _install_into_startup_folder(launcher)

    task_command = f'wscript.exe "{launcher}"'
    completed = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            _TASK_NAME,
            "/TR",
            task_command,
            "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )

    Console.line(f"Wrote {launcher}.")
    if completed.returncode == 0:
        Console.line("Installed. The watcher starts at login and is running now.")
        Console.line(f"Logs:   {support_directory() / 'activity.log'}")
        if not options.verbose:
            Console.line(
                "The watcher logs warnings and failures only. Reinstall with --verbose to have "
                "it log every step."
            )
        _report_shortcut()
        return 0

    complaint = (completed.stderr or completed.stdout or "").strip()
    Console.progress(
        "Could not create the startup task"
        + (f": {complaint}" if complaint else f" (status {completed.returncode})")
    )
    return _install_into_startup_folder(launcher)


def _install_into_startup_folder(launcher: Path) -> int:
    startup = _startup_directory()
    if startup is None:
        raise SettingsProblem("Could not find the Startup folder.")
    startup.mkdir(parents=True, exist_ok=True)
    target = startup / "Dorm WiFi.vbs"
    target.write_bytes(launcher.read_bytes())
    Console.line(f"Wrote {target}.")
    Console.line("Installed through the Startup folder instead. The watcher starts at login.")
    _report_shortcut()
    return 0


def _stop_running_watcher() -> None:
    """Stop a watcher that the launcher started.

    ``schtasks /End`` ends the script, but the console program it was waiting for
    can outlive it. Uninstall is the one command that has to leave nothing
    running, so this looks for the one command line the installed watcher uses
    and stops it. The uninstall itself is not a match.
    """
    import subprocess

    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*-m dormwifi run*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        creationflags=_CREATE_NO_WINDOW,
    )


def command_uninstall(options: argparse.Namespace) -> int:
    import subprocess

    removed = False

    # Remove the scripts first. The launcher restarts the watcher every five
    # seconds until its own file is gone, so killing it before deleting the file
    # would only make it come back.
    paths = [support_directory() / _LAUNCHER_NAME]
    startup = _startup_directory()
    if startup is not None:
        paths.append(startup / "Dorm WiFi.vbs")
    for path in paths:
        if path.is_file():
            path.unlink()
            Console.line(f"Removed {path}.")
            removed = True

    subprocess.run(
        ["schtasks", "/End", "/TN", _TASK_NAME],
        check=False,
        capture_output=True,
        creationflags=_CREATE_NO_WINDOW,
    )
    completed = subprocess.run(
        ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
        check=False,
        capture_output=True,
        text=True,
        creationflags=_CREATE_NO_WINDOW,
    )
    if completed.returncode == 0:
        Console.line(f"Removed the startup task {_TASK_NAME}.")
        removed = True

    _stop_running_watcher()

    if _remove_desktop_shortcut():
        Console.line("Removed the Desktop launcher.")

    if not removed:
        Console.line("There was no startup task installed.")
    return 0


# MARK: - The desktop launcher

_SHORTCUT_NAME = "Dorm WiFi.lnk"


def _powershell_literal(text: object) -> str:
    """One PowerShell single-quoted string, with embedded quotes doubled."""
    return "'" + str(text).replace("'", "''") + "'"


def _run_powershell(script: str):
    import subprocess

    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )


def _create_desktop_shortcut() -> Path | None:
    """Put a Dorm WiFi icon on the Desktop that opens the window.

    The shortcut targets ``pythonw.exe`` directly, so double-clicking it never
    shows a console. ``WorkingDirectory`` is this checkout, which is what makes
    ``-m dormwifi`` resolvable without requiring PYTHONPATH to be set globally.
    """
    interpreter = _window_interpreter()
    root = Path(__file__).resolve().parent.parent
    icon = root / "data" / "dormwifi.ico"

    script = "\n".join(
        [
            "$desktop = [Environment]::GetFolderPath('Desktop')",
            f"$path = Join-Path $desktop {_powershell_literal(_SHORTCUT_NAME)}",
            "$shell = New-Object -ComObject WScript.Shell",
            "$link = $shell.CreateShortcut($path)",
            f"$link.TargetPath = {_powershell_literal(interpreter)}",
            "$link.Arguments = '-m dormwifi gui'",
            f"$link.WorkingDirectory = {_powershell_literal(root)}",
            "$link.Description = 'Sign in to the dorm WiFi and keep the session alive'",
        ]
    )
    if icon.is_file():
        script += f"\n$link.IconLocation = {_powershell_literal(icon)}"
    script += "\n$link.Save()\nWrite-Output $path"

    completed = _run_powershell(script)
    if completed.returncode != 0:
        Console.progress(
            "Could not create the Desktop shortcut: "
            + (completed.stderr or completed.stdout or "").strip()
        )
        return None
    return Path(completed.stdout.strip())


def _remove_desktop_shortcut() -> bool:
    script = "\n".join(
        [
            "$desktop = [Environment]::GetFolderPath('Desktop')",
            f"$path = Join-Path $desktop {_powershell_literal(_SHORTCUT_NAME)}",
            "if (Test-Path -LiteralPath $path) {",
            "  Remove-Item -LiteralPath $path -Force",
            "  Write-Output $path",
            "}",
        ]
    )
    completed = _run_powershell(script)
    return completed.returncode == 0 and bool(completed.stdout.strip())


def command_shortcut(options: argparse.Namespace) -> int:
    if options.remove:
        if _remove_desktop_shortcut():
            Console.line("Removed the Desktop launcher.")
        else:
            Console.line("There was no Desktop launcher.")
        return 0

    path = _create_desktop_shortcut()
    if path is None:
        return 1
    Console.line(f"Created {path}.")
    Console.line("Double-click it to open the window; no terminal is needed.")
    return 0


# MARK: - The window


def _signal_existing_window() -> bool:
    """Ask an already-running window to come back, instead of opening a second.

    The window may be hidden in the notification area, so the desktop shortcut
    would otherwise start a second process and a second tray icon. The tray's
    hidden window receives the same message Explorer sends on a double-click.
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if title.value == "DormWiFiTray":
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return False
    user32.PostMessageW(found[0], 0x8000 + 20, 0, 0x0203)
    return True


def command_gui(options: argparse.Namespace) -> int:
    if _signal_existing_window():
        Console.line("Showing the running Dorm WiFi window.")
        return 0
    return _start_window(options)


def _start_window(options: argparse.Namespace) -> int:
    try:
        from .app import main as window_main
    except ImportError as error:
        raise SettingsProblem(
            f"The window needs tkinter, which comes with Python ({error})"
        ) from None

    return window_main(
        [f"--settings={options.settings_path}"] if options.settings_path else []
    )


def _window_interpreter() -> str:
    """``pythonw.exe`` when it exists, so the window does not carry a console."""
    interpreter = Path(sys.executable)
    windowed = interpreter.with_name("pythonw.exe")
    return str(windowed) if windowed.is_file() else str(interpreter)


def _start_window_and_follow(options: argparse.Namespace) -> int:
    """Hand the watching to the window, and show its log here.

    The same shape as the macOS `--menu`: one watcher with two ways to look at
    it. Starting the window detached means this terminal can be closed (or
    Control-C'd) without taking the watcher with it.
    """
    import subprocess

    try:
        import tkinter  # noqa: F401 - presence check only
    except ImportError as error:
        raise SettingsProblem(
            f"The window needs tkinter, which comes with Python ({error})"
        ) from None

    arguments = [_window_interpreter(), "-m", "dormwifi", "gui"]
    if options.settings_path:
        arguments.extend(["--settings", str(options.settings_path)])

    Console.progress("Starting the window.")
    try:
        flags = 0
        start_new_session = False
        if os.name == "nt":
            flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
        else:  # pragma: no cover - the Windows build runs the branch above
            start_new_session = True
        subprocess.Popen(
            arguments,
            start_new_session=start_new_session,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise SettingsProblem(f"Could not start the window: {error}") from None

    log_path = (
        Path(options.log_file).expanduser()
        if options.log_file
        else support_directory() / "activity.log"
    )
    Console.line("Window started. Following its log; Control-C stops watching here.")
    Console.line("The window keeps running after you stop this, and quits from its own menu.")

    try:
        _follow(log_path)
    except KeyboardInterrupt:
        pass
    return 0


# MARK: - Shared


def _load(options: argparse.Namespace) -> Settings:
    try:
        return Settings.load(options.settings_path)
    except (OSError, ValueError) as error:
        raise SettingsProblem(f"Cannot read the settings file: {error}") from None


def _preflight(settings: Settings) -> None:
    """Catch the settings that make a login impossible before any request goes out.

    `gateway_ip` is deliberately allowed to be empty: that means "ask this
    machine which router it is on", which is the normal setting.
    """
    if settings.interval < 1:
        raise SettingsProblem("interval has to be at least 1 minute.")


# MARK: - The parser


def _shared_options() -> argparse.ArgumentParser:
    """The options every command takes, in either position.

    `default=SUPPRESS` so a subcommand does not write its own defaults over the
    value the top-level parser already read: `dormwifi -v run` and
    `dormwifi run -v` both mean the same thing, and with plain defaults argparse
    quietly turned the first into verbose=False.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--settings",
        "-s",
        dest="settings_path",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Path to the settings file.",
    )
    parent.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print every step, not just the outcome.",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    shared = _shared_options()
    parser = argparse.ArgumentParser(
        prog="dormwifi",
        description="Signs in to a captive-portal WiFi and keeps the session alive.",
        parents=[shared],
        epilog=(
            "With no subcommand this runs the watcher, which signs in and then keeps renewing "
            f"the session before it can expire. Settings live in {default_path()}."
        ),
    )
    parser.add_argument("--version", action="version", version=f"dormwifi {__version__}")
    # Deliberately not `verbose` or `settings_path` here: `set_defaults` rewrites
    # the matching action objects, and those objects are shared with every
    # subparser through `parents`, so a subparser would then write its own
    # default over the value already parsed. `parse()` seeds those two instead.
    parser.set_defaults(handler=command_run, menu=False, log_file=None)

    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser(
        "run",
        parents=[shared],
        help="Sign in, then keep the session alive until stopped.",
    )
    run.add_argument("--log-file", metavar="PATH", help="Mirror the log to this file.")
    run.add_argument(
        "--menu",
        "--gui",
        "-g",
        dest="menu",
        action="store_true",
        help="Show the window instead of watching here, and follow its log in this terminal.",
    )
    run.set_defaults(handler=command_run)

    subparsers.add_parser(
        "login", parents=[shared], help="Sign in once and exit."
    ).set_defaults(handler=command_login)

    subparsers.add_parser(
        "status", parents=[shared], help="Report whether the session and the internet are up."
    ).set_defaults(handler=command_status)

    subparsers.add_parser(
        "discover",
        parents=[shared],
        help="Read the router's login page and look for the portal's address.",
    ).set_defaults(handler=command_discover)

    subparsers.add_parser(
        "logout", parents=[shared], help="End the current session."
    ).set_defaults(handler=command_logout)

    config = subparsers.add_parser(
        "config", parents=[shared], help="Show or change the settings file."
    )
    config.set_defaults(handler=command_config, config_command="show")
    config_subparsers = config.add_subparsers(dest="config_command")
    config_subparsers.add_parser(
        "show", parents=[shared], help="Print the current settings."
    ).set_defaults(handler=command_config, config_command="show")
    config_subparsers.add_parser(
        "path", parents=[shared], help="Print where the settings file lives."
    ).set_defaults(handler=command_config, config_command="path")
    config_set = config_subparsers.add_parser(
        "set", parents=[shared], help="Change one setting."
    )
    config_set.add_argument("key", metavar="KEY", help=", ".join(_SETTABLE_KEYS))
    config_set.add_argument("value", metavar="VALUE")
    config_set.set_defaults(handler=command_config, config_command="set")

    install = subparsers.add_parser(
        "install", parents=[shared], help="Start the watcher at login, and keep it running."
    )
    install.add_argument(
        "--executable", metavar="PATH", help="Path to the launcher the startup task should run."
    )
    install.set_defaults(handler=command_install)

    subparsers.add_parser(
        "uninstall", parents=[shared], help="Stop the watcher and remove it from login."
    ).set_defaults(handler=command_uninstall)

    subparsers.add_parser("gui", parents=[shared], help="Open the window.").set_defaults(
        handler=command_gui
    )

    shortcut = subparsers.add_parser(
        "shortcut",
        parents=[shared],
        help="Put a Dorm WiFi launcher on the Desktop, or remove it.",
    )
    shortcut.add_argument(
        "--remove",
        action="store_true",
        help="Remove the Desktop launcher instead of creating it.",
    )
    shortcut.set_defaults(handler=command_shortcut)

    return parser


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line, with the shared options seeded once.

    The seed is what lets `-v` sit on either side of the subcommand: the value
    is already in the namespace, so a subparser with `SUPPRESS` defaults leaves
    it alone instead of replacing it.
    """
    parser = build_parser()
    return parser.parse_args(
        argv, namespace=argparse.Namespace(verbose=False, settings_path=None)
    )


def main(argv: list[str] | None = None) -> int:
    options = parse(argv)

    try:
        return int(options.handler(options) or 0)
    except SettingsProblem as problem:
        Console.progress(f"X  {problem}")
        return 2
    except KeyboardInterrupt:
        Console.progress("Stopped.")
        return 130
