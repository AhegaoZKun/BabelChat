"""Full-screen picker for the chat area (GTK4).

The user drags a box around their WoW chat window. One transparent surface is
opened on every monitor at once, so there is no "which screen?" question: they
draw on whichever one shows the game, and the monitor is read off the surface
that received the drag.

On Wayland the surfaces are layer-shell overlays, the same mechanism that lets
the chat overlay sit above a fullscreen game. Elsewhere they are plain
fullscreen windows. The box is an ordinary widget moved inside the surface, so
dragging it costs nothing: the layer-shell resize jitter that makes the chat
overlay use a ghost outline does not apply here.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

from app.i18n import tr  # noqa: E402
from app.ocr_capture import OcrRegion  # noqa: E402

_MIN_PX = 30

_CSS = b"""
window.bc-picker { background-color: rgba(0, 0, 0, 0.32); }
.bc-pick-rect { border: 2px solid #99ccff; background-color: rgba(153, 204, 255, 0.20); }
.bc-pick-hint {
    background-color: rgba(0, 0, 0, 0.78);
    color: #ffffff;
    padding: 8px 18px;
    border-radius: 8px;
    font-size: 16px;
}
"""


def _normalised(x0: float, y0: float, dx: float, dy: float) -> tuple[float, float, float, float]:
    """Corner + offset (offset may be negative) as x, y, width, height."""
    return min(x0, x0 + dx), min(y0, y0 + dy), abs(dx), abs(dy)


class RegionPicker:
    """Open on every monitor; call `on_done(region)` once, or `on_done(None)` on Esc."""

    def __init__(self, on_done: Callable[[OcrRegion | None], None]) -> None:
        self._on_done = on_done
        self._windows: list[Gtk.Window] = []
        self._finished = False

    def show(self) -> None:
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        monitors = display.get_monitors()
        sizes: list[tuple[int, int]] = []
        for i in range(monitors.get_n_items()):
            geo = monitors.get_item(i).get_geometry()
            scale = monitors.get_item(i).get_scale_factor()
            sizes.append((geo.width * scale, geo.height * scale))

        seen: dict[tuple[int, int], int] = {}
        for i in range(monitors.get_n_items()):
            ordinal = seen.get(sizes[i], 0)
            seen[sizes[i]] = ordinal + 1
            window = self._make_window(monitors.get_item(i), sizes[i], ordinal)
            self._windows.append(window)
            window.present()

    # -- internals ----------------------------------------------------------

    def _make_window(self, monitor: Gdk.Monitor, pixel_size: tuple[int, int], ordinal: int) -> Gtk.Window:
        geo = monitor.get_geometry()
        window = Gtk.Window()
        window.add_css_class("bc-picker")
        window.set_decorated(False)

        if LayerShell is not None and LayerShell.is_supported():
            LayerShell.init_for_window(window)
            LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
            LayerShell.set_monitor(window, monitor)
            for edge in (
                LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT,
            ):
                LayerShell.set_anchor(window, edge, True)
            LayerShell.set_exclusive_zone(window, -1)
            # Esc has to reach this surface even while the game has focus.
            LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.EXCLUSIVE)
        else:
            window.set_default_size(geo.width, geo.height)
            window.fullscreen()

        fixed = Gtk.Fixed()
        fixed.set_hexpand(True)
        fixed.set_vexpand(True)
        rect = Gtk.Box()
        rect.add_css_class("bc-pick-rect")
        rect.set_visible(False)
        fixed.put(rect, 0, 0)

        hint = Gtk.Label(label=tr("region.hint"))
        hint.add_css_class("bc-pick-hint")
        hint.set_halign(Gtk.Align.CENTER)
        hint.set_valign(Gtk.Align.START)
        hint.set_margin_top(40)
        hint.set_can_target(False)  # clicks go through to the drag below

        overlay = Gtk.Overlay()
        overlay.set_child(fixed)
        overlay.add_overlay(hint)
        window.set_child(overlay)
        window.set_cursor_from_name("crosshair")

        start = {"x": 0.0, "y": 0.0}
        drag = Gtk.GestureDrag()
        drag.set_button(1)

        def on_begin(_gesture: Gtk.GestureDrag, x: float, y: float) -> None:
            start["x"], start["y"] = x, y
            rect.set_visible(False)

        def on_update(_gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
            x, y, w, h = _normalised(start["x"], start["y"], dx, dy)
            fixed.move(rect, x, y)
            rect.set_size_request(max(1, int(w)), max(1, int(h)))
            rect.set_visible(True)

        def on_end(_gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
            x, y, w, h = _normalised(start["x"], start["y"], dx, dy)
            if w < _MIN_PX or h < _MIN_PX:
                rect.set_visible(False)  # a click, or too small to be a chat window
                return
            width = fixed.get_width() or geo.width
            height = fixed.get_height() or geo.height
            self._finish(
                OcrRegion(
                    x=x / width, y=y / height, w=w / width, h=h / height,
                    monitor=ordinal,
                    abs=(geo.x + int(x), geo.y + int(y), int(w), int(h)),
                    px=pixel_size,
                )
            )

        drag.connect("drag-begin", on_begin)
        drag.connect("drag-update", on_update)
        drag.connect("drag-end", on_end)
        fixed.add_controller(drag)

        keys = Gtk.EventControllerKey()

        def on_key(_ctrl: Gtk.EventControllerKey, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
            if keyval == Gdk.KEY_Escape:
                self._finish(None)
                return True
            return False

        keys.connect("key-pressed", on_key)
        window.add_controller(keys)
        return window

    def _finish(self, region: OcrRegion | None) -> None:
        if self._finished:
            return
        self._finished = True
        for window in self._windows:
            window.close()
        self._windows.clear()
        # Let the surfaces go away before the caller reacts (it may open a dialog).
        GLib.idle_add(lambda: (self._on_done(region), False)[1])
