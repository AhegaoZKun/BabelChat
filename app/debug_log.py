"""An opt-in trace of everything the addon sends, for diagnosing capture.

This file used to be written unconditionally, and it holds the full text of
every message the capture path sees — whispers and guild chat included. Those
are other people's private conversations, and the people in them never agreed
to have them written to disk on someone else's machine.

It is worth having when capture misbehaves, so it stays; it is off unless the
user turns it on, and it says what it is when they do.
"""

from __future__ import annotations

import logging
import pathlib
import sys
import time

logger = logging.getLogger(__name__)

FILENAME = "babelchat_raw.log"

_HEADER = (
    "# BabelChat capture trace — every chat line the addon sent, in full.\n"
    "# This includes whispers and guild chat from other players.\n"
    "# Turn it off in Settings when you are done, and delete this file.\n"
)

_enabled = False
_path: pathlib.Path | None = None


def default_path() -> pathlib.Path:
    """Beside the executable when frozen, in the working directory otherwise."""
    if getattr(sys, "frozen", False):
        return pathlib.Path.home() / FILENAME
    return pathlib.Path(FILENAME)


def configure(enabled: bool, path: str | pathlib.Path | None = None) -> None:
    """Turn the trace on or off. Off is the default and needs no call.

    Turning it on truncates the file and writes a header explaining what is in
    it. Turning it off leaves the file alone — the user may still want to read
    it — but nothing further is appended.
    """
    global _enabled, _path
    _enabled = bool(enabled)
    _path = pathlib.Path(path) if path else default_path()
    if not _enabled:
        return
    try:
        _path.write_text(_HEADER, encoding="utf-8")
        logger.info("Capture trace enabled: %s", _path)
    except OSError as e:
        logger.warning("Could not start the capture trace at %s: %s", _path, e)
        _enabled = False


def is_enabled() -> bool:
    return _enabled


def record(seq: int, kind: str, event: str, author: str, text: str) -> None:
    """Append one captured line. A no-op unless the trace was switched on."""
    if not _enabled or _path is None:
        return
    t = time.localtime()
    stamp = f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
    try:
        with open(_path, "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] #{seq} [{kind}] {event}|{author}|{text}\n")
    except OSError:
        # A trace that cannot be written is not a reason to lose the message.
        pass
