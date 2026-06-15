"""memory_reader_linux.py — Linux memory reader using Rust scanner.

Replaces the pure-Python scanner with a call into libbabelchat_scanner.so,
which scans /proc/<pid>/mem in compiled Rust with real threads and no GIL.

The Rust library exposes:
    int32_t find_and_read_buffer(int32_t pid, int32_t min_seq, char* out, int32_t out_len)

All the slow parts (region enumeration, chunk scanning, marker search) run in
Rust. Python handles sequencing, message parsing, and delivery — unchanged.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import pathlib
import re
import sys
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Rust library loading ──────────────────────────────────────────────────────

_LIB_NAMES = [
    "libbabelchat_scanner.so",
    # Common alternative locations
    str(pathlib.Path(__file__).parent / "libbabelchat_scanner.so"),
    str(pathlib.Path(__file__).parent.parent / "libbabelchat_scanner.so"),
]


def _load_rust_lib() -> ctypes.CDLL | None:
    for name in _LIB_NAMES:
        try:
            lib = ctypes.CDLL(name)
            lib.find_and_read_buffer.restype = ctypes.c_int32
            lib.find_and_read_buffer.argtypes = [
                ctypes.c_int32,  # pid
                ctypes.c_int32,  # min_seq
                ctypes.c_char_p,  # out_buf
                ctypes.c_int32,  # out_len
            ]
            logger.info("Loaded Rust scanner: %s", name)
            return lib
        except OSError:
            continue
    return None


_rust_lib: ctypes.CDLL | None = _load_rust_lib()
_OUT_BUF_SIZE = 131072  # 128KB output buffer

# ── Constants (kept for compatibility) ────────────────────────────────────────

MARKER_START = b"__WCT_BUF_"
MARKER_END = b"__WCT_END__"
POLL_INTERVAL = 0.25
ATTACH_RETRY_INTERVAL = 5.0
SCAN_RETRY_INTERVAL = 2.0
MAX_BUF_READ = 65536

RAW_LOG_FILE = str(
    (pathlib.Path.home() / "babelchat_raw.log") if getattr(sys, "frozen", False) else pathlib.Path("babelchat_raw.log")
)

WOW_PROCESS_NAMES = ["Wow.exe", "WowT.exe", "WowB.exe"]
_MAX_DELIVERED_PAYLOADS = 200

# ── Fallback pure-Python scanner (if Rust .so not available) ──────────────────


def _find_wow_pid() -> int | None:
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmdline_path = f"/proc/{entry.name}/cmdline"
                with open(cmdline_path, "rb") as f:
                    cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
                for name in WOW_PROCESS_NAMES:
                    tokens = cmdline.lower().split()
                    if any(
                        t == name.lower() or t.endswith(f"/{name.lower()}") or t.endswith(f"\\{name.lower()}")
                        for t in tokens
                    ):
                        return int(entry.name)
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return None


def _is_system_noise(text: str) -> bool:
    t = re.sub(r"^\d{1,2}:\d{2}:\d{2}\s+", "", text.lstrip())
    if t.startswith(("<DBM>", "<BW>", "<WA>", "|TInterface", "[WCT]", "[MoveAny")):
        return True
    if "|Hachievement:" in t:
        return True
    return any(phrase in t for phrase in ("has earned", "achievement", "creates:", "создает:"))


# ── Rust-backed scanner ───────────────────────────────────────────────────────


def _rust_find_buffer(pid: int, min_seq: int) -> str | None:
    """Call Rust scanner to find and read the addon buffer. Returns content string or None."""
    if _rust_lib is None:
        return None
    buf = ctypes.create_string_buffer(_OUT_BUF_SIZE)
    n = _rust_lib.find_and_read_buffer(pid, min_seq, buf, _OUT_BUF_SIZE)
    if n <= 0:
        return None
    return buf.raw[:n].decode("utf-8", errors="replace")


# ── Main reader class ─────────────────────────────────────────────────────────


class WoWAddonBufReader:
    """Reads chat messages from WoW addon's in-memory buffer — Linux/Rust version.

    Uses the Rust scanner library for fast memory scanning. Falls back to
    raising RuntimeError at attach time if the library is not available.
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
        logger.info("Addon buffer reader thread started (Linux/Rust)")

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
                if not os.path.exists(f"/proc/{self._pid}"):
                    logger.info("WoW process gone, detaching")
                    self._detach()
                    continue

            self._stop_event.wait(POLL_INTERVAL)

    def _attach(self) -> None:
        if _rust_lib is None:
            raise RuntimeError(
                "Rust scanner library not found. Build libbabelchat_scanner.so and place it "
                "next to memory_reader_linux.py. See babelchat_scanner/README.md."
            )
        pid = _find_wow_pid()
        if pid is None:
            raise RuntimeError("WoW process not found")
        mem_path = f"/proc/{pid}/mem"
        if not os.access(mem_path, os.R_OK):
            raise RuntimeError(
                f"Cannot read /proc/{pid}/mem — set ptrace_scope: echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope"
            )
        self._pid = pid
        self._attached = True
        self._consecutive_misses = 0
        logger.info("Attached to WoW PID %d (Rust scanner)", pid)

    def _detach(self) -> None:
        self._pid = None
        self._attached = False
        self._last_seq = 0

    def _poll(self) -> None:
        if self._pid is None:
            return

        content = _rust_find_buffer(self._pid, self._last_seq)

        if content is None:
            self._consecutive_misses += 1
            if self._consecutive_misses >= 4:
                logger.debug(
                    "Scanner miss x%d (pid=%d, min_seq=%d)", self._consecutive_misses, self._pid, self._last_seq
                )
            return

        self._consecutive_misses = 0
        self._deliver_new_messages(content)

    def _deliver_new_messages(self, content: str) -> None:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return

        # Seq reset detection
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
                dict_translated_text = ""

                if kind == "DICT":
                    sub_parts = payload.split("|", 2)
                    if len(sub_parts) >= 3:
                        event = sub_parts[0]
                        author = sub_parts[1]
                        text_and_translated = sub_parts[2]
                        if "\t" in text_and_translated:
                            msg_text, dict_translated_text = text_and_translated.split("\t", 1)
                        else:
                            msg_text = text_and_translated
                else:
                    sub_parts = payload.split("|", 2)
                    if len(sub_parts) >= 3:
                        event = sub_parts[0]
                        author = sub_parts[1]
                        msg_text = sub_parts[2]

                try:
                    with open(RAW_LOG_FILE, "a", encoding="utf-8") as f:
                        t = time.localtime()
                        ts = f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"
                        f.write(f"[{ts}] #{seq} [{kind}] {event}|{author}|{msg_text}\n")
                except OSError:
                    pass

                if _is_system_noise(msg_text):
                    continue

                msg_text = re.sub(r"^\d{1,2}:\d{2}:\d{2}\s+", "", msg_text)

                if event and author:
                    log_line = self._make_synthetic_log_line(event, author, msg_text)
                    if not log_line:
                        t = time.localtime()
                        ts = f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"
                        log_line = f"{ts}  {msg_text}"
                else:
                    t = time.localtime()
                    ts = f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"
                    log_line = f"{ts}  {msg_text}"

                if kind == "DICT":
                    self._on_new_line(log_line, dict_translated=True, dict_text=dict_translated_text)
                else:
                    self._on_new_line(log_line)

        if self._pre_reset_texts and time.monotonic() > self._pre_reset_expire:
            self._pre_reset_texts.clear()

        if new_count > 0:
            self._last_new_msg_time = time.monotonic()

    @staticmethod
    def _make_synthetic_log_line(channel: str, author: str, text: str) -> str | None:
        _ADDON_CHANNEL_TO_LOG = {
            "SAY": "Say",
            "YELL": "Yell",
            "PARTY": "Party",
            "PARTY_LEADER": "Party Leader",
            "RAID": "Raid",
            "RAID_LEADER": "Raid Leader",
            "RAID_WARNING": "Raid Warning",
            "GUILD": "Guild",
            "OFFICER": "Officer",
            "INSTANCE_CHAT": "Instance",
            "INSTANCE_CHAT_LEADER": "Instance Leader",
            "CHANNEL": "Say",
            "EMOTE": "Say",
            "BATTLEGROUND": "Instance",
            "BATTLEGROUND_LEADER": "Instance Leader",
        }
        t = time.localtime()
        ts = f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"
        if channel in ("WHISPER", "BN_WHISPER"):
            return f"{ts}  [{author}] whispers: {text}"
        if channel == "WHISPER_INFORM":
            return f"{ts}  To [{author}]: {text}"
        log_channel = _ADDON_CHANNEL_TO_LOG.get(channel)
        if log_channel is None:
            return None
        return f"{ts}  [{log_channel}] {author}: {text}"


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
