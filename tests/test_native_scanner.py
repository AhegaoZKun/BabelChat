"""How the native scanner is located and loaded.

The loader used to start its search from a bare filename, which sends Windows
through its standard search order — the current working directory and every
entry in PATH included. Anything a same-user process can write to becomes a
place to plant a library. These tests pin the property that fixes it: every
candidate is an absolute path, and a bare name is never one of them.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from app import native_scanner
from app.native_scanner import LINUX_LIBRARY, WINDOWS_LIBRARY, candidate_paths, library_name, load_scanner


def test_every_candidate_is_an_absolute_path():
    for path in candidate_paths():
        assert path.is_absolute(), f"{path} is relative — the loader would search PATH for it"


def test_no_candidate_is_a_bare_filename():
    """The bare name is the vulnerability, not a convenience."""
    names = {path.name for path in candidate_paths()}
    assert names == {library_name()}
    assert all(len(path.parts) > 1 for path in candidate_paths())


def test_a_library_planted_in_the_working_directory_is_not_a_candidate(tmp_path, monkeypatch):
    planted = tmp_path / library_name()
    planted.write_bytes(b"not a real library")
    monkeypatch.chdir(tmp_path)

    assert planted.resolve() not in candidate_paths()


def test_candidates_are_deduplicated():
    paths = candidate_paths()
    assert len(paths) == len(set(paths))


def test_a_frozen_build_looks_beside_the_executable_first(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    first = candidate_paths()[0]

    assert first.parent == tmp_path.resolve()


def test_the_library_name_matches_the_platform():
    expected = WINDOWS_LIBRARY if sys.platform == "win32" else LINUX_LIBRARY
    assert library_name() == expected


def test_a_missing_library_returns_none_rather_than_raising(monkeypatch):
    """The Python scanner takes over — slower, but the app still runs."""
    monkeypatch.setattr(native_scanner, "candidate_paths", lambda name=None: [])

    assert load_scanner() is None


def test_an_unloadable_file_is_skipped_without_raising(monkeypatch, tmp_path):
    broken = tmp_path / library_name()
    broken.write_bytes(b"definitely not a shared library")
    monkeypatch.setattr(native_scanner, "candidate_paths", lambda name=None: [broken])

    assert load_scanner() is None


@pytest.mark.skipif(sys.platform != "win32", reason="the search-order flags are Windows-only")
def test_windows_passes_the_confined_search_flags_to_the_loader(monkeypatch, tmp_path):
    """Asserting the constant's value proves nothing — it would hold even if the
    flags were never handed to CDLL. This checks the call that is made."""
    seen = {}

    class FakeCDLL:
        def __init__(self, path, winmode=None):
            seen["path"] = path
            seen["winmode"] = winmode
            self.find_and_read_buffer = type("Fn", (), {"restype": None, "argtypes": None})()

    library = tmp_path / library_name()
    library.write_bytes(b"stub")
    monkeypatch.setattr(native_scanner, "candidate_paths", lambda name=None: [library])
    monkeypatch.setattr(native_scanner.ctypes, "CDLL", FakeCDLL)

    assert load_scanner() is not None
    assert seen["path"] == str(library)
    assert seen["winmode"] == 0x00000100 | 0x00001000


def test_a_library_missing_our_export_is_skipped_not_fatal(monkeypatch, tmp_path):
    """Declaring the signature fails with AttributeError, not OSError. Unhandled
    it escaped load_scanner and the module import that called it, so the reader
    could not even fall back to the Python scanner."""

    class NoExport:
        def __init__(self, *_args, **_kwargs):
            pass

        def __getattr__(self, name):
            raise AttributeError(name)

    library = tmp_path / library_name()
    library.write_bytes(b"stub")
    monkeypatch.setattr(native_scanner, "candidate_paths", lambda name=None: [library])
    monkeypatch.setattr(native_scanner.ctypes, "CDLL", NoExport)

    assert load_scanner() is None


@pytest.mark.skipif(sys.platform != "win32", reason="the bundled library is the Windows build")
def test_the_shipped_library_declares_the_scanner_entry_point():
    lib = load_scanner()
    assert lib is not None, "the Windows build ships this library; a miss is a packaging bug"
    assert lib.find_and_read_buffer.restype is not None


def test_the_loader_is_shared_by_both_platform_readers():
    """Both readers carried their own copy, and the Windows one grew the fix
    while the Linux one did not."""
    windows = pathlib.Path("app/memory_reader_windows.py").read_text(encoding="utf-8")
    linux = pathlib.Path("app/memory_reader_linux.py").read_text(encoding="utf-8")
    for source in (windows, linux):
        assert "from app.native_scanner import load_scanner" in source
        assert "_DLL_NAMES" not in source
        assert "_LIB_NAMES" not in source
