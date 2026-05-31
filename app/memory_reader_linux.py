"""Read WoW chat messages from addon's in-memory buffer string — Linux implementation.

Uses /proc/<pid>/mem + /proc/<pid>/maps to read Proton/Wine WoW process memory.
Requires ptrace permissions (run as root, or set /proc/sys/kernel/yama/ptrace_scope to 0).

Architecture mirrors the Windows implementation — same tiered scan cascade,
same marker format, same public API. Only the low-level memory access differs.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import struct
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Markers written by the addon into BabelChatDB.wctbuf
MARKER_START = b"__WCT_BUF_"
MARKER_START_LEGACY = b"__WCT_BUF__"
MARKER_END = b"__WCT_END__"

# Polling and retry intervals
POLL_INTERVAL = 0.25
ATTACH_RETRY_INTERVAL = 5.0
SCAN_RETRY_INTERVAL = 2.0
MAX_BUF_READ = 65536

RAW_LOG_FILE = "babelchat_raw.log"

# WoW process names under Wine/Proton
WOW_PROCESS_NAMES = ["Wow.exe", "WowT.exe", "WowB.exe"]

# Compiled marker pattern
_MARKER_PATTERN = re.compile(rb"__WCT_BUF_[\d]{4}__|__WCT_BUF__")

# Neighborhood scan radius
_NEIGHBORHOOD_RADIUS = 16 * 1024 * 1024

# Region history size
_REGION_HISTORY_SIZE = 16

# Adaptive rescan intervals
_RESCAN_INTERVALS = [10.0, 15.0, 20.0, 30.0]

# Max region size to scan (100MB)
_MAX_REGION_SIZE = 512 * 1024 * 1024

# Max delivered payloads for seq reset dedup
_MAX_DELIVERED_PAYLOADS = 200

# Consecutive polls with same seq before declaring buffer frozen
_FROZEN_THRESHOLD = 3


def _find_content_start(raw: bytes) -> int:
    """Find where buffer content starts after the marker header."""
    if raw.startswith(b"__WCT_BUF_"):
        end = raw.find(b"__", 10)
        if end != -1:
            return end + 2
    if raw.startswith(MARKER_START_LEGACY):
        return len(MARKER_START_LEGACY)
    return -1


def _extract_max_seq(content: bytes) -> int:
    """Extract the highest sequence number from buffer content."""
    max_seq = 0
    for line in content.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        idx = line.find(b"|")
        if idx <= 0:
            continue
        try:
            seq = int(line[:idx])
            if seq > max_seq:
                max_seq = seq
        except ValueError:
            continue
    return max_seq


def _is_system_noise(text: str) -> bool:
    """Quick check if AddMessage text is obvious system/addon noise."""
    t = re.sub(r"^\d{1,2}:\d{2}:\d{2}\s+", "", text.lstrip())
    if t.startswith(("<DBM>", "<BW>", "<WA>", "|TInterface", "[WCT]", "[MoveAny")):
        return True
    if "|Hachievement:" in t:
        return True
    if "заслужил" in t and "достижение" in t:
        return True
    if "has earned" in t and "achievement" in t:
        return True
    if " создает: " in t or " creates: " in t:
        return True
    if " производит " in t and " в звание " in t:
        return True
    if t.startswith(("Вы превращаете", "You convert")):
        return True
    if t.startswith((
        "Вы не состоите", "You are not in",
        "Смена канала", "Channel ",
        "Вы покинули канал", "You left channel",
        "Ведите себя", "Please keep",
        "Сообщение дня от гильдии", "Guild Message of the Day",
    )):
        return True
    if " ставит маяк " in t or " получает добычу" in t:
        return True
    if " получает предмет" in t or " receives loot" in t:
        return True
    if " засыпает." in t or " очищает " in t or " освобождает " in t:
        return True
    if " находит что-то " in t or " в панике пытается бежать" in t:
        return True
    return bool(t.startswith(("Получено:", "You receive")))


def _find_wow_pid() -> int | None:
    """Find the PID of a running WoW process under Wine/Proton.

    Scans /proc for processes whose cmdline matches known WoW exe names.
    Returns PID or None if not found.
    """
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmdline_path = f"/proc/{entry.name}/cmdline"
                with open(cmdline_path, "rb") as f:
                    cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
                for name in WOW_PROCESS_NAMES:
                    # Use word-boundary match to avoid matching WowVoiceProxy.exe etc.
                    # Split cmdline on spaces/nulls and check if any token ends with the name
                    tokens = cmdline.lower().split()
                    if any(token == name.lower() or token.endswith(f"/{name.lower()}") or token.endswith(f"\\{name.lower()}") for token in tokens):
                        return int(entry.name)
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return None


def _get_readable_regions(pid: int) -> list[tuple[int, int]]:
    """Parse /proc/<pid>/maps and return readable, non-special memory regions.

    Returns list of (base_address, size) sorted by base address.
    Wine/Proton heap regions are typically r--, rw-, or r-x.
    We skip special mappings (vvar, vdso, vsyscall) and huge regions.
    """
    regions: list[tuple[int, int]] = []
    maps_path = f"/proc/{pid}/maps"
    try:
        with open(maps_path) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 5:
                    continue
                addr_range, perms = parts[0], parts[1]
                # Must be readable
                if perms[0] != "r":
                    continue
                # Skip special kernel regions
                name = parts[5] if len(parts) > 5 else ""
                if name in ("[vvar]", "[vdso]", "[vsyscall]"):
                    continue
                # Parse address range
                try:
                    start_s, end_s = addr_range.split("-")
                    start = int(start_s, 16)
                    end = int(end_s, 16)
                    size = end - start
                except ValueError:
                    continue
                if size <= 0 or size > _MAX_REGION_SIZE:
                    continue
                regions.append((start, size))
    except OSError:
        pass
    regions.sort(key=lambda r: r[0])
    return regions


def _read_process_memory(pid: int, address: int, size: int) -> bytes | None:
    """Read memory from /proc/<pid>/mem using os.pread() for 64-bit address support.

    Opens a fresh fd on every call to avoid stale handle issues.
    """
    try:
        fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
        try:
            data = os.pread(fd, size, address)
            return data if data else None
        finally:
            os.close(fd)
    except (OSError, OverflowError) as e:
        pass  # EIO on unreadable regions is expected
        return None


def _close_mem_fd(pid: int) -> None:
    """No-op: kept for API compatibility."""
    pass


# Yield interval: sleep every N regions during scan to avoid starving WoW's CPU
_SCAN_YIELD_INTERVAL = 50
_SCAN_YIELD_SLEEP = 0.002  # 2ms yield every 50 regions


def _scan_region_batch(
    pid: int,
    regions: list[tuple[int, int]],
    min_seq: int = 0,
) -> tuple[int, int]:
    """Scan a batch of memory regions for the best (highest seq) marker."""
    best_addr = 0
    best_seq = -1

    for i, (base, size) in enumerate(regions):
        # Yield CPU every N regions to avoid starving WoW
        if i > 0 and i % _SCAN_YIELD_INTERVAL == 0:
            time.sleep(_SCAN_YIELD_SLEEP)
        # Two-phase: read 4KB to find marker, then full buffer only if found
        probe = _read_process_memory(pid, base, min(size, 4096))
        if probe is None:
            continue
        has_marker = MARKER_START in probe or MARKER_START_LEGACY in probe
        if not has_marker:
            continue
        # Marker found in probe — read full region
        raw = _read_process_memory(pid, base, size)
        if raw is None:
            continue

        matches = list(_MARKER_PATTERN.finditer(raw))

        for match in matches:
            content_start = match.start()
            remaining = len(raw) - content_start
            chunk = raw[content_start:content_start + min(remaining, MAX_BUF_READ)]

            content_offset = _find_content_start(chunk)
            if content_offset == -1:
                continue
            marker_end = chunk.find(MARKER_END, content_offset)
            if marker_end == -1:
                continue

            content = chunk[content_offset:marker_end]
            max_seq = _extract_max_seq(content)
            if max_seq > best_seq and max_seq > min_seq:
                best_seq = max_seq
                best_addr = base + match.start()

    return best_addr, best_seq


def _scan_regions_for_marker(
    pid: int,
    regions: list[tuple[int, int]],
    min_seq: int = 0,
) -> int:
    """Scan memory regions for the best marker (single-threaded).

    Parallel scanning is avoided on Linux because each thread opens its own
    /proc/<pid>/mem fd, which causes race conditions and EIO errors under
    some kernel configurations. The two-phase probe (4KB first) makes
    single-threaded scanning fast enough.
    """
    addr, _seq = _scan_region_batch(pid, regions, min_seq)
    return addr


class WoWAddonBufReader:
    """Reads chat messages from the WoW addon's in-memory buffer — Linux version.

    Public API identical to the Windows implementation.
    Uses /proc/<pid>/mem instead of ReadProcessMemory.
    """

    def __init__(self, on_new_line: Callable[..., None]) -> None:
        self._on_new_line = on_new_line
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid: int | None = None
        self._attached = False
        self._last_seq = 0
        self._player_name: str = ""

        self._buf_addr: int = 0
        self._cached_region: tuple[int, int] | None = None
        self._all_regions: list[tuple[int, int]] = []
        self._cached_region_index: int = -1
        self._region_history: list[tuple[int, int]] = []

        self._stale_count: int = 0
        self._stale_tier: int = 0

        self._seq_history: list[int] = []
        self._frozen_count: int = 0

        self._blacklisted_addrs: dict[int, float] = {}
        self._blacklist_ttl: float = 60.0

        self._delivered_payloads: set[str] = set()
        self._pre_reset_texts: set[str] = set()
        self._pre_reset_expire: float = 0.0

        self._last_rescan: float = 0.0
        self._rescan_interval: float = 10.0
        self._same_addr_count: int = 0

        self._last_new_msg_time: float = 0.0
        self._ptr_addr: int = 0
        self._ptr_offset: int = 0

    def _is_blacklisted(self, addr: int) -> bool:
        if addr not in self._blacklisted_addrs:
            return False
        if time.monotonic() > self._blacklisted_addrs[addr]:
            del self._blacklisted_addrs[addr]
            return False
        return True

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
        logger.info("Addon buffer reader thread started (Linux)")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._detach()
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

            if self._buf_addr == 0:
                try:
                    found = self._find_marker()
                except Exception as e:
                    logger.warning("Marker scan error: %s", e)
                    if not self._is_process_alive():
                        logger.info("WoW process gone, detaching")
                        self._detach()
                    self._stop_event.wait(SCAN_RETRY_INTERVAL)
                    continue

                if not found:
                    self._stop_event.wait(SCAN_RETRY_INTERVAL)
                    continue

            try:
                self._poll_buffer()
            except Exception as e:
                logger.warning("Buffer read error: %s", e)
                if not self._is_process_alive():
                    logger.info("WoW process gone, detaching")
                    self._detach()
                    continue

            self._stop_event.wait(POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Process attach/detach
    # ------------------------------------------------------------------

    def _attach(self) -> None:
        """Find WoW PID via /proc and attach."""
        pid = _find_wow_pid()
        if pid is None:
            raise RuntimeError("WoW process not found")

        # Verify we can actually read memory
        test = _read_process_memory(pid, 0, 1)
        # test may be None if address 0 is unmapped — that's fine,
        # we just need to confirm /proc/<pid>/mem is openable.
        mem_path = f"/proc/{pid}/mem"
        if not os.access(mem_path, os.R_OK):
            raise RuntimeError(
                f"Cannot read /proc/{pid}/mem — run as root or set "
                "ptrace_scope: echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope"
            )

        self._pid = pid
        self._attached = True
        # Verify we can open /proc/<pid>/mem
        try:
            fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
            os.close(fd)
        except OSError as e:
            raise RuntimeError(f"Cannot open /proc/{pid}/mem: {e}") from e
        self._all_regions = _get_readable_regions(pid)
        logger.info(
            "Attached to WoW PID %d (%d readable regions)",
            pid, len(self._all_regions),
        )

    def _detach(self) -> None:
        if self._pid is not None:
            _close_mem_fd(self._pid)
        self._pid = None
        self._attached = False
        self._buf_addr = 0
        self._cached_region = None
        self._cached_region_index = -1
        self._all_regions = []
        self._stale_count = 0
        self._ptr_addr = 0
        self._ptr_offset = 0

    def _is_process_alive(self) -> bool:
        if self._pid is None:
            return False
        return os.path.exists(f"/proc/{self._pid}")

    # ------------------------------------------------------------------
    # Memory region enumeration
    # ------------------------------------------------------------------

    def _get_memory_regions(self) -> list[tuple[int, int]]:
        """Return cached memory regions. Use _refresh_regions() to update."""
        return self._all_regions

    def _refresh_regions(self) -> None:
        """Re-read /proc/<pid>/maps and update the cached region list."""
        if self._pid is None:
            return
        self._all_regions = _get_readable_regions(self._pid)

    def _find_region_for_addr(self, addr: int) -> tuple[int, int, int] | None:
        for i, (base, size) in enumerate(self._all_regions):
            if base <= addr < base + size:
                return base, size, i
        return None

    # ------------------------------------------------------------------
    # Region history
    # ------------------------------------------------------------------

    def _record_region_hit(self, base: int, size: int) -> None:
        entry = (base, size)
        if entry in self._region_history:
            self._region_history.remove(entry)
        self._region_history.insert(0, entry)
        if len(self._region_history) > _REGION_HISTORY_SIZE:
            self._region_history.pop()

    def _update_cached_region(self, addr: int) -> None:
        region_info = self._find_region_for_addr(addr)
        if region_info:
            base, size, idx = region_info
            self._cached_region = (base, size)
            self._cached_region_index = idx

    def _record_hit_from_addr(self, addr: int) -> None:
        region_info = self._find_region_for_addr(addr)
        if region_info:
            base, size, idx = region_info
            self._cached_region = (base, size)
            self._cached_region_index = idx
            self._record_region_hit(base, size)

    def _accept_marker(self, addr: int) -> bool:
        if not addr or self._is_blacklisted(addr):
            return False
        self._buf_addr = addr
        self._stale_count = 0
        self._stale_tier = 0
        self._record_hit_from_addr(addr)
        self._maybe_skip_existing(addr)
        return True

    # ------------------------------------------------------------------
    # Marker scanning
    # ------------------------------------------------------------------

    def _scan_cached_region(self, min_seq: int = 0) -> int:
        if self._pid is None or not self._cached_region:
            return 0
        t0 = time.monotonic()
        addr = _scan_regions_for_marker(self._pid, [self._cached_region], min_seq=min_seq)
        elapsed = time.monotonic() - t0
        if addr and addr not in self._blacklisted_addrs:
            logger.info("Cached region scan HIT: marker at 0x%X (%.0fms)", addr, elapsed * 1000)
            return addr
        logger.debug("Cached region scan MISS (%.0fms)", elapsed * 1000)
        return 0

    def _fast_relocate_buffer(self, min_seq: int = 0) -> int:
        if self._pid is None:
            return 0
        addr = self._scan_cached_region(min_seq=min_seq)
        if addr and addr != self._buf_addr:
            return addr
        if self._buf_addr:
            addr = self._neighborhood_scan(self._buf_addr, min_seq=min_seq)
            if addr and addr not in self._blacklisted_addrs and addr != self._buf_addr:
                return addr
        if self._region_history:
            addr = _scan_regions_for_marker(self._pid, self._region_history, min_seq=min_seq)
            if addr and addr not in self._blacklisted_addrs and addr != self._buf_addr:
                return addr
        return 0

    def _find_marker(self, min_seq: int = 0) -> bool:
        if min_seq == 0 and self._last_seq > 0:
            min_seq = max(0, self._last_seq - 10)
        if self._pid is None:
            return False

        if self._cached_region:
            addr = self._scan_cached_region(min_seq=min_seq)
            if self._accept_marker(addr):
                return True

        if self._region_history:
            t0 = time.monotonic()
            addr = _scan_regions_for_marker(self._pid, self._region_history, min_seq=min_seq)
            elapsed = time.monotonic() - t0
            if self._accept_marker(addr):
                logger.info("History scan HIT: marker at 0x%X (%.0fms)", addr, elapsed * 1000)
                return True
            logger.info("History scan MISS (%.0fms)", elapsed * 1000)

        if self._buf_addr:
            t0 = time.monotonic()
            addr = self._neighborhood_scan(self._buf_addr, min_seq=min_seq)
            elapsed = time.monotonic() - t0
            if self._accept_marker(addr):
                logger.info("Neighborhood scan HIT: 0x%X (%.0fms)", addr, elapsed * 1000)
                return True
            logger.info("Neighborhood scan MISS (%.0fms)", elapsed * 1000)

        self._refresh_regions()
        t0 = time.monotonic()
        addr = self._scan_heap_regions(min_seq=min_seq)
        elapsed = time.monotonic() - t0
        if self._accept_marker(addr):
            logger.info("Heap scan HIT: marker at 0x%X (%.1fs)", addr, elapsed)
            return True
        logger.info("Heap scan MISS (%.1fs)", elapsed)

        t0 = time.monotonic()
        addr = self._full_marker_scan(min_seq=min_seq)
        elapsed = time.monotonic() - t0
        if self._accept_marker(addr):
            logger.info("Full scan: marker at 0x%X (%.1fs)", addr, elapsed)
            return True

        logger.warning("All scans failed: marker not found (%.1fs total)", elapsed)
        return False

    def _maybe_skip_existing(self, addr: int) -> None:
        if self._last_seq != 0 or self._pid is None:
            return
        try:
            raw = _read_process_memory(self._pid, addr, MAX_BUF_READ)
            if raw is None:
                return
            co = _find_content_start(raw)
            if co != -1:
                end_idx = raw.find(MARKER_END, co)
                if end_idx != -1:
                    content = raw[co:end_idx].decode("utf-8", errors="replace")
                    for line in content.splitlines():
                        parts = line.strip().split("|", 2)
                        if len(parts) >= 3 and parts[1] == "META":
                            meta = parts[2].split("|", 1)
                            if meta[0] == "PLAYER" and len(meta) > 1:
                                name = meta[1].strip()
                                if name:
                                    self._player_name = name
                                    logger.info("Player name from addon: %s", name)
                    max_seq = _extract_max_seq(raw[co:end_idx])
                    if max_seq > 0:
                        self._last_seq = max_seq
                        logger.info("Skipping existing buffer (last_seq=%d)", max_seq)
        except Exception:
            pass

    def _full_marker_scan(self, min_seq: int = 0) -> int:
        if self._pid is None:
            return 0
        logger.info("Full scan: searching for addon buffer marker...")
        best_addr = 0
        best_seq = -1
        for base, size in self._all_regions:
            raw = _read_process_memory(self._pid, base, min(size, MAX_BUF_READ))
            if raw is None:
                continue
            for match in _MARKER_PATTERN.finditer(raw):
                a = base + match.start()
                if a in self._blacklisted_addrs:
                    continue
                chunk = raw[match.start():]
                co = _find_content_start(chunk)
                if co == -1:
                    continue
                end_idx = chunk.find(MARKER_END, co)
                if end_idx == -1:
                    continue
                ms = _extract_max_seq(chunk[co:end_idx])
                if ms > best_seq and ms > min_seq:
                    best_seq = ms
                    best_addr = a
        if best_addr:
            logger.info("Full scan: best marker at 0x%X (max_seq=%d)", best_addr, best_seq)
        return best_addr

    def _quick_rescan_for_newer_buffer(self) -> None:
        if self._pid is None:
            return
        new_addr = 0
        if self._cached_region:
            new_addr = self._scan_cached_region()
            if new_addr in self._blacklisted_addrs or new_addr == self._buf_addr:
                new_addr = 0
        if not new_addr and self._region_history:
            new_addr = _scan_regions_for_marker(self._pid, self._region_history)
            if new_addr in self._blacklisted_addrs or new_addr == self._buf_addr:
                new_addr = 0
        if new_addr and new_addr != self._buf_addr:
            self._accept_marker(new_addr)
            self._same_addr_count = 0
            self._frozen_count = 0
            self._rescan_interval = _RESCAN_INTERVALS[0]
        else:
            self._same_addr_count += 1
            if self._same_addr_count >= 20 and self._same_addr_count % 10 == 0:
                self._check_for_newer_buffer()
            else:
                tier = min(self._same_addr_count // 3, len(_RESCAN_INTERVALS) - 1)
                self._rescan_interval = _RESCAN_INTERVALS[tier]

    def _check_for_newer_buffer(self) -> None:
        if self._pid is None:
            return

        def _is_rejected(addr: int) -> bool:
            return not addr or self._is_blacklisted(addr) or addr == self._buf_addr

        new_addr = 0
        scan_type = "cached_region"
        if self._cached_region:
            new_addr = self._scan_cached_region()
            if _is_rejected(new_addr):
                new_addr = 0

        if not new_addr:
            scan_type = "history"
            if self._region_history:
                new_addr = _scan_regions_for_marker(self._pid, self._region_history)
                if _is_rejected(new_addr):
                    new_addr = 0

        if not new_addr and self._buf_addr:
            new_addr = self._neighborhood_scan(self._buf_addr)
            if _is_rejected(new_addr):
                new_addr = 0
            scan_type = "neighborhood"

        if not new_addr:
            self._refresh_regions()
            new_addr = self._scan_heap_regions()
            if _is_rejected(new_addr):
                new_addr = 0
            scan_type = "heap"

        if not new_addr and self._same_addr_count >= 5:
            new_addr = self._full_marker_scan(min_seq=self._last_seq)
            if _is_rejected(new_addr):
                new_addr = 0
            scan_type = "full"

        if new_addr and new_addr != self._buf_addr:
            logger.info("Found newer buffer at 0x%X (%s scan)", new_addr, scan_type)
            self._accept_marker(new_addr)
            self._same_addr_count = 0
            self._frozen_count = 0
            self._rescan_interval = _RESCAN_INTERVALS[0]
        else:
            self._same_addr_count += 1
            tier = min(self._same_addr_count // 3, len(_RESCAN_INTERVALS) - 1)
            self._rescan_interval = _RESCAN_INTERVALS[tier]

    def _neighborhood_scan(self, center_addr: int, min_seq: int = 0) -> int:
        if self._pid is None or center_addr == 0:
            return 0
        start = max(0, center_addr - _NEIGHBORHOOD_RADIUS)
        end = center_addr + _NEIGHBORHOOD_RADIUS
        best_addr = 0
        best_seq = -1

        for region in self._all_regions:
            base, size = region
            region_end = base + size
            if region_end < start or base > end:
                continue
            if size > 128 * 1024 * 1024:
                continue
            raw = _read_process_memory(self._pid, base, min(size, MAX_BUF_READ))
            if raw is None:
                continue
            offset = 0
            while offset < len(raw):
                idx = raw.find(b"__WCT_BUF_", offset)
                if idx == -1:
                    break
                chunk = raw[idx:]
                co = _find_content_start(chunk)
                if co == -1:
                    offset = idx + 10
                    continue
                me = chunk.find(MARKER_END, co)
                if me == -1:
                    break
                ms = _extract_max_seq(chunk[co:me])
                if ms > best_seq and ms > min_seq:
                    best_seq = ms
                    best_addr = base + idx
                offset = idx + me

        if best_addr:
            self._record_hit_from_addr(best_addr)
        return best_addr

    def _scan_heap_regions(self, min_seq: int = 0) -> int:
        if self._pid is None:
            return 0
        heap_regions = [(b, s) for b, s in self._all_regions if s <= 128 * 1024 * 1024]
        logger.debug("Heap scan: %d regions (of %d total), min_seq=%d", len(heap_regions), len(self._all_regions), min_seq)
        result = _scan_regions_for_marker(self._pid, heap_regions, min_seq=min_seq)
        logger.debug("Heap scan result: %s", hex(result) if result else "none")
        return result

    # ------------------------------------------------------------------
    # Buffer reading and polling
    # ------------------------------------------------------------------

    def _read_buffer(self) -> str | None:
        if self._pid is None or self._buf_addr == 0:
            return None
        raw = _read_process_memory(self._pid, self._buf_addr, MAX_BUF_READ)
        if raw is None:
            return None
        co = _find_content_start(raw)
        if co == -1:
            return None
        end_idx = raw.find(MARKER_END, co)
        if end_idx == -1:
            return None
        content_bytes = raw[co:end_idx]
        try:
            return content_bytes.decode("utf-8", errors="replace")
        except Exception:
            return None

    def _poll_buffer(self) -> None:
        if self._pid is None or self._buf_addr == 0:
            return

        content = self._read_buffer()
        if content is None:
            self._stale_count += 1
            if self._stale_count == 1:
                self._refresh_regions()
                self._refresh_regions()
                logger.info("Marker gone at 0x%X, trying fast relocate...", self._buf_addr)
                old_addr = self._buf_addr
                new_addr = self._fast_relocate_buffer(min_seq=self._last_seq)
                if new_addr:
                    logger.info("Fast relocate SUCCESS: 0x%X → 0x%X", old_addr, new_addr)
                    self._accept_marker(new_addr)
                    content = self._read_buffer()
                    if content is not None and content.strip():
                        self._deliver_new_messages(content)
                    return
                logger.info("Fast relocate MISS, will try full scan on next cycle")

            stale_threshold = min(2 << self._stale_tier, 16)
            if self._stale_count >= stale_threshold:
                if self._stale_tier >= 3:
                    old_addr = self._buf_addr if self._buf_addr else 0
                    if old_addr:
                        self._blacklisted_addrs[old_addr] = time.monotonic() + self._blacklist_ttl
                        if self._cached_region and self._cached_region in self._region_history:
                            self._region_history.remove(self._cached_region)
                self._stale_tier += 1
                self._buf_addr = 0
                self._stale_count = 0
            return

        stripped = content.strip()
        if not stripped:
            return

        self._stale_count = 0
        self._stale_tier = 0

        buf_max_seq = _extract_max_seq(content.encode("utf-8", errors="replace"))
        if buf_max_seq > 0:
            if self._seq_history and buf_max_seq == self._seq_history[-1]:
                self._frozen_count += 1
            else:
                self._frozen_count = 0
            self._seq_history.append(buf_max_seq)
            if len(self._seq_history) > 3:
                self._seq_history.pop(0)
            if self._frozen_count >= _FROZEN_THRESHOLD:
                now_f = time.monotonic()
                time_idle = now_f - self._last_new_msg_time if self._last_new_msg_time else float("inf")
                if time_idle > 3.0:
                    self._frozen_count = 0
                    self._quick_rescan_for_newer_buffer()

        self._deliver_new_messages(content)

        now = time.monotonic()
        time_since_new_msg = now - self._last_new_msg_time if self._last_new_msg_time else float("inf")
        if (
            time_since_new_msg > 999999.0
            and now - self._last_rescan >= self._rescan_interval
        ):
            self._last_rescan = now
            self._quick_rescan_for_newer_buffer()

    def _deliver_new_messages(self, content: str) -> None:
        """Parse buffer content and deliver messages with seq > last_seq."""
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

        if max_seq_in_buf > 0 and max_seq_in_buf < self._last_seq and (self._last_seq - max_seq_in_buf) > 50:
            logger.info(
                "Seq reset detected (buf max=%d, last_seq=%d) — saving texts & resetting",
                max_seq_in_buf, self._last_seq,
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
                        ts = (
                            f"{t.tm_mon}/{t.tm_mday} "
                            f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"
                        )
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
            self._rescan_interval = _RESCAN_INTERVALS[0]
            self._same_addr_count = 0
            self._frozen_count = 0
            self._last_new_msg_time = time.monotonic()

    @staticmethod
    def _make_synthetic_log_line(channel: str, author: str, text: str) -> str | None:
        """Convert addon buffer entry to a WoW chat log line for parse_line()."""
        _ADDON_CHANNEL_TO_LOG = {
            "SAY": "Say", "YELL": "Yell", "PARTY": "Party",
            "PARTY_LEADER": "Party Leader", "RAID": "Raid",
            "RAID_LEADER": "Raid Leader", "RAID_WARNING": "Raid Warning",
            "GUILD": "Guild", "OFFICER": "Officer",
            "INSTANCE_CHAT": "Instance", "INSTANCE_CHAT_LEADER": "Instance Leader",
            "CHANNEL": "Say", "EMOTE": "Say",
            "BATTLEGROUND": "Instance", "BATTLEGROUND_LEADER": "Instance Leader",
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
