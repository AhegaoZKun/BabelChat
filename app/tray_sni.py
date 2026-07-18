"""System tray icon via StatusNotifierItem (SNI) over D-Bus.

GTK4 has no tray API and the common appindicator libraries are GTK3-only, so
this implements the two D-Bus interfaces Plasma (and other SNI hosts like
waybar/swaybar) consume directly with Gio — no extra dependencies:

  * org.kde.StatusNotifierItem  — the icon itself (left-click = Activate)
  * com.canonical.dbusmenu      — the right-click context menu

Everything degrades gracefully: if no StatusNotifierWatcher is on the bus
(e.g. plasmashell not running yet), registration waits for it to appear and
re-registers automatically after shell restarts. Failures only log.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

import gi
from gi.repository import Gio, GLib  # noqa: E402

logger = logging.getLogger(__name__)

_SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i"/><arg name="y" type="i"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i"/><arg name="y" type="i"/></method>
    <method name="ContextMenu"><arg name="x" type="i"/><arg name="y" type="i"/></method>
    <method name="Scroll"><arg name="delta" type="i"/><arg name="orientation" type="s"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

_MENU_XML = """
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
  </interface>
</node>
"""

_WATCHER_NAME = "org.kde.StatusNotifierWatcher"
_WATCHER_PATH = "/StatusNotifierWatcher"
_ITEM_PATH = "/StatusNotifierItem"
_MENU_PATH = "/MenuBar"


@dataclass
class MenuItem:
    """One entry in the tray context menu. label=None → separator."""

    key: str = ""
    label: str | None = None
    callback: Callable[[], None] | None = None
    checkable: bool = False
    checked: bool = False
    dbus_id: int = field(default=0, init=False)


class TrayIcon:
    """SNI tray icon with a dbusmenu context menu.

    Not thread-safe: construct and mutate from the GTK main thread. update_*
    methods may be called any time after construction; they take effect once
    the bus connection is up.
    """

    def __init__(
        self,
        *,
        title: str = "BabelChat",
        icon_png: str | None = None,
        icon_name: str = "",
        items: list[MenuItem] | None = None,
        on_activate: Callable[[], None] | None = None,
        on_secondary_activate: Callable[[], None] | None = None,
    ) -> None:
        self._title = title
        self._icon_name = icon_name
        self._pixmaps = _load_pixmaps(icon_png) if icon_png else []
        if not self._pixmaps and not self._icon_name:
            self._icon_name = "applications-chat"  # generic fallback
        self._items = items or []
        for i, item in enumerate(self._items):
            item.dbus_id = i + 1
        self._on_activate = on_activate
        self._on_secondary = on_secondary_activate
        self._revision = 1
        self._conn: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._registrations: list[int] = []
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            None,
        )
        self._watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            _WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            None,
        )

    # ── public API ────────────────────────────────────────────────────────
    def update_item(self, key: str, *, label: str | None = None, checked: bool | None = None) -> None:
        """Change a menu item's label and/or check state; refreshes the menu."""
        for item in self._items:
            if item.key == key:
                if label is not None:
                    item.label = label
                if checked is not None:
                    item.checked = checked
                self._bump_layout()
                return

    def shutdown(self) -> None:
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._conn is not None:
            for reg in self._registrations:
                self._conn.unregister_object(reg)
            self._registrations.clear()
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0

    # ── bus plumbing ──────────────────────────────────────────────────────
    def _on_bus_acquired(self, conn: Gio.DBusConnection, _name: str) -> None:
        self._conn = conn
        try:
            sni = Gio.DBusNodeInfo.new_for_xml(_SNI_XML).interfaces[0]
            menu = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]
            self._registrations.append(
                conn.register_object(_ITEM_PATH, sni, self._sni_call, self._sni_get, None)
            )
            self._registrations.append(
                conn.register_object(_MENU_PATH, menu, self._menu_call, self._menu_get, None)
            )
        except GLib.Error:
            logger.exception("tray: failed to export SNI objects")
            return
        self._register_with_watcher()

    def _on_watcher_appeared(self, _conn, _name, _owner) -> None:
        # (Re-)register whenever a watcher shows up — covers plasmashell restarts.
        self._register_with_watcher()

    def _register_with_watcher(self) -> None:
        if self._conn is None:
            return
        self._conn.call(
            _WATCHER_NAME,
            _WATCHER_PATH,
            _WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_registered,
        )

    def _on_registered(self, conn: Gio.DBusConnection, res) -> None:
        try:
            conn.call_finish(res)
            logger.info("tray: registered with StatusNotifierWatcher")
        except GLib.Error as e:
            logger.debug("tray: watcher registration pending/failed: %s", e.message)

    # ── org.kde.StatusNotifierItem ────────────────────────────────────────
    def _sni_call(self, _c, _s, _p, _i, method, params, invocation) -> None:
        if method == "Activate":
            if self._on_activate:
                self._on_activate()
        elif method == "SecondaryActivate" and self._on_secondary:
            self._on_secondary()
        # ContextMenu/Scroll: the host renders our Menu property itself.
        invocation.return_value(None)

    def _sni_get(self, _c, _s, _p, _i, prop: str):
        tooltip = GLib.Variant("(sa(iiay)ss)", ("", [], self._title, ""))
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "babelchat"),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("u", 0),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconPixmap": GLib.Variant("a(iiay)", self._pixmaps),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "ToolTip": tooltip,
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", _MENU_PATH),
        }
        return values.get(prop)

    # ── com.canonical.dbusmenu ────────────────────────────────────────────
    def _item_props(self, item: MenuItem) -> dict:
        if item.label is None:
            return {"type": GLib.Variant("s", "separator")}
        props = {
            "label": GLib.Variant("s", item.label),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }
        if item.checkable:
            props["toggle-type"] = GLib.Variant("s", "checkmark")
            props["toggle-state"] = GLib.Variant("i", 1 if item.checked else 0)
        return props

    def _menu_call(self, _c, _s, _p, _i, method, params, invocation) -> None:
        if method == "GetLayout":
            # NOTE: elements go into the 'av' as bare structs — pygobject wraps
            # each into a variant itself; pre-wrapping with "v" double-wraps,
            # which Plasma's dbusmenu parser rejects (menu shows empty).
            children = [
                GLib.Variant("(ia{sv}av)", (it.dbus_id, self._item_props(it), []))
                for it in self._items
            ]
            root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, root)))
        elif method == "GetGroupProperties":
            ids = set(params.unpack()[0])
            out = [
                (it.dbus_id, self._item_props(it))
                for it in self._items
                if not ids or it.dbus_id in ids
            ]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            for it in self._items:
                if it.dbus_id == item_id:
                    val = self._item_props(it).get(name) or GLib.Variant("s", "")
                    invocation.return_value(GLib.Variant("(v)", (val,)))
                    return
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                self._dispatch(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _d, _t in params.unpack()[0]:
                if event_id == "clicked":
                    self._dispatch(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _menu_get(self, _c, _s, _p, _i, prop: str):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(prop)

    def _dispatch(self, item_id: int) -> None:
        for it in self._items:
            if it.dbus_id == item_id and it.callback is not None:
                try:
                    it.callback()
                except Exception:  # noqa: BLE001 — a menu action must not kill the bus handler
                    logger.exception("tray: menu callback failed (%s)", it.key)
                return

    def _bump_layout(self) -> None:
        self._revision += 1
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(
                None,
                _MENU_PATH,
                "com.canonical.dbusmenu",
                "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, 0)),
            )
        except GLib.Error:
            logger.debug("tray: LayoutUpdated emit failed", exc_info=True)


def _load_pixmaps(png_path: str) -> list[tuple[int, int, bytes]]:
    """PNG → SNI IconPixmap entries (ARGB32, network byte order)."""
    try:
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        sizes = (22, 32, 48)
        out: list[tuple[int, int, bytes]] = []
        for size in sizes:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(png_path, size, size, True)
            if not pb.get_has_alpha():
                pb = pb.add_alpha(False, 0, 0, 0)
            w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
            rgba = pb.get_pixels()
            argb = bytearray(w * h * 4)
            for y in range(h):
                row = y * stride
                for x in range(w):
                    o = row + x * 4
                    d = (y * w + x) * 4
                    argb[d] = rgba[o + 3]      # A
                    argb[d + 1] = rgba[o]      # R
                    argb[d + 2] = rgba[o + 1]  # G
                    argb[d + 3] = rgba[o + 2]  # B
            out.append((w, h, bytes(argb)))
        return out
    except Exception:  # noqa: BLE001 — icon trouble must never break the tray
        logger.exception("tray: failed to load icon pixmap from %s", png_path)
        return []
