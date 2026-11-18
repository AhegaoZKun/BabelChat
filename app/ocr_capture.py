"""Screen capture for the OCR chat source.

This source reads pixels and nothing else: it never touches the game process.
How pixels may be obtained depends entirely on the platform, so there is one
small interface and a backend per situation:

    Windows, X11 session   MssCapture     direct screen grab
    Wayland, wlroots       GrimCapture    `grim` (wlr-screencopy)
    Wayland, anything else PortalCapture  xdg-desktop-portal ScreenCast + PipeWire

Wayland deliberately forbids silent screen capture, so the portal path shows the
desktop's own "share your screen" dialog once and remembers the answer with a
restore token. That is also the honest description of what the app is doing: the
user explicitly grants it a view of one screen.

A region is stored as fractions of its monitor, so it survives a change of
resolution or scaling. The one exception is `abs`, the region in global logical
coordinates, which `grim` needs because it addresses the compositor's whole
layout rather than a single output.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)


class CaptureUnavailable(RuntimeError):
    """This backend cannot produce frames here; the message says why."""


@dataclass(frozen=True, slots=True)
class OcrRegion:
    """The chat area to read, as fractions (0..1) of one monitor."""

    x: float
    y: float
    w: float
    h: float
    # Which monitor, counted among the monitors of the same pixel size: two
    # identical screens cannot be told apart by size, so the ordinal breaks the tie.
    monitor: int = 0
    # Global logical coordinates (x, y, w, h) at the time it was drawn.
    abs: tuple[int, int, int, int] | None = None
    # Pixel size of the monitor it was drawn on, to find that monitor again.
    px: tuple[int, int] | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> OcrRegion | None:
        if not data:
            return None
        try:
            w, h = float(data["w"]), float(data["h"])
            if w <= 0 or h <= 0:
                return None
            raw_abs = data.get("abs")
            raw_px = data.get("px")
            return cls(
                x=min(max(float(data["x"]), 0.0), 1.0),
                y=min(max(float(data["y"]), 0.0), 1.0),
                w=min(w, 1.0),
                h=min(h, 1.0),
                monitor=int(data.get("monitor", 0)),
                abs=tuple(int(v) for v in raw_abs) if raw_abs else None,  # type: ignore[arg-type]
                px=tuple(int(v) for v in raw_px) if raw_px else None,  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict:
        out: dict = {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "monitor": self.monitor}
        if self.abs:
            out["abs"] = list(self.abs)
        if self.px:
            out["px"] = list(self.px)
        return out

    def crop(self, frame: np.ndarray) -> np.ndarray:
        """The region's pixels out of a frame covering exactly its monitor."""
        height, width = frame.shape[:2]
        x0 = min(int(self.x * width), width - 1)
        y0 = min(int(self.y * height), height - 1)
        x1 = max(x0 + 1, min(width, int((self.x + self.w) * width)))
        y1 = max(y0 + 1, min(height, int((self.y + self.h) * height)))
        return np.ascontiguousarray(frame[y0:y1, x0:x1])


class Capture(Protocol):
    name: str

    def start(self) -> None:
        """Acquire whatever the backend needs. May block on a permission dialog."""
        ...

    def grab(self, region: OcrRegion) -> np.ndarray | None:
        """RGB uint8 pixels of the region, or None when there is no frame yet."""
        ...

    def stop(self) -> None: ...


# ── session detection ────────────────────────────────────────────────────────


def is_wayland_session() -> bool:
    if sys.platform in ("win32", "darwin"):
        return False
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


# ── mss: Windows and X11 ─────────────────────────────────────────────────────


class MssCapture:
    name = "mss"

    def __init__(self) -> None:
        self._sct = None

    def start(self) -> None:
        try:
            import mss
        except ImportError as exc:
            raise CaptureUnavailable("mss is not installed (pip install mss)") from exc
        # mss keeps per-thread state on Windows, so this must run on the thread
        # that will call grab().
        # `mss.MSS` is the current name; `mss.mss` still works but warns.
        self._sct = getattr(mss, "MSS", None)() if hasattr(mss, "MSS") else mss.mss()

    @staticmethod
    def _monitor_for(region: OcrRegion, monitors: list[dict]) -> dict:
        """The monitor the region was drawn on.

        The picker and the capture library do not enumerate screens in the same
        order, so the index alone can name the wrong one. The monitor's pixel
        size is stable across both; the ordinal only settles ties between
        identical screens.
        """
        if region.px:
            same_size = [m for m in monitors if (m["width"], m["height"]) == tuple(region.px)]
            if same_size:
                return same_size[min(max(region.monitor, 0), len(same_size) - 1)]
        return monitors[region.monitor] if 0 <= region.monitor < len(monitors) else monitors[0]

    def grab(self, region: OcrRegion) -> np.ndarray | None:
        if self._sct is None:
            return None
        monitors = self._sct.monitors[1:] or [self._sct.monitors[0]]
        mon = self._monitor_for(region, monitors)
        box = {
            "left": mon["left"] + int(region.x * mon["width"]),
            "top": mon["top"] + int(region.y * mon["height"]),
            "width": max(1, int(region.w * mon["width"])),
            "height": max(1, int(region.h * mon["height"])),
        }
        shot = self._sct.grab(box)
        bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(bgra[:, :, 2::-1])  # BGRA -> RGB

    def stop(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None


# ── grim: wlroots compositors ────────────────────────────────────────────────


class GrimCapture:
    name = "grim"

    def start(self) -> None:
        if not shutil.which("grim"):
            raise CaptureUnavailable("grim is not installed")

    def grab(self, region: OcrRegion) -> np.ndarray | None:
        if region.abs is None:
            return None
        x, y, w, h = region.abs
        try:
            proc = subprocess.run(
                ["grim", "-g", f"{x},{y} {w}x{h}", "-t", "png", "-"],
                capture_output=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None
        from PIL import Image

        return np.asarray(Image.open(io.BytesIO(proc.stdout)).convert("RGB"))

    def stop(self) -> None:
        pass


# ── xdg-desktop-portal ScreenCast + PipeWire ─────────────────────────────────

_PORTAL = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENCAST = "org.freedesktop.portal.ScreenCast"


class PortalCapture:
    """Frames from the compositor's own screen-sharing service.

    `restore_token` lets the desktop skip the dialog on later runs; whatever
    token the portal hands back is passed to `on_token` so the caller can save it.
    """

    name = "portal"

    def __init__(self, restore_token: str = "", on_token: Callable[[str], None] | None = None) -> None:
        self._restore_token = restore_token
        self._on_token = on_token
        self._session: str | None = None
        self._bus = None
        self._pipeline = None
        self._fd: int | None = None
        self._frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tokens = itertools.count(1)

    # -- D-Bus plumbing -----------------------------------------------------

    def _request(self, ctx, method: str, build, timeout: float = 120.0) -> dict:
        """Call a portal method and wait for its asynchronous Response signal."""
        from gi.repository import Gio, GLib

        token = f"babelchat{os.getpid()}x{next(self._tokens)}"
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        answer: dict = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params, *_rest) -> None:
            answer["value"] = params.unpack()

        # Subscribe BEFORE calling: the response can arrive before the call returns.
        sub = self._bus.signal_subscribe(
            _PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, on_response,
        )
        try:
            options = {"handle_token": GLib.Variant("s", token)}
            self._bus.call_sync(
                _PORTAL, _PORTAL_PATH, _SCREENCAST, method, build(options),
                GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None,
            )
            deadline = time.monotonic() + timeout
            while "value" not in answer:
                if self._stop.is_set() or time.monotonic() > deadline:
                    raise CaptureUnavailable("the screen-share request timed out")
                if not ctx.iteration(False):
                    time.sleep(0.02)
        finally:
            self._bus.signal_unsubscribe(sub)

        code, results = answer["value"]
        if code != 0:
            raise CaptureUnavailable("screen sharing was not granted")
        return results

    def start(self) -> None:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gio, GLib, Gst
        except (ImportError, ValueError) as exc:
            raise CaptureUnavailable(
                "Wayland screen capture needs GStreamer's PipeWire plugin "
                "(Arch: gst-plugin-pipewire, Debian/Ubuntu: gstreamer1.0-pipewire)"
            ) from exc

        Gst.init(None)
        if Gst.ElementFactory.find("pipewiresrc") is None:
            raise CaptureUnavailable(
                "the GStreamer PipeWire plugin is missing "
                "(Arch: gst-plugin-pipewire, Debian/Ubuntu: gstreamer1.0-pipewire)"
            )

        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            sess_token = f"babelchatsession{os.getpid()}"

            results = self._request(
                ctx, "CreateSession",
                lambda o: GLib.Variant("(a{sv})", ({**o, "session_handle_token": GLib.Variant("s", sess_token)},)),
            )
            self._session = results["session_handle"]

            def select(options: dict) -> GLib.Variant:
                options = {
                    **options,
                    "types": GLib.Variant("u", 1),          # whole monitors
                    "multiple": GLib.Variant("b", False),
                    "cursor_mode": GLib.Variant("u", 1),    # no cursor in the frames
                    "persist_mode": GLib.Variant("u", 2),   # remember until revoked
                }
                if self._restore_token:
                    options["restore_token"] = GLib.Variant("s", self._restore_token)
                return GLib.Variant("(oa{sv})", (self._session, options))

            self._request(ctx, "SelectSources", select)
            started = self._request(
                ctx, "Start", lambda o: GLib.Variant("(osa{sv})", (self._session, "", o)),
            )
            streams = started.get("streams") or []
            if not streams:
                raise CaptureUnavailable("no screen was shared")
            node_id = int(streams[0][0])

            token = started.get("restore_token")
            if token and token != self._restore_token:
                self._restore_token = token
                if self._on_token:
                    self._on_token(token)

            reply, fd_list = self._bus.call_with_unix_fd_list_sync(
                _PORTAL, _PORTAL_PATH, _SCREENCAST, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self._session, {})),
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
            )
            self._fd = fd_list.get(reply.unpack()[0])
        finally:
            ctx.pop_thread_default()

        self._start_pipeline(Gst, node_id)

    def _start_pipeline(self, Gst, node_id: int) -> None:  # noqa: N803 - GStreamer module
        description = (
            f"pipewiresrc fd={os.dup(self._fd)} path={node_id} always-copy=true do-timestamp=true "
            "! videoconvert ! video/x-raw,format=BGRx "
            "! appsink name=sink max-buffers=1 drop=true sync=false"
        )
        try:
            self._pipeline = Gst.parse_launch(description)
        except Exception as exc:  # noqa: BLE001
            raise CaptureUnavailable(f"could not build the capture pipeline: {exc}") from exc
        sink = self._pipeline.get_by_name("sink")
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise CaptureUnavailable("the PipeWire stream could not be started")

        def pump() -> None:
            # The compositor only sends a frame when the screen changes, so a
            # quiet screen means no new samples; the last frame stays current.
            while not self._stop.is_set():
                sample = sink.emit("try-pull-sample", 500 * Gst.MSECOND)
                if sample is None:
                    continue
                frame = self._sample_to_array(Gst, sample)
                if frame is not None:
                    with self._lock:
                        self._frame = frame

        self._thread = threading.Thread(target=pump, name="portal-capture", daemon=True)
        self._thread.start()

    @staticmethod
    def _sample_to_array(Gst, sample) -> np.ndarray | None:  # noqa: N803
        structure = sample.get_caps().get_structure(0)
        width, height = structure.get_value("width"), structure.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            raw = np.frombuffer(info.data, dtype=np.uint8).copy()
        finally:
            buf.unmap(info)
        stride = raw.size // height if height else 0
        if stride < width * 4:
            return None
        bgrx = raw[: stride * height].reshape(height, stride // 4, 4)[:, :width]
        return np.ascontiguousarray(bgrx[:, :, 2::-1])  # BGRx -> RGB

    # -- Capture interface ----------------------------------------------------

    def grab(self, region: OcrRegion) -> np.ndarray | None:
        with self._lock:
            frame = self._frame
        return None if frame is None else region.crop(frame)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._pipeline is not None:
            try:
                from gi.repository import Gst

                self._pipeline.set_state(Gst.State.NULL)
            except Exception:  # noqa: BLE001
                pass
            self._pipeline = None
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        if self._bus is not None and self._session:
            try:
                from gi.repository import Gio

                self._bus.call_sync(
                    _PORTAL, self._session, "org.freedesktop.portal.Session", "Close",
                    None, None, Gio.DBusCallFlags.NONE, 2000, None,
                )
            except Exception:  # noqa: BLE001
                pass


# ── selection ────────────────────────────────────────────────────────────────


def create_capture(
    restore_token: str = "",
    on_token: Callable[[str], None] | None = None,
) -> Capture:
    """The backend that suits this session. Raises CaptureUnavailable if none does."""
    if not is_wayland_session():
        return MssCapture()
    # wlroots compositors answer to grim and need no permission dialog at all.
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if shutil.which("grim") and any(name in desktop for name in ("sway", "hyprland", "river", "wayfire", "labwc")):
        return GrimCapture()
    return PortalCapture(restore_token, on_token)


def describe_backend() -> str:
    """One line for the settings page: what capture will be used on this machine."""
    if not is_wayland_session():
        return "direct screen capture"
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if shutil.which("grim") and any(name in desktop for name in ("sway", "hyprland", "river", "wayfire", "labwc")):
        return "grim (wlroots)"
    return "Wayland screen sharing (asks once)"
