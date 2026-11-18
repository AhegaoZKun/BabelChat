"""The OCR chat source: reads the chat area off the screen.

It plugs into the pipeline exactly where the memory reader does: same
constructor shape, same `start()`/`stop()`, and it hands over chat-log format
lines through `on_new_line`. Everything after that point (parser, detector,
translator, overlay) neither knows nor cares which source a line came from.

One pass of the loop:

    grab region -> skip if the pixels have not changed -> recognise text
    -> group into lines -> join wrapped lines -> keep only what is new
    -> `on_new_line(line)`

Two things keep it cheap. Recognition only runs when the region's pixels
actually changed, so an idle chat costs one tiny comparison per second. And the
wait after each pass grows with how long recognition took, so a slow machine
backs off instead of saturating a core the game wants.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.ocr_capture import Capture, CaptureUnavailable, OcrRegion, create_capture
from app.ocr_engine import OcrEngine, OcrUnavailable, create_engine, prepare
from app.ocr_parse import ChatDiffer, Entry, boxes_to_lines, frame_from_lines

logger = logging.getLogger(__name__)

# How different two frames must be before recognition runs again, on a 0-255
# scale. Two tests, because a global average cannot see a small change in a big
# area: one short line of text in an otherwise quiet chat moves the average by a
# fraction of a level but changes the blocks it sits in by tens. A block that
# moves by `_BLOCK_THRESHOLD` counts; so does the whole picture drifting by
# `_MEAN_THRESHOLD` (a moving game world behind a translucent chat).
_BLOCK_THRESHOLD = 10.0
_MEAN_THRESHOLD = 0.6
# Recognition also runs at least this often whatever the comparison says. The
# comparison only exists to save work; it must never be the reason a message is
# not delivered, so anything it cannot see is caught within this many seconds.
_MAX_SECONDS_WITHOUT_READING = 4.0
# Blocks the region is divided into for the comparison.
_GRID_COLUMNS = 64
_GRID_ROWS = 24
# Consecutive all-black frames before saying so. A game in exclusive fullscreen
# is invisible to a screen grab, and silence would look like "no chat".
_BLACK_FRAMES_BEFORE_WARNING = 5

#: Recognition passes in a row that read several lines and found no chat message
#: among them before saying so. A user who pointed the box at the wrong thing, or
#: whose chat does not show a channel or a speaker, would otherwise see nothing
#: happen and have no way to tell why.
_BLIND_PASSES_BEFORE_WARNING = 3
_BLIND_MIN_LINES = 3


@dataclass
class ReadReport:
    """What one read of the chat area produced, for showing the user."""

    lines: list[str]
    entries: list[Entry]


@dataclass
class OcrSettings:
    region: OcrRegion | None = None
    script: str = "default"
    interval: float = 1.0
    engine: str = "auto"
    restore_token: str = ""


def _signature(frame: np.ndarray) -> np.ndarray:
    """The region as a coarse grid of average brightness.

    Each cell is the AVERAGE of the pixels it covers, not a sample of one of them:
    text is thin, and a grid that picks single pixels walks straight past it.
    """
    grey = frame.astype(np.float32).mean(axis=2)
    height, width = grey.shape
    rows = max(1, min(_GRID_ROWS, height))
    columns = max(1, min(_GRID_COLUMNS, width))
    cell_h, cell_w = height // rows, width // columns
    cropped = grey[: cell_h * rows, : cell_w * columns]
    return cropped.reshape(rows, cell_h, columns, cell_w).mean(axis=(1, 3))


def _changed(a: np.ndarray, b: np.ndarray) -> bool:
    if a.shape != b.shape:
        return True
    difference = np.abs(a - b)
    return bool(difference.max() > _BLOCK_THRESHOLD or difference.mean() > _MEAN_THRESHOLD)


class OcrChatWatcher:
    """Pipeline source that reads chat from the screen."""

    def __init__(
        self,
        on_new_line: Callable[..., None],
        settings: OcrSettings | None = None,
        on_token: Callable[[str], None] | None = None,
        on_history: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._on_new_line = on_new_line
        self._settings = settings or OcrSettings()
        self._on_token = on_token
        # Called once, with the chat lines that were already on screen when reading
        # began, so the overlay can show what the user can see instead of looking
        # empty until somebody speaks. They are not translated.
        self._on_history = on_history
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

        self._problem = ""
        self._attached = False
        self._last_lines: list[str] = []

    # ── source interface (mirrors MemoryChatWatcher) ─────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ocr-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_attached(self) -> bool:
        return self._attached

    @property
    def problem(self) -> str:
        """Name of what is wrong, or "". Doubles as the overlay's status key."""
        return self._problem

    @property
    def player_name(self) -> str:
        # The screen does not say whose character this is. Skip-own-messages
        # falls back to the configured character name instead.
        return ""

    # ── extras for the settings UI ───────────────────────────────────────────

    @property
    def last_lines(self) -> list[str]:
        """What the last pass read, for the "test" preview."""
        return list(self._last_lines)

    def configure(self, settings: OcrSettings) -> None:
        """Swap settings live; the loop picks them up on its next pass."""
        self._settings = settings
        self._wake.set()

    # ── loop ─────────────────────────────────────────────────────────────────

    def _set_problem(self, name: str) -> None:
        if name != self._problem:
            self._problem = name
            logger.warning("OCR source: %s", name)

    def _clear_problem(self, name: str) -> None:
        """Clear `name` if it is the current problem. Each problem is lifted by its
        own cure, so one condition recovering cannot hide another that is still true.
        """
        if self._problem == name:
            self._problem = ""

    def _send_history(self, differ: ChatDiffer) -> None:
        if self._on_history is None:
            return
        lines = [line for message in differ.baseline() if (line := message.to_log_line())]
        if not lines:
            return
        try:
            self._on_history(lines)
        except Exception:  # noqa: BLE001
            logger.exception("showing the chat already on screen failed")

    def _run(self) -> None:
        capture: Capture | None = None
        engine: OcrEngine | None = None
        differ = ChatDiffer()
        last_sig: np.ndarray | None = None
        black_run = 0
        blind_passes = 0
        history_sent = False
        first_read_logged = False
        last_read = 0.0
        region_seen: OcrRegion | None = None

        def pause(seconds: float) -> None:
            self._wake.wait(seconds)
            self._wake.clear()

        try:
            while not self._stop.is_set():
                settings = self._settings
                region = settings.region

                if region is None:
                    self._attached = False
                    self._set_problem("ocr_no_region")
                    pause(0.5)
                    continue

                self._clear_problem("ocr_no_region")

                # A different region means a different chat window: the old
                # frame is no longer the baseline for the new one.
                if region != region_seen:
                    region_seen = region
                    differ.reset()
                    last_sig = None
                    blind_passes = 0

                if capture is None:
                    try:
                        capture = create_capture(settings.restore_token, self._on_token)
                        capture.start()
                        self._clear_problem("ocr_capture")
                    except CaptureUnavailable as exc:
                        logger.warning("OCR capture unavailable: %s", exc)
                        self._attached = False
                        self._set_problem("ocr_capture")
                        capture = None
                        pause(5.0)
                        continue

                if engine is None or getattr(engine, "script", None) != settings.script:
                    try:
                        engine = create_engine(settings.script, settings.engine)
                        self._clear_problem("ocr_no_engine")
                    except OcrUnavailable as exc:
                        logger.warning("OCR engine unavailable: %s", exc)
                        self._attached = False
                        self._set_problem("ocr_no_engine")
                        engine = None
                        pause(5.0)
                        continue
                    differ.reset()

                started = time.monotonic()
                try:
                    frame = capture.grab(region)
                except Exception:  # noqa: BLE001 - a lost display must not kill the thread
                    logger.exception("OCR capture failed; will retry")
                    frame = None
                    with contextlib.suppress(Exception):
                        capture.stop()
                    capture = None

                if frame is None or frame.size == 0:
                    self._attached = False  # still waiting for the first frame
                    pause(settings.interval)
                    continue

                self._attached = True
                sig = _signature(frame)

                if float(sig.max()) < 3.0:
                    black_run += 1
                    if black_run >= _BLACK_FRAMES_BEFORE_WARNING:
                        self._set_problem("ocr_black")
                    pause(settings.interval)
                    continue
                black_run = 0
                self._clear_problem("ocr_black")

                unchanged = last_sig is not None and not _changed(sig, last_sig)
                overdue = time.monotonic() - last_read >= _MAX_SECONDS_WITHOUT_READING
                if unchanged and not overdue:
                    pause(settings.interval)
                    continue
                last_sig = sig
                last_read = time.monotonic()

                try:
                    boxes = engine.recognise(prepare(frame))
                except Exception:  # noqa: BLE001
                    logger.exception("OCR recognition failed on this frame")
                    pause(settings.interval)
                    continue

                lines = boxes_to_lines(boxes)
                self._last_lines = lines
                entries = frame_from_lines(lines)
                if not first_read_logged:
                    # Counts only: the console is where a report gets pasted from,
                    # and the chat itself stays out of it (see debug_log).
                    first_read_logged = True
                    logger.info(
                        "OCR source: first read, %d lines, %d recognised as chat",
                        len(lines), sum(1 for e in entries if e.msg is not None),
                    )

                # Several lines of text and none of them chat: say so, rather than
                # leaving a working reader that never produces anything.
                if any(e.msg is not None for e in entries):
                    blind_passes = 0
                    self._clear_problem("ocr_unrecognised")
                elif len(lines) >= _BLIND_MIN_LINES:
                    blind_passes += 1
                    if blind_passes >= _BLIND_PASSES_BEFORE_WARNING:
                        self._set_problem("ocr_unrecognised")

                was_primed = differ.primed
                new_messages = differ.update(entries)
                if not was_primed and not history_sent:
                    history_sent = True
                    self._send_history(differ)
                if new_messages:
                    logger.debug("OCR source: %d new message(s) from the screen", len(new_messages))
                for message in new_messages:
                    log_line = message.to_log_line()
                    if log_line:
                        try:
                            self._on_new_line(log_line)
                        except Exception:  # noqa: BLE001
                            logger.exception("pipeline rejected an OCR line")

                took = time.monotonic() - started
                pause(max(settings.interval, took * 2.0))
        finally:
            if capture is not None:
                try:
                    capture.stop()
                except Exception:  # noqa: BLE001
                    logger.debug("capture stop failed", exc_info=True)
            self._attached = False


def read_region_once(
    settings: OcrSettings,
    wait_for_frame: float = 6.0,
    on_token: Callable[[str], None] | None = None,
) -> list[str]:
    """The text lines one read of the region produced."""
    return read_region_report(settings, wait_for_frame, on_token).lines


def read_region_report(
    settings: OcrSettings,
    wait_for_frame: float = 6.0,
    on_token: Callable[[str], None] | None = None,
) -> ReadReport:
    """One read of the configured region, for the "test this area" button.

    Raises CaptureUnavailable / OcrUnavailable with a message fit to show.
    """
    if settings.region is None:
        raise CaptureUnavailable("no area selected")
    capture = create_capture(settings.restore_token, on_token)
    capture.start()
    try:
        deadline = time.monotonic() + wait_for_frame
        frame = capture.grab(settings.region)
        while frame is None and time.monotonic() < deadline:
            time.sleep(0.2)
            frame = capture.grab(settings.region)
        if frame is None:
            raise CaptureUnavailable("no picture arrived from the screen")
        engine = create_engine(settings.script, settings.engine)
        lines = boxes_to_lines(engine.recognise(prepare(frame)))
        return ReadReport(lines=lines, entries=frame_from_lines(lines))
    finally:
        capture.stop()


__all__ = ["OcrChatWatcher", "OcrSettings", "ReadReport", "read_region_once", "read_region_report"]
