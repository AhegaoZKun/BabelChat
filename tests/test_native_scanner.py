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


def test_a_library_planted_in_the_working_directory_is_never_loaded(tmp_path, monkeypatch):
    """The vulnerability was that the loader searched the working directory, so
    the property has to be about what gets LOADED, not only about what the
    candidate list happens to contain. Asserting on candidate_paths alone could
    not fail: it is __file__-relative, so tmp_path was never going to appear in
    it however the loading worked."""
    planted = tmp_path / library_name()
    planted.write_bytes(b"not a real library")
    monkeypatch.chdir(tmp_path)

    loaded = []

    class RecordingCDLL:
        def __init__(self, path, winmode=None):
            loaded.append(pathlib.Path(path).resolve())
            raise OSError("stub")

    monkeypatch.setattr(native_scanner.ctypes, "CDLL", RecordingCDLL)
    load_scanner()

    assert planted.resolve() not in loaded, "the loader opened a library from the working directory"


def test_candidates_are_deduplicated():
    paths = candidate_paths()
    assert len(paths) == len(set(paths))


def test_a_frozen_build_looks_beside_the_executable_first(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    first = candidate_paths()[0]

    assert first.parent == tmp_path.resolve()


def test_the_library_name_carries_the_platform_extension():
    """Comparing library_name() to the module's own two constants restates the
    implementation. What matters is that each platform gets a name its loader
    can actually open."""
    assert WINDOWS_LIBRARY.endswith(".dll")
    assert LINUX_LIBRARY.startswith("lib") and LINUX_LIBRARY.endswith(".so")
    assert library_name() == (WINDOWS_LIBRARY if sys.platform == "win32" else LINUX_LIBRARY)
    assert library_name() != ""


def test_a_library_that_is_not_on_disk_returns_none_rather_than_raising(monkeypatch, tmp_path):
    """The Python scanner takes over — slower, but the app still runs.

    Feeding an empty candidate list meant the loop body never ran, so nothing
    under test executed. A path that is named but absent exercises the miss."""
    monkeypatch.setattr(native_scanner, "candidate_paths", lambda name=None: [tmp_path / library_name()])

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
    import ctypes

    lib = load_scanner()
    assert lib is not None, "the Windows build ships this library; a miss is a packaging bug"
    # restype is never None — ctypes defaults it to c_int — so asserting that it
    # is set proves nothing. The signature the loader declares is what a wrong
    # call would get wrong.
    assert lib.find_and_read_buffer.restype is ctypes.c_int32
    assert lib.find_and_read_buffer.argtypes is not None


def test_the_loader_is_shared_by_both_platform_readers(monkeypatch):
    """Both readers carried their own copy, and the Windows one grew the
    search-order fix while the Linux one did not.

    Checked by making the shared loader return a sentinel and importing each
    reader, rather than by grepping their source: a reader could import the
    shared name and still call its own copy, and the source check also only
    passed when pytest happened to run from the repository root.
    """
    import importlib

    sentinel = object()
    monkeypatch.setattr(native_scanner, "load_scanner", lambda *a, **k: sentinel)

    for module_name in ("app.memory_reader_windows", "app.memory_reader_linux"):
        module = importlib.reload(importlib.import_module(module_name))
        handle = module._rust_lib
        assert handle is sentinel, f"{module_name} does not get its scanner from the shared loader"
