"""Loading the bundled native memory scanner, safely.

The scanner is a small Rust library that reads the addon's buffer out of the
game process. Loading it used to start from a bare filename:

    _DLL_NAMES = ["babelchat_scanner_win.dll", <abs path>, <abs path>]

`ctypes.CDLL("name.dll")` goes through the Windows loader's standard search
order, which includes the current working directory and every entry in PATH. A
process running as the same user — ordinary malware, no elevation needed — can
drop a matching DLL into either and have it loaded by us. That mattered more
than it usually would, because the app also asked for administrator rights: the
next UAC prompt the user approves for BabelChat would run the planted library
elevated.

So: absolute paths only, and on Windows the dependency search is confined to
System32 and the library's own directory rather than the ambient PATH.
"""

from __future__ import annotations

import ctypes
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)

WINDOWS_LIBRARY = "babelchat_scanner_win.dll"
LINUX_LIBRARY = "libbabelchat_scanner.so"

# LoadLibraryEx flags. CPython already applies exactly these when a path
# containing a separator is passed, so they are belt-and-braces rather than
# the mechanism: the security comes from passing an absolute path at all.
# They cover System32, the application directory, any AddDllDirectory entry
# and the library's own directory — not the working directory, and not PATH.
_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
_LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
_SAFE_SEARCH = _LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | _LOAD_LIBRARY_SEARCH_DEFAULT_DIRS


def library_name() -> str:
    return WINDOWS_LIBRARY if sys.platform == "win32" else LINUX_LIBRARY


def candidate_paths(name: str | None = None) -> list[pathlib.Path]:
    """Absolute paths the scanner may legitimately live at, in priority order.

    Every entry is absolute and resolved. A bare filename is deliberately absent:
    it is the whole vulnerability, and a caller that adds one back should have to
    do it visibly.
    """
    name = name or library_name()
    here = pathlib.Path(__file__).resolve().parent
    candidates = []

    # PyInstaller unpacks bundled data next to the frozen executable.
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        candidates.append(pathlib.Path(bundle_dir) / name)
    candidates.append(here / name)
    candidates.append(here.parent / name)

    seen: set[pathlib.Path] = set()
    unique = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _load_from(path: pathlib.Path) -> ctypes.CDLL:
    if sys.platform == "win32":
        return ctypes.CDLL(str(path), winmode=_SAFE_SEARCH)
    return ctypes.CDLL(str(path))


def load_scanner(name: str | None = None) -> ctypes.CDLL | None:
    """Load the scanner and declare its one entry point.

    Returns None when the library is absent or unloadable — the callers fall
    back to a pure-Python scan, which is slower but works.
    """
    for path in candidate_paths(name):
        if not path.is_file():
            continue
        try:
            lib = _load_from(path)
            # Declaring the signature is where a same-named library WITHOUT our
            # export fails, with AttributeError rather than OSError. Unhandled,
            # it escapes this loop and the module import that called it, so the
            # reader cannot even fall back to the Python scanner.
            lib.find_and_read_buffer.restype = ctypes.c_int32
            lib.find_and_read_buffer.argtypes = [
                ctypes.c_int32,  # pid
                ctypes.c_int32,  # min_seq
                ctypes.c_char_p,  # out_buf
                ctypes.c_int32,  # out_len
            ]
        except (OSError, AttributeError) as e:
            logger.warning("Native scanner at %s is unusable: %s", path, e)
            continue
        logger.info("Loaded native scanner: %s", path)
        return lib

    logger.info("No native scanner found — falling back to the Python scanner")
    return None
