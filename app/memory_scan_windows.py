"""Finding WoW's process, and reading the addon's buffer out of it.

Two scanners live here: the Rust library that does the work, and a pure-Python
fallback for a build without it. Alongside them sits the question neither of
them used to answer — whether this process is allowed to read that one at all.

That question was the whole of a tester's lost evening. Both scanners return
None for "nothing new in the buffer" and for "Windows would not give us a
handle", and the reader treated the two the same: it waited. The first resolves
with the next chat message; the second never resolves and needs the user to do
something about it.
"""

from __future__ import annotations

import ctypes
import logging

from app.addon_protocol import MARKER_END, extract_max_seq, find_content_start
from app.native_scanner import load_scanner

logger = logging.getLogger(__name__)

_rust_lib: ctypes.CDLL | None = load_scanner()
_OUT_BUF_SIZE = 131072  # 128KB

MAX_BUF_READ = 65536
WOW_PROCESS_NAMES = ["Wow.exe", "WowT.exe", "WowB.exe"]


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
                # Opening with full access can fail without elevation. There is
                # no PID to hand back from this path, so the caller retries on
                # the next poll; the tasklist branch above is what normally
                # supplies the PID and needs no rights at all.
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


# ── why the game's memory cannot be read ─────────────────────────────────────
#
# Every scan path returned None for "nothing new in the buffer" and for "the
# operating system refused to let us look", which are not remotely the same
# thing. The second one never resolves on its own and the app said nothing
# about it: the overlay showed WoW as connected, because being connected meant
# only that a process with that name existed, and then no message ever arrived.
# A tester spent an evening on that.

#: Windows error codes worth naming. Anything else is reported by number.
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87

ACCESS_DENIED = "access_denied"
PROCESS_GONE = "process_gone"
NO_BUFFER = "no_buffer"


def describe_access(pid: int) -> str:
    """Whether this process may read that one, and why not.

    Returns "" when the memory can be read. The check is the same OpenProcess
    the scanner does, so a success here means the scanner will get its handle
    too.
    """
    try:
        import pymem.ressources.kernel32 as kernel32
    except ImportError:
        return ""  # the native scanner is in use; it does its own opening

    process_vm_read = 0x0010
    process_query_information = 0x0400
    handle = kernel32.OpenProcess(process_vm_read | process_query_information, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return ""

    code = ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError()
    if code == _ERROR_ACCESS_DENIED:
        return ACCESS_DENIED
    if code == _ERROR_INVALID_PARAMETER:
        return PROCESS_GONE
    return f"open_failed_{code}"


def _pymem_find_buffer(pid: int, min_seq: int) -> str | None:
    """Fallback: use pymem if the native scanner is not available."""
    try:
        pm = _open_for_reading(pid)
        if pm is None:
            return None
        try:
            return _scan_for_buffer(pm, min_seq)
        finally:
            # The handle has to be released even when the scan raises, which it
            # does routinely: WoW exiting mid-scan is the common case, and this
            # runs again every couple of seconds.
            pm.close_process()
    except Exception:
        return None


def _scan_for_buffer(pm, min_seq: int) -> str | None:
    import pymem.pattern

    try:
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
        return best_content
    except Exception:
        return None
