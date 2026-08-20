"""Read WoW chat messages from addon's in-memory buffer — Windows/Rust version.

Uses babelchat_scanner_win.dll (compiled Rust) for memory scanning via
ReadProcessMemory + VirtualQueryEx. The DLL exposes:

    int32_t find_and_read_buffer(int32_t pid, int32_t min_seq, char* out, int32_t out_len)

Fast path: single ReadProcessMemory at cached address (~microseconds, near-zero CPU).
Slow path: parallel region scan on cache miss (~every few seconds on GC relocation).

Falls back to pure-Python pymem scanner if the DLL is not found.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import pathlib
import re
import sys
import threading
import time
from collections.abc import Callable

from app.addon_protocol import (
    MARKER_END,
    bare_log_line,
    extract_max_seq,
    find_content_start,
    is_system_noise,
    make_synthetic_log_line,
)
from app.native_scanner import load_scanner

logger = logging.getLogger(__name__)

# ── DLL loading ───────────────────────────────────────────────────────────────

_rust_lib: ctypes.CDLL | None = load_scanner()
_OUT_BUF_SIZE = 131072  # 128KB

# ── Constants ─────────────────────────────────────────────────────────────────

POLL_INTERVAL = 0.25
ATTACH_RETRY_INTERVAL = 5.0
SCAN_RETRY_INTERVAL = 2.0
MAX_BUF_READ = 65536

RAW_LOG_FILE = str(
    (pathlib.Path.home() / "babelchat_raw.log") if getattr(sys, "frozen", False) else pathlib.Path("babelchat_raw.log")
)

WOW_PROCESS_NAMES = ["Wow.exe", "WowT.exe", "WowB.exe"]
_MAX_DELIVERED_PAYLOADS = 200

# ── Process discovery ─────────────────────────────────────────────────────────


def _find_wow_pid() -> int | None:
    """Find WoW PID using Windows EnumProcesses / tasklist fallback."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).decode("utf-8", errors="replace")
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                name = parts[0]
                if name in WOW_PROCESS_NAMES:
                    try:
                        return int(parts[1])
                    except ValueError:
                        continue
    except Exception:
        pass

    # Fallback: pymem's process lookup. Only the PID is wanted here, so the
    # handle it opens is closed immediately — `Pymem(name)` asks for
    # PROCESS_ALL_ACCESS, and holding that open is what made the app look like
    # it needed administrator rights.
    try:
        import pymem
        import pymem.exception

        for proc_name in WOW_PROCESS_NAMES:
            try:
                pm = pymem.Pymem(proc_name)
                pid = pm.process_id
                pm.close_process()
                return pid
            except pymem.exception.ProcessNotFound:
                continue
            except pymem.exception.PymemError:
                # Opening with full access can fail without elevation. The name
                # matched, so fall through to the reader, which opens the
                # process with read-only rights and does not need elevation.
                continue
    except ImportError:
        pass

    return None


# ── Rust scanner call ─────────────────────────────────────────────────────────


def _rust_find_buffer(pid: int, min_seq: int) -> str | None:
    if _rust_lib is None:
        return None
    buf = ctypes.create_string_buffer(_OUT_BUF_SIZE)
    n = _rust_lib.find_and_read_buffer(pid, min_seq, buf, _OUT_BUF_SIZE)
    if n <= 0:
        return None
    return buf.raw[:n].decode("utf-8", errors="replace")


# ── Pure-Python fallback scanner ──────────────────────────────────────────────


def _open_for_reading(pid: int):
    """Open the game process with the least rights that can read its memory.

    `pymem.Pymem(name)` opens PROCESS_ALL_ACCESS, which wants SeDebugPrivilege
    and is why the app asked to run as administrator at all. Reading another
    process owned by the same user needs only these two rights, which Windows
    grants from the target's own DACL — no elevation involved.
    """
    import pymem
    import pymem.ressources.kernel32

    process_vm_read = 0x0010
    process_query_information = 0x0400

    handle = pymem.ressources.kernel32.OpenProcess(process_vm_read | process_query_information, False, pid)
    if not handle:
        return None

    pm = pymem.Pymem()
    pm.process_id = pid
    pm.process_handle = handle
    return pm


def _pymem_find_buffer(pid: int, min_seq: int) -> str | None:
    """Fallback: use pymem if the native scanner is not available."""
    try:
        import pymem.pattern

        pm = _open_for_reading(pid)
        if pm is None:
            return None

        addrs = pymem.pattern.pattern_scan_all(
            pm.process_handle,
            rb"__WCT_BUF_",
            return_multiple=True,
        )
        best_content = None
        best_seq = min_seq
        for a in addrs or []:
            try:
                raw = pm.read_bytes(a, MAX_BUF_READ)
            except Exception:
                continue
            co = find_content_start(raw)
            if co == -1:
                continue
            end_idx = raw.find(MARKER_END, co)
            if end_idx == -1:
                continue
            content = raw[co:end_idx]
            seq = extract_max_seq(content)
            if seq > best_seq:
                best_seq = seq
                best_content = content.decode("utf-8", errors="replace")
        pm.close_process()
        return best_content
    except Exception:
        return None


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
    def player_name(self) -> str:
        return self._player_name

    def start(self) -> None:
        with contextlib.suppress(OSError):
            open(RAW_LOG_FILE, "w").close()
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
            raise RuntimeError("WoW process not found")
        self._pid = pid
        self._attached = True
        self._consecutive_misses = 0
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
            return

        self._consecutive_misses = 0
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
                    # Addon 3.3.0 and earlier appended the gloss after a tab.
                    # The addon is installed by hand, so an app updated ahead of
                    # it still receives those records; keeping the tail would
                    # send the gloss to the translation API and print it in the
                    # overlay. Current records never contain a tab — the addon
                    # strips them — so this only ever fires on a legacy buffer.
                    tab = msg_text.find("	")
                    if tab != -1:
                        msg_text = msg_text[:tab]

                try:
                    with open(RAW_LOG_FILE, "a", encoding="utf-8") as f:
                        t = time.localtime()
                        ts = f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"
                        f.write(f"[{ts}] #{seq} [{kind}] {event}|{author}|{msg_text}\n")
                except OSError:
                    pass

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
    def player_name(self) -> str:
        return self._reader.player_name
