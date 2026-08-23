"""Silence is not a status.

A tester spent an evening on this: messages arrived minutes late, then not at
all, and the overlay showed WoW as connected the whole time. It was telling the
truth about the only thing it checked — a process named Wow.exe existed — and
that was never the question.

Everything below the indicator was equally quiet. Both scanners return None for
"nothing new in the buffer" and for "Windows refused the handle", which are not
the same thing at all: the first resolves on its own with the next message, the
second never resolves and needs the user to do something. Neither produced a
log line above INFO.

So the reader now names what is wrong, and the indicator shows it.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("pymem", reason="the Windows memory reader")

if sys.platform != "win32":
    pytest.skip("the Windows memory reader", allow_module_level=True)

from app.memory_reader_windows import (  # noqa: E402
    ACCESS_DENIED,
    NO_BUFFER,
    PROCESS_GONE,
    WoWAddonBufReader,
    describe_access,
)


@pytest.fixture
def reader():
    return WoWAddonBufReader(lambda *_args, **_kwargs: None)


# ── being refused is not being connected ─────────────────────────────────────


def test_a_process_we_may_read_reports_no_problem():
    """Our own process is the one case guaranteed to be readable."""
    import os

    assert describe_access(os.getpid()) == ""


def test_a_pid_that_does_not_exist_does_not_read_as_readable():
    """A PID above the range Windows hands out."""
    assert describe_access(0x7FFFFFF0) != ""


@pytest.mark.parametrize(
    ("code", "expected"),
    [(5, ACCESS_DENIED), (87, PROCESS_GONE), (299, "open_failed_299")],
    ids=["access_denied", "no_such_process", "anything_else"],
)
def test_each_refusal_code_gets_its_own_answer(code, expected, monkeypatch):
    """Reported by number when it is not one of the two worth naming, because a
    code the user can search for beats a shrug. Accepting "anything non-empty"
    was what let the whole distinction collapse into one message in the first
    place, so each is pinned."""
    import pymem.ressources.kernel32 as kernel32

    import app.memory_reader_windows as module

    monkeypatch.setattr(kernel32, "OpenProcess", lambda *_a: 0)
    monkeypatch.setattr(module.ctypes, "get_last_error", lambda: code)

    assert describe_access(4321) == expected


def test_a_refused_process_does_not_come_back_as_attached(reader, monkeypatch):
    """The defect exactly: `_attach` looked for a process and declared success,
    so the overlay went green beside a reader that could not read anything."""
    import app.memory_reader_windows as module

    monkeypatch.setattr(module, "_find_wow_pid", lambda: 4321)
    monkeypatch.setattr(module, "describe_access", lambda _pid: ACCESS_DENIED)

    with pytest.raises(RuntimeError):
        reader._attach()

    assert reader.is_attached is False
    assert reader.problem == ACCESS_DENIED


def test_a_readable_process_attaches_and_reports_nothing_wrong(reader, monkeypatch):
    import app.memory_reader_windows as module

    monkeypatch.setattr(module, "_find_wow_pid", lambda: 4321)
    monkeypatch.setattr(module, "describe_access", lambda _pid: "")

    reader._attach()

    assert reader.is_attached is True
    assert reader.problem == ""


def test_wow_not_running_is_not_reported_as_a_fault(reader, monkeypatch):
    """ "Start the game" is not a problem to be diagnosed, and dressing it up as
    one would make the indicator cry wolf every time the app is opened first."""
    import app.memory_reader_windows as module

    monkeypatch.setattr(module, "_find_wow_pid", lambda: None)

    with pytest.raises(RuntimeError):
        reader._attach()

    assert reader.problem == ""


# ── an empty buffer that stays empty is its own answer ───────────────────────


def test_a_buffer_that_never_appears_is_eventually_named(reader, monkeypatch):
    """This is the tester's second evening: WoW running, memory readable, and
    no message ever arriving because the addon was not writing. Waiting quietly
    for that is indistinguishable from a quiet chat."""
    import app.memory_reader_windows as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(module, "_rust_lib", None)
    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: None)

    reader._pid = 4321
    reader._poll()
    assert reader.problem == "", "it complained on the very first miss"

    clock["now"] += module._SILENCE_BEFORE_COMPLAINT + 1
    reader._poll()

    assert reader.problem == NO_BUFFER


def test_a_quiet_minute_followed_by_a_message_clears_the_complaint(reader, monkeypatch):
    """Someone starts the app, alt-tabs, and comes back to a busy Trade chat.
    An indicator stuck on a problem that has resolved is the same defect
    pointing the other way."""
    import app.memory_reader_windows as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(module, "_rust_lib", None)
    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: None)

    reader._pid = 4321
    reader._poll()
    clock["now"] += module._SILENCE_BEFORE_COMPLAINT + 1
    reader._poll()
    assert reader.problem == NO_BUFFER

    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: "1|RAW|SAY|Player|hello")
    reader._poll()

    assert reader.problem == ""


# ── and the overlay shows it ─────────────────────────────────────────────────


def test_every_reason_the_reader_can_name_has_something_to_show():
    """A status the indicator does not know about falls through to a default,
    and a default that looks like every other state is how this hid."""
    pytest.importorskip("PyQt6", reason="the Qt overlay")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app.locales import LANGUAGE_MODULES
    from app.overlay import ChatOverlay

    for reason in (ACCESS_DENIED, NO_BUFFER, PROCESS_GONE):
        assert reason in ChatOverlay._WOW_STATES, f"the overlay has no state for {reason}"
        _label, _colour, key = ChatOverlay._WOW_STATES[reason]
        assert key, f"{reason} shows no explanation"
        # Read from each language's own module, not through `tr`: it falls back
        # to English for a missing key, so a deleted Russian string comes back
        # as perfectly good English and the check sees nothing wrong. The
        # Russian is the one that matters most here — the tester who lost an
        # evening to this reads Russian.
        for language, module in LANGUAGE_MODULES.items():
            assert key in module.STRINGS, f"{key} has no {language} copy"
            if reason in (ACCESS_DENIED, NO_BUFFER):
                # These two are the ones the user has to act on, so a sentence
                # naming the state is not enough — it has to say what to do.
                # "WoW is not running" needs no such thing and is left short.
                assert len(module.STRINGS[key].split()) >= 12, f"{key} in {language} says what, not what to do"


def test_a_problem_does_not_look_like_a_healthy_connection():
    """Green beside a broken reader is the whole story here."""
    pytest.importorskip("PyQt6", reason="the Qt overlay")
    from app.overlay import ChatOverlay

    healthy = ChatOverlay._WOW_STATES["attached"][1]
    for reason in (ACCESS_DENIED, NO_BUFFER):
        assert ChatOverlay._WOW_STATES[reason][1] != healthy, f"{reason} is drawn in the healthy colour"
