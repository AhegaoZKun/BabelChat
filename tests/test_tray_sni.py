"""Tray SNI tests — skipped when PyGObject or a D-Bus session isn't available.

The dbusmenu/SNI Variant signatures are exercised over a real private session
bus; CI without python3-gi simply skips.
"""

import os
import shutil
import subprocess
import threading
import time

import pytest

gi = pytest.importorskip("gi")

from gi.repository import Gio, GLib  # noqa: E402

from app.tray_sni import MenuItem, TrayIcon  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="no dbus-daemon")


@pytest.fixture
def session_bus():
    addr = subprocess.check_output(
        ["dbus-daemon", "--session", "--fork", "--print-address"]
    ).decode().strip()
    old = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = addr
    yield addr
    if old is not None:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = old


def test_tray_exports_and_dispatches(session_bus):
    clicked = []
    tray = TrayIcon(
        icon_name="applications-chat",
        on_activate=lambda: clicked.append("activate"),
        items=[
            MenuItem("overlay", "Hide overlay", lambda: clicked.append("overlay")),
            MenuItem("tr", "Translation", lambda: clicked.append("tr"), checkable=True, checked=True),
            MenuItem(),
            MenuItem("quit", "Quit", lambda: clicked.append("quit")),
        ],
    )
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    time.sleep(0.4)
    client = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(path, iface, method, params, reply=None):
        return client.call_sync(
            tray._bus_name, path, iface, method, params, reply, Gio.DBusCallFlags.NONE, 2000, None
        )

    status = call(
        "/StatusNotifierItem",
        "org.freedesktop.DBus.Properties",
        "Get",
        GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "Status")),
    ).unpack()[0]
    assert status == "Active"

    raw = call("/MenuBar", "com.canonical.dbusmenu", "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
    # Wire-format check: each child must be v<(ia{sv}av)>, not v<v<...>> —
    # Plasma's C++ dbusmenu parser rejects double-wrapped variants.
    first_child = raw.get_child_value(1).get_child_value(2).get_child_value(0)
    assert first_child.get_variant().get_type_string() == "(ia{sv}av)"
    rev, (_, _, children) = raw.unpack()
    assert [c[1].get("label", c[1].get("type")) for c in children] == [
        "Hide overlay",
        "Translation",
        "separator",
        "Quit",
    ]
    assert children[1][1]["toggle-state"] == 1

    call("/StatusNotifierItem", "org.kde.StatusNotifierItem", "Activate", GLib.Variant("(ii)", (0, 0)))
    for i in (1, 2, 4):
        call(
            "/MenuBar",
            "com.canonical.dbusmenu",
            "Event",
            GLib.Variant("(isvu)", (i, "clicked", GLib.Variant("s", ""), 0)),
        )
    time.sleep(0.3)
    assert clicked == ["activate", "overlay", "tr", "quit"]

    GLib.idle_add(lambda: (tray.update_item("tr", checked=False), False)[-1])
    time.sleep(0.3)
    rev2, (_, _, children2) = call(
        "/MenuBar", "com.canonical.dbusmenu", "GetLayout", GLib.Variant("(iias)", (0, -1, []))
    ).unpack()
    assert rev2 > rev
    assert children2[1][1]["toggle-state"] == 0
    loop.quit()
    tray.shutdown()
