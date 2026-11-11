"""The notification-area icon, in plain ``ctypes``.

No ``pystray``, no ``pip install``. A tiny hidden Win32 window owns the icon and
runs its own message loop on a thread; mouse events are turned into command ids
on a ``queue.Queue``. The Tk loop polls that queue and does the actual work, so
no Windows callback ever enters Tkinter or Tcl.

Closing the window withdraws it and leaves this icon in place. Double-clicking
the icon (or choosing Show) brings the window back; Quit actually stops the
watcher.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

WM_APP = 0x8000
WM_TRAY = WM_APP + 20
WM_NULL = 0x0000
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001

MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_SEPARATOR = 0x00000800

TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

CMD_SHOW = 1
CMD_TOGGLE = 2
CMD_SIGNIN = 3
CMD_LOG = 4
CMD_QUIT = 5

_active = hasattr(ctypes, "windll")

if _active:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", _GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    _user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _user32.CreatePopupMenu.restype = wintypes.HANDLE
    _user32.AppendMenuW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_size_t,
        wintypes.LPCWSTR,
    ]
    _user32.AppendMenuW.restype = wintypes.BOOL
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
    ]
    _user32.TrackPopupMenu.restype = wintypes.UINT
    _user32.DestroyMenu.argtypes = [wintypes.HANDLE]
    _user32.DestroyMenu.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    _shell32.Shell_NotifyIconW.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(_NOTIFYICONDATAW),
    ]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    try:
        _TASKBAR_CREATED = int(_user32.RegisterWindowMessageW("TaskbarCreated"))
    except Exception:  # pragma: no cover - only on a broken session
        _TASKBAR_CREATED = 0
else:  # pragma: no cover - the tests run on Windows
    _user32 = None
    _shell32 = None
    _kernel32 = None
    _NOTIFYICONDATAW = None
    _WNDPROC = None
    _WNDCLASSW = None
    _TASKBAR_CREATED = 0


class TrayIcon:
    """One icon in the notification area, owned by a hidden Win32 window."""

    def __init__(self, icon_path: Path, tooltip: str = "Dorm WiFi") -> None:
        self.icon_path = Path(icon_path)
        self._tooltip = tooltip_text(tooltip)
        self._menu_rows: list[tuple[int, str, bool]] = []
        self._commands: queue.Queue[int] = queue.Queue()
        self._lock = threading.Lock()
        self._hwnd = None
        self._nid = None
        self._icon = None
        self._thread = None
        self._ready = threading.Event()
        self._available = False
        self._added = False
        self._last_tooltip = ""
        self._class_name = f"DormWiFiTray{id(self)}"
        self._wndproc_callback = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def hwnd(self):
        return self._hwnd

    def start(self) -> bool:
        if not _active or not self.icon_path.is_file():
            return False
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self._available

    def set_menu(self, rows: list[tuple[int, str, bool]]) -> None:
        with self._lock:
            self._menu_rows = list(rows)

    def update(self, tooltip: str) -> None:
        text = tooltip_text(tooltip)
        with self._lock:
            self._tooltip = text
            if not self._available or self._nid is None:
                return
            if text == self._last_tooltip:
                return
            self._last_tooltip = text
            self._nid.szTip = text
            self._nid.uFlags = NIF_TIP
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def notify(self, title: str, text: str) -> None:
        with self._lock:
            if not self._available or self._nid is None:
                return
            self._nid.uFlags = NIF_INFO
            self._nid.szInfoTitle = title[:63]
            self._nid.szInfo = text[:255]
            self._nid.dwInfoFlags = NIIF_INFO
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def poll(self) -> list[int]:
        """Commands the tray collected since the last poll, for the Tk thread."""
        found: list[int] = []
        while True:
            try:
                found.append(self._commands.get_nowait())
            except queue.Empty:
                return found

    def remove(self) -> None:
        hwnd = self._hwnd
        if hwnd:
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._available = False
        self._hwnd = None

    # MARK: - The Win32 side

    def _run(self) -> None:
        hinstance = _kernel32.GetModuleHandleW(None)
        self._wndproc_callback = _WNDPROC(self._wndproc)
        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc_callback
        window_class.hInstance = hinstance
        window_class.lpszClassName = self._class_name
        registered = _user32.RegisterClassW(ctypes.byref(window_class))
        if not registered:
            self._ready.set()
            return

        hwnd = _user32.CreateWindowExW(
            0,
            self._class_name,
            "DormWiFiTray",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            _user32.UnregisterClassW(self._class_name, hinstance)
            self._ready.set()
            return

        self._hwnd = hwnd
        icon = _user32.LoadImageW(
            None, str(self.icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE
        )
        if not icon:
            _user32.DestroyWindow(hwnd)
            _user32.UnregisterClassW(self._class_name, hinstance)
            self._ready.set()
            return

        self._icon = icon
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = icon
        with self._lock:
            nid.szTip = self._tooltip
            self._nid = nid
        self._added = bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self._available = self._added
        self._ready.set()

        if self._added:
            message = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))

        with self._lock:
            if self._added:
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                self._added = False
            self._nid = None
        if self._icon:
            _user32.DestroyIcon(self._icon)
            self._icon = None
        _user32.DestroyWindow(hwnd)
        _user32.UnregisterClassW(self._class_name, hinstance)
        self._available = False
        self._hwnd = None

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            event = int(lparam) & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._commands.put(CMD_SHOW)
            elif event == WM_RBUTTONUP:
                self._show_menu(hwnd)
            return 0
        if _TASKBAR_CREATED and message == _TASKBAR_CREATED:
            # Explorer restarted and forgot every icon it was drawing.
            with self._lock:
                if self._nid is not None:
                    self._added = bool(
                        _shell32.Shell_NotifyIconW(
                            NIM_ADD, ctypes.byref(self._nid)
                        )
                    )
            return 0
        if message == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self, hwnd) -> None:
        with self._lock:
            rows = list(self._menu_rows)
        if not rows:
            return
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for command, label, enabled in rows:
                flags = MF_STRING | (0 if enabled else MF_GRAYED)
                _user32.AppendMenuW(menu, flags, command, label)
            point = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            _user32.SetForegroundWindow(hwnd)
            chosen = _user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            # The documented dance: without it the menu stays on screen until
            # another window is clicked.
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        finally:
            _user32.DestroyMenu(menu)
        if chosen:
            self._commands.put(int(chosen))


def tooltip_text(text: str) -> str:
    """A tooltip fits 127 characters plus the terminator."""
    return str(text)[:127]
