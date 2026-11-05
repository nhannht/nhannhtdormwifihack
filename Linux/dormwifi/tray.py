"""A tray icon, spoken to the desktop over D-Bus rather than drawn by GTK.

GTK 4 removed ``Gtk.StatusIcon``, and libayatana-appindicator — which is what
most tray libraries use on Linux — is a GTK 3 library, so a GTK 4 process cannot
load it. Neither of those is the obstacle it looks like: the icon lives on the
session bus. A program exports ``org.kde.StatusNotifierItem`` plus a
``com.canonical.dbusmenu`` menu, and whichever shell is running
``org.kde.StatusNotifierWatcher`` (GNOME with the AppIndicator extension, KDE,
Xfce, Cinnamon, Budgie) draws it. That is exactly what libayatana-appindicator
does internally.

Gio is enough, so this module imports no window toolkit at all and can be tested
without a display.

The icon is drawn with cairo instead of being looked up in the theme. A GTK 3
program asks the icon theme for a pixbuf; GTK 4's theme hands back a
``Gtk.IconPaintable`` that only a renderer can turn into pixels. Drawing four
small shapes is less code than that, and it means the shape as well as the colour
says what is going on.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

import cairo
import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib  # noqa: E402  - after require_version

INTERFACE_SNI = "org.kde.StatusNotifierItem"
INTERFACE_MENU = "com.canonical.dbusmenu"
PATH_SNI = "/StatusNotifierItem"
PATH_MENU = "/MenuBar"
BUS_NAME = "org.kde.StatusNotifierItem"

#: The colours are for a human, and the shapes below carry the same information
#: without them.
_GREEN = (0.18, 0.76, 0.49)
_AMBER = (0.90, 0.65, 0.04)
_RED = (0.88, 0.11, 0.14)
_BLUE = (0.21, 0.52, 0.89)
_GREY = (0.60, 0.60, 0.60)

_ICON_SIZE = 22

_ITEM_HEADLINE = 1
_ITEM_NETWORK = 2
_ITEM_ROUTER = 3
_ITEM_INTERNET = 4
_ITEM_SEPARATOR_ONE = 5
_ITEM_TOGGLE = 6
_ITEM_SIGN_IN = 7
_ITEM_SHOW = 8
_ITEM_SEPARATOR_TWO = 9
_ITEM_QUIT = 10

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
  </interface>
</node>
"""


# MARK: - What the icon and the menu say


@dataclass(frozen=True)
class MenuEntry:
    """One line of the drop-down."""

    id: int
    label: str = ""
    enabled: bool = True
    separator: bool = False


def icon_name_for(connection: str) -> str:
    """A theme icon name, for hosts that would rather look one up."""
    return {
        "signedIn": "emblem-ok-symbolic",
        "starting": "view-refresh-symbolic",
        "signingIn": "view-refresh-symbolic",
        "wrongNetwork": "dialog-warning-symbolic",
        "onFallback": "network-wireless-symbolic",
        "stopped": "media-playback-pause-symbolic",
        "stopping": "process-stop-symbolic",
    }.get(connection, "view-refresh-symbolic")


def icon_pixmap(connection: str, has_internet: bool) -> bytes:
    """The icon as ARGB32 bytes, big-endian, one scanline after another.

    The shape says what the colour says, so the icon still means something
    without any colour at all: a filled circle when signed in, a ring while it is
    working, a triangle when the router is not there, a rounded square on the
    safety-net network, and two bars when it is paused.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, _ICON_SIZE, _ICON_SIZE)
    context = cairo.Context(surface)
    middle = _ICON_SIZE / 2
    radius = _ICON_SIZE * 0.36

    if connection == "signedIn":
        colour = _GREEN if has_internet else _AMBER
        context.set_source_rgb(*colour)
        context.arc(middle, middle, radius, 0, 2 * math.pi)
        context.fill()
    elif connection in ("starting", "signingIn"):
        context.set_source_rgb(*_AMBER)
        context.set_line_width(2.5)
        context.arc(middle, middle, radius, 0.4, 0.4 + 1.55 * math.pi)
        context.stroke()
    elif connection == "wrongNetwork":
        context.set_source_rgb(*_RED)
        context.move_to(middle, _ICON_SIZE * 0.16)
        context.line_to(_ICON_SIZE * 0.9, _ICON_SIZE * 0.84)
        context.line_to(_ICON_SIZE * 0.1, _ICON_SIZE * 0.84)
        context.close_path()
        context.fill()
    elif connection == "onFallback":
        colour = _BLUE if has_internet else _GREY
        context.set_source_rgb(*colour)
        context.arc(_ICON_SIZE * 0.26, middle, radius * 0.85, 0, 2 * math.pi)
        context.fill()
        context.arc(_ICON_SIZE * 0.74, middle, radius * 0.85, 0, 2 * math.pi)
        context.fill()
    else:
        # Paused, stopping, or anything this build does not know.
        context.set_source_rgb(*_GREY)
        context.rectangle(middle - radius * 0.75, middle - radius, radius * 0.5, radius * 2)
        context.rectangle(middle + radius * 0.25, middle - radius, radius * 0.5, radius * 2)
        context.fill()

    return _argb_bytes(surface)


def _argb_bytes(surface: "cairo.ImageSurface") -> bytes:
    """cairo stores BGRA in memory order; the tray wants ARGB, big-endian."""
    surface.flush()
    data = bytes(surface.get_data())
    width, height = surface.get_width(), surface.get_height()
    stride = surface.get_stride()
    out = bytearray(width * height * 4)

    for row in range(height):
        source = row * stride
        target = row * width * 4
        for column in range(width):
            blue = data[source + column * 4]
            green = data[source + column * 4 + 1]
            red = data[source + column * 4 + 2]
            alpha = data[source + column * 4 + 3]
            out[target + column * 4] = alpha
            out[target + column * 4 + 1] = red
            out[target + column * 4 + 2] = green
            out[target + column * 4 + 3] = blue

    return bytes(out)


def menu_entries(status, running: bool, controlled: bool = True) -> list[MenuEntry]:
    """The drop-down, in the order the macOS menu has it.

    The countdown is in the headline, so a glance at the opened menu is enough:
    the label is rebuilt from the deadline, not from a number stored when the
    router was last asked.
    """
    network = getattr(status, "network", "") or "—"
    router = getattr(status, "router_ip", "") or "—"
    internet = "up" if getattr(status, "has_internet", False) else "down"

    if controlled:
        toggle = MenuEntry(_ITEM_TOGGLE, "Pause auto-connect" if running else "Start auto-connect")
    else:
        # A systemd unit or a terminal holds the watcher. Offering to pause it
        # from here would be a button that cannot work.
        toggle = MenuEntry(_ITEM_TOGGLE, "Watched by another process", enabled=False)

    return [
        MenuEntry(_ITEM_HEADLINE, status.headline, enabled=False),
        MenuEntry(_ITEM_NETWORK, f"Network: {network}", enabled=False),
        MenuEntry(_ITEM_ROUTER, f"Router:  {router}", enabled=False),
        MenuEntry(_ITEM_INTERNET, f"Internet:  {internet}", enabled=False),
        MenuEntry(_ITEM_SEPARATOR_ONE, separator=True),
        toggle,
        MenuEntry(_ITEM_SIGN_IN, "Sign in now", enabled=controlled),
        MenuEntry(_ITEM_SHOW, "Show window"),
        MenuEntry(_ITEM_SEPARATOR_TWO, separator=True),
        MenuEntry(_ITEM_QUIT, "Quit"),
    ]


def needs_attention(status) -> bool:
    """Whether the icon should ask for a look rather than sit quietly.

    A paused watcher and a healthy session are both fine to ignore; a router that
    has stopped answering is not.
    """
    return getattr(status, "connection", "") == "wrongNetwork"


# MARK: - The icon on the bus


class TrayIcon:
    """Exports a status-notifier item and its menu on the session bus.

    One instance per process. Every method that talks to the bus swallows
    failures: a desktop without a tray is a normal desktop, and losing the icon
    must never take the watcher with it.
    """

    def __init__(
        self,
        title: str,
        on_activate: Callable[[], None],
        on_secondary_activate: Callable[[], None],
        on_toggle: Callable[[], None],
        on_sign_in: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._title = title
        self._on_activate = on_activate
        self._on_secondary_activate = on_secondary_activate
        self._callbacks = {
            _ITEM_TOGGLE: on_toggle,
            _ITEM_SIGN_IN: on_sign_in,
            _ITEM_SHOW: on_activate,
            _ITEM_QUIT: on_quit,
        }
        self._connection: Gio.DBusConnection | None = None
        self._registrations: list[int] = []
        self._status = None
        self._running = False
        self._controlled = True
        self._revision = 1
        self._pixmap = icon_pixmap("starting", False)
        self._icon_name = icon_name_for("starting")
        self._label = ""
        self._last_layout = 0.0
        self._registered = False

    # MARK: - Lifecycle

    @property
    def is_showing(self) -> bool:
        return self._registered

    def start(self) -> bool:
        """Export the objects and ask the desktop to draw them.

        Returns False when there is no tray host on this desktop, which is not an
        error: the window still works.
        """
        try:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False

        try:
            self._export(PATH_SNI, SNI_XML, self._on_sni_call, self._on_sni_property)
            self._export(PATH_MENU, MENU_XML, self._on_menu_call, self._on_menu_property)
        except GLib.Error:
            self.stop()
            return False

        if not self._watcher_running():
            self.stop()
            return False

        try:
            self._connection.call_sync(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._connection.get_unique_name(),)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error:
            self.stop()
            return False

        self._registered = True
        self._watch_for_a_restarted_shell()
        return True

    def _watch_for_a_restarted_shell(self) -> None:
        """Register again if the shell goes away and comes back.

        A GNOME Shell restart, or the extension being switched off and on, takes
        every tray icon with it and leaves the exported objects orphaned. Without
        this the icon is gone until the app is restarted, and an app with no
        window open then looks like it has died.
        """
        if self._connection is None:
            return
        self._connection.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            "org.kde.StatusNotifierWatcher",
            Gio.DBusSignalFlags.NONE,
            self._on_watcher_changed,
        )

    def _on_watcher_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        _name, old_owner, new_owner = parameters.unpack()
        if new_owner and not old_owner:
            self.start()

    def stop(self) -> None:
        """Stop drawing, and put the objects away.

        GNOME 42's watcher implements no ``UnregisterStatusNotifierItem``, and
        the protocol only drops an item when the bus name behind it goes away, so
        a process that stays alive cannot take its icon back out of the panel.
        Blanking it is the most that can be done from here; the entry itself goes
        when the process does. What matters is that nothing in the panel can be
        clicked into a handler that no longer exists.
        """
        if self._registered and self._connection is not None:
            try:
                self._notify(
                    INTERFACE_SNI,
                    {
                        "IconName": GLib.Variant("s", ""),
                        "IconPixmap": GLib.Variant("a(iiay)", []),
                        "Status": GLib.Variant("s", "Passive"),
                    },
                )
            except GLib.Error:
                pass

        for identifier in self._registrations:
            try:
                self._connection.unregister_object(identifier)  # type: ignore[union-attr]
            except (GLib.Error, AttributeError):
                pass
        self._registrations = []
        self._registered = False

    def _watcher_running(self) -> bool:
        try:
            reply = self._connection.call_sync(  # type: ignore[union-attr]
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error:
            return False
        return bool(reply.unpack()[0])

    def _export(self, path, xml, on_call, on_property) -> None:
        info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
        identifier = self._connection.register_object(  # type: ignore[union-attr]
            path, info, on_call, on_property, None
        )
        if identifier:
            self._registrations.append(identifier)

    # MARK: - What it says

    def update(self, status, running: bool, controlled: bool = True) -> None:
        """Take a new state, and tell the desktop only what it needs to hear.

        The countdown in the menu label comes from a deadline, so it keeps
        moving on its own; what has to reach the desktop is a *changed* label.
        Re-emitting the whole layout every second would do nothing a person can
        see, so a fresh reading of the same session is announced at most every
        ten seconds, and a real change is announced at once.
        """
        previous = self._status
        previous_pixmap = self._pixmap
        previous_running = self._running
        previous_controlled = self._controlled
        previous_label = self._label

        self._status = status
        self._running = running
        self._controlled = controlled
        self._pixmap = icon_pixmap(status.connection, status.has_internet)
        self._icon_name = icon_name_for(status.connection)
        self._label = status.headline

        if not self._registered:
            return

        state_changed = (
            previous is None
            or status.connection != previous.connection
            or status.has_internet != previous.has_internet
            or running != previous_running
            or controlled != previous_controlled
            or status.network != previous.network
            or status.router_ip != previous.router_ip
        )
        icon_changed = self._pixmap != previous_pixmap
        label_changed = self._label != previous_label

        if icon_changed:
            self._emit("NewIcon")
            self._emit("NewToolTip")

        now = time.monotonic()
        if state_changed or (label_changed and now - self._last_layout >= 10):
            self._revision += 1
            self._last_layout = now
            self._emit("LayoutUpdated", GLib.Variant("(ui)", (self._revision, 0)))

        if state_changed or icon_changed:
            self._notify(
                INTERFACE_SNI,
                {
                    "IconName": GLib.Variant("s", self._icon_name),
                    "IconPixmap": self._variant_pixmap(),
                    "ToolTip": self._variant_tooltip(),
                    "Status": GLib.Variant(
                        "s", "NeedsAttention" if needs_attention(status) else "Active"
                    ),
                },
            )

    def _notify(self, interface: str, changed: dict[str, GLib.Variant]) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(
                None,
                PATH_SNI,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (interface, changed, [])),
            )
        except GLib.Error:
            pass

    def _emit(self, signal: str, parameters: GLib.Variant | None = None) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(None, PATH_SNI, INTERFACE_SNI, signal, parameters)
        except GLib.Error:
            pass

    def _variant_pixmap(self) -> GLib.Variant:
        return GLib.Variant("a(iiay)", [(_ICON_SIZE, _ICON_SIZE, self._pixmap)])

    def _variant_tooltip(self) -> GLib.Variant:
        headline = getattr(self._status, "headline", self._title) if self._status else self._title
        return GLib.Variant("(sa(iiay)ss)", ("", [], self._title, headline))

    # MARK: - org.kde.StatusNotifierItem

    def _on_sni_property(self, _connection, _sender, _path, _interface, name):
        status = self._status
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "nhannhtdormwifihack"),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant(
                "s", "NeedsAttention" if status is not None and needs_attention(status) else "Active"
            ),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconPixmap": self._variant_pixmap(),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", icon_name_for("wrongNetwork")),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "ToolTip": self._variant_tooltip(),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", PATH_MENU),
        }
        return values.get(name)

    def _on_sni_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        if method == "Activate":
            self._on_activate()
            invocation.return_value(None)
        elif method == "SecondaryActivate":
            self._on_secondary_activate()
            invocation.return_value(None)
        elif method in ("ContextMenu", "Scroll"):
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, f"No such method: {method}"
            )

    # MARK: - com.canonical.dbusmenu

    def _on_menu_property(self, _connection, _sender, _path, _interface, name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(name)

    def _entries(self) -> list[MenuEntry]:
        if self._status is None:
            from .status_board import WatcherStatus

            self._status = WatcherStatus()
        return menu_entries(self._status, self._running, self._controlled)

    def _on_menu_call(
        self, _connection, _sender, _path, _interface, method, parameters, invocation
    ):
        try:
            if method == "GetLayout":
                revision, layout = self._build_layout()
                invocation.return_value(GLib.Variant("(u(ia{sv}av))", (revision, layout)))
            elif method == "GetProperty":
                item_id, name = parameters.unpack()
                entry = self._entry(item_id)
                if entry is None or name not in ("label", "enabled"):
                    invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
                else:
                    value = entry.label if name == "label" else entry.enabled
                    variant = (
                        GLib.Variant("s", value)
                        if isinstance(value, str)
                        else GLib.Variant("b", value)
                    )
                    invocation.return_value(GLib.Variant("(v)", (variant,)))
            elif method == "GetGroupProperties":
                ids, _names = parameters.unpack()
                rows = [
                    (entry.id, self._properties(entry))
                    for entry in (self._entry(item_id) for item_id in ids or [0])
                    if entry is not None
                ]
                invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
            elif method == "Event":
                item_id, event_id, _data, _timestamp = parameters.unpack()
                if event_id in ("clicked", "activate"):
                    self._activate_item(item_id)
                invocation.return_value(None)
            elif method == "EventGroup":
                events = parameters.unpack()[0]
                for item_id, event_id, _data, _timestamp in events:
                    if event_id in ("clicked", "activate"):
                        self._activate_item(item_id)
                invocation.return_value(GLib.Variant("(ai)", ([],)))
            elif method == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
            elif method == "AboutToShowGroup":
                invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            else:
                invocation.return_error_literal(
                    Gio.DBusError.quark(),
                    Gio.DBusError.UNKNOWN_METHOD,
                    f"No such method: {method}",
                )
        except (GLib.Error, TypeError, ValueError) as error:
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.FAILED, str(error)
            )

    def _build_layout(self):
        """The menu tree as plain Python, ready to be turned into a variant.

        Deliberately not pre-built ``GLib.Variant`` objects. PyGObject rebuilds a
        container it is handed by *iterating* it, and iterating a variant of type
        ``av`` yields the unpacked children, which then fail the "expected a
        GLib.Variant" check one level down. Only the leaves may be variants; the
        tuples and lists around them stay Python.
        """
        children = [
            GLib.Variant("(ia{sv}av)", (entry.id, self._properties(entry), []))
            for entry in self._entries()
        ]
        root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
        return self._revision, root

    def _properties(self, entry: MenuEntry) -> dict[str, GLib.Variant]:
        if entry.separator:
            return {"type": GLib.Variant("s", "separator"), "visible": GLib.Variant("b", True)}
        return {
            "label": GLib.Variant("s", entry.label),
            "enabled": GLib.Variant("b", entry.enabled),
            "visible": GLib.Variant("b", True),
            "type": GLib.Variant("s", "standard"),
        }

    def _entry(self, item_id: int) -> MenuEntry | None:
        for entry in self._entries():
            if entry.id == item_id:
                return entry
        return None

    def _activate_item(self, item_id: int) -> None:
        callback = self._callbacks.get(item_id)
        if callback is not None:
            callback()
