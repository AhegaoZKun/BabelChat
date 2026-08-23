"""Read WoW chat messages from addon's in-memory buffer — Windows/Rust version.

Uses babelchat_scanner_win.dll (compiled Rust) for memory scanning via
ReadProcessMemory + VirtualQueryEx. The DLL exposes:

    int32_t find_and_read_buffer(int32_t pid, int32_t min_seq, char* out, int32_t out_len)

Fast path: single ReadProcessMemory at cached address (~microseconds, near-zero CPU).
Slow path: parallel region scan on cache miss (~every few seconds on GC relocation).

Falls back to pure-Python pymem scanner if the DLL is not found.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

from app import debug_log
from app.addon_protocol import (
    bare_log_line,
    is_system_noise,
    make_synthetic_log_line,
)

# Re-exported deliberately: ACCESS_DENIED and PROCESS_GONE are this reader's
# vocabulary even though the scanner is what produces them, and the overlay and
# the tests read them from here.
from app.memory_scan_windows import (  # noqa: F401
    ACCESS_DENIED,
    NO_BUFFER,
    PROCESS_GONE,
    WOW_PROCESS_NAMES,
    _find_wow_pid,
    _pymem_find_buffer,
    _rust_find_buffer,
    _rust_lib,
    describe_access,
)

logger = logging.getLogger(__name__)

#: How long a buffer may be absent before the app stops looking busy and says
#: so. Long enough to cover a character-select screen and a zone load, short
#: enough that nobody spends an evening guessing.
_SILENCE_BEFORE_COMPLAINT = 45.0

POLL_INTERVAL = 0.25
ATTACH_RETRY_INTERVAL = 5.0
SCAN_RETRY_INTERVAL = 2.0
_MAX_DELIVERED_PAYLOADS = 200

# ── Main reader class ─────────────────────────────────────────────────────────


class WoWAddonBufReader:
    """Reads chat messages from WoW addon buffer — Windows/Rust version.

    Uses babelchat_scanner_win.dll for fast memory scanning.
    Falls back to pure-Python pymem scanner if DLL not found.
    """

    def __init__(self, on_new_line: Callable[..., None]) -> None:
        self._on_new_line = on_new_line
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid: int | None = None
        self._attached = False
        #: Why nothing is arriving, when the answer is not "WoW is not running".
        #: Read by the overlay, which used to have no way to tell a working
        #: reader from a refused one.
        self._problem: str = ""
        self._first_miss_at: float = 0.0
        self._last_seq = 0
        self._player_name: str = ""
        self._delivered_payloads: set[str] = set()
        self._pre_reset_texts: set[str] = set()
        self._pre_reset_expire: float = 0.0
        self._last_new_msg_time: float = 0.0
        self._consecutive_misses: int = 0

    @property
    def is_attached(self) -> bool:
        return self._attached

    @property
    def problem(self) -> str:
        """ "" when nothing is wrong, otherwise why no message is arriving."""
        return self._problem

    @property
    def player_name(self) -> str:
        return self._player_name

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        mode = "Rust DLL" if _rust_lib else "pymem fallback"
        logger.info("Addon buffer reader thread started (Windows/%s)", mode)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Addon buffer reader stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._attached:
                try:
                    self._attach()
                except Exception as e:
                    logger.info("Cannot attach to WoW: %s", e)
                    self._stop_event.wait(ATTACH_RETRY_INTERVAL)
                    continue

            try:
                self._poll()
            except Exception as e:
                logger.warning("Poll error: %s", e)
                if not self._is_process_alive():
                    logger.info("WoW process gone, detaching")
                    self._detach()
                    continue

            self._stop_event.wait(POLL_INTERVAL)

    def _attach(self) -> None:
        pid = _find_wow_pid()
        if pid is None:
            self._problem = ""  # WoW simply is not running; that is not a fault
            raise RuntimeError("WoW process not found")

        # Finding the process is not the same as being allowed to read it, and
        # treating them as one is what let the overlay show a green tick beside
        # a reader that could never deliver a message.
        refusal = describe_access(pid)
        if refusal:
            self._problem = refusal
            raise RuntimeError(refusal)

        self._pid = pid
        self._attached = True
        self._problem = ""
        self._consecutive_misses = 0
        self._first_miss_at = 0.0
        logger.info("Attached to WoW PID %d", pid)

    def _detach(self) -> None:
        self._pid = None
        self._attached = False
        self._last_seq = 0

    def _is_process_alive(self) -> bool:
        if self._pid is None:
            return False
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, self._pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        return False

    def _poll(self) -> None:
        if self._pid is None:
            return

        if _rust_lib:
            content = _rust_find_buffer(self._pid, self._last_seq)
        else:
            content = _pymem_find_buffer(self._pid, self._last_seq)

        if content is None:
            self._consecutive_misses += 1
            # Attached, allowed to read, and still nothing there. That means the
            # addon is not writing its buffer — disabled, not installed, or not
            # loaded for this character — and no amount of waiting fixes it. Say
            # so rather than looking busy.
            now = time.monotonic()
            if self._first_miss_at == 0.0:
                self._first_miss_at = now
            elif not self._problem and now - self._first_miss_at > _SILENCE_BEFORE_COMPLAINT:
                self._problem = NO_BUFFER
                logger.warning(
                    "No addon buffer in WoW's memory after %.0fs — is the addon enabled for this character?",
                    now - self._first_miss_at,
                )
            return

        self._consecutive_misses = 0
        self._first_miss_at = 0.0
        self._problem = ""
        self._deliver_new_messages(content)

    def _deliver_new_messages(self, content: str) -> None:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return

        max_seq_in_buf = 0
        for line in lines:
            parts = line.split("|", 1)
            if parts:
                try:
                    s = int(parts[0])
                    if s > max_seq_in_buf:
                        max_seq_in_buf = s
                except ValueError:
                    pass

        if max_seq_in_buf > 0 and max_seq_in_buf < self._last_seq:
            logger.info(
                "Seq reset detected (buf max=%d, last_seq=%d) — resetting",
                max_seq_in_buf,
                self._last_seq,
            )
            self._pre_reset_texts = set(self._delivered_payloads)
            self._pre_reset_expire = time.monotonic() + 60.0
            self._last_seq = 0

        new_count = 0
        for line in lines:
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            try:
                seq = int(parts[0])
            except ValueError:
                continue

            kind = parts[1]
            payload = parts[2]

            if kind == "META":
                meta_parts = payload.split("|", 1)
                if meta_parts[0] == "PLAYER" and len(meta_parts) > 1:
                    name = meta_parts[1].strip()
                    if name and name != self._player_name:
                        self._player_name = name
                        logger.info("Player name from addon: %s", name)
                continue

            if seq <= self._last_seq:
                continue
            self._last_seq = seq

            if self._pre_reset_texts and payload[:200] in self._pre_reset_texts:
                continue

            new_count += 1
            self._delivered_payloads.add(payload[:200])
            if len(self._delivered_payloads) > _MAX_DELIVERED_PAYLOADS:
                self._delivered_payloads.clear()

            nul_pos = payload.find("\x00")
            if nul_pos != -1:
                payload = payload[:nul_pos]
            payload = payload.rstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08")
            if not payload.strip():
                continue

            if kind in ("RAW", "DICT"):
                event = ""
                author = ""
                msg_text = payload

                # RAW and DICT carry identical fields; `kind` only records
                # whether the addon also glossed the line in chat. The gloss
                # itself is no longer transmitted — this pipeline discards it
                # regardless (see _on_new_line), and the newline DictEngine
                # embedded in it used to split the record in half.
                sub_parts = payload.split("|", 2)
                if len(sub_parts) >= 3:
                    event = sub_parts[0]
                    author = sub_parts[1]
                    msg_text = sub_parts[2]
                    # Addon 3.3.0 and earlier appended the gloss after a tab; 3.4.0
                    # does not.
                    # The addon is installed by hand, so an app updated ahead of
                    # it still receives those records; keeping the tail would
                    # send the gloss to the translation API and print it in the
                    # overlay. Current records never contain a tab — the addon
                    # strips them — so this only ever fires on a legacy buffer.
                    tab = msg_text.find("	")
                    if tab != -1:
                        msg_text = msg_text[:tab]

                debug_log.record(seq, kind, event, author, msg_text)

                if is_system_noise(msg_text):
                    continue

                msg_text = re.sub(r"^\d{1,2}:\d{2}:\d{2}\s+", "", msg_text)

                if event:
                    log_line = make_synthetic_log_line(event, author, msg_text)
                    if not log_line:
                        log_line = bare_log_line(msg_text)
                else:
                    log_line = bare_log_line(msg_text)

                self._on_new_line(log_line, dict_translated=(kind == "DICT"))

        if self._pre_reset_texts and time.monotonic() > self._pre_reset_expire:
            self._pre_reset_texts.clear()

        if new_count > 0:
            self._last_new_msg_time = time.monotonic()


class MemoryChatWatcher:
    """High-level watcher: bridges WoWAddonBufReader to the pipeline."""

    def __init__(self, on_new_line: Callable[..., None]) -> None:
        self._on_new_line = on_new_line
        self._reader = WoWAddonBufReader(on_new_line=on_new_line)

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    @property
    def is_attached(self) -> bool:
        return self._reader.is_attached

    @property
    def problem(self) -> str:
        return self._reader.problem

    @property
    def player_name(self) -> str:
        return self._reader.player_name
