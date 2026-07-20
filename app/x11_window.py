"""X11 helpers for the overlay fallback: always-on-top via EWMH, positioning.

GTK4 removed gtk_window_set_keep_above(), but on X11 the mechanism still
exists at the protocol level: a _NET_WM_STATE client message to the root
window, which the window manager honors. Implemented directly over libX11
with ctypes so no new Python dependency is needed (libX11 is present on any
system with an X server). Every function fails soft — an overlay that can't
be raised is still a working chat window.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

logger = logging.getLogger(__name__)

_NET_WM_STATE_REMOVE = 0
_NET_WM_STATE_ADD = 1
SubstructureNotifyMask = 1 << 19
SubstructureRedirectMask = 1 << 20
ClientMessage = 33


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


def _open_xlib():
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    xlib = ctypes.CDLL(name)
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xlib.XSendEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p,
    ]
    xlib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
    xlib.XMoveResizeWindow.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint,
    ]
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    return xlib


def get_xid(gtk_window) -> int | None:
    """XID of a realized Gtk.Window on the X11 backend, else None."""
    try:
        import gi

        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11

        surface = gtk_window.get_surface()
        if surface is not None and isinstance(surface, GdkX11.X11Surface):
            return surface.get_xid()
    except Exception:  # noqa: BLE001 — absence of GdkX11 just means no X11
        logger.debug("x11: could not resolve XID", exc_info=True)
    return None


def _send_wm_state(xlib, dpy, xid: int, action: int, atom_a: bytes, atom_b: bytes | None) -> None:
    ev = _XEvent()
    ev.xclient.type = ClientMessage
    ev.xclient.window = xid
    ev.xclient.message_type = xlib.XInternAtom(dpy, b"_NET_WM_STATE", 0)
    ev.xclient.format = 32
    ev.xclient.data[0] = action
    ev.xclient.data[1] = xlib.XInternAtom(dpy, atom_a, 0)
    ev.xclient.data[2] = xlib.XInternAtom(dpy, atom_b, 0) if atom_b else 0
    ev.xclient.data[3] = 1  # source: normal application
    root = xlib.XDefaultRootWindow(dpy)
    xlib.XSendEvent(
        dpy, root, 0, SubstructureRedirectMask | SubstructureNotifyMask, ctypes.byref(ev)
    )


def apply_overlay_hints(xid: int) -> bool:
    """Make the window always-on-top, sticky, and hidden from taskbar/pager."""
    try:
        xlib = _open_xlib()
        dpy = xlib.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            _send_wm_state(xlib, dpy, xid, _NET_WM_STATE_ADD, b"_NET_WM_STATE_ABOVE", b"_NET_WM_STATE_STICKY")
            _send_wm_state(
                xlib, dpy, xid, _NET_WM_STATE_ADD,
                b"_NET_WM_STATE_SKIP_TASKBAR", b"_NET_WM_STATE_SKIP_PAGER",
            )
            xlib.XFlush(dpy)
        finally:
            xlib.XCloseDisplay(dpy)
        return True
    except Exception:  # noqa: BLE001 — degrade to a normal window
        logger.exception("x11: failed to apply overlay hints")
        return False


def move_window(xid: int, x: int, y: int, w: int | None = None, h: int | None = None) -> bool:
    """Move (and optionally resize) a window by XID."""
    try:
        xlib = _open_xlib()
        dpy = xlib.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            if w is not None and h is not None:
                xlib.XMoveResizeWindow(dpy, xid, int(x), int(y), int(w), int(h))
            else:
                xlib.XMoveWindow(dpy, xid, int(x), int(y))
            xlib.XFlush(dpy)
        finally:
            xlib.XCloseDisplay(dpy)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("x11: failed to move window")
        return False
