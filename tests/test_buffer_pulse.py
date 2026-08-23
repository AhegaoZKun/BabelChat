"""The buffer's pulse, and why the reader needed one.

Every rebuild of the companion buffer allocates a new Lua string and frees the
previous one, and the freed bytes stay readable for a while. So the reader can
be looking at a copy the addon will never touch again — and until now it had no
way to tell, because the only thing in the buffer that moved was the message
counter, which does not move in a quiet chat either.

Measured on a live game before the fix: messages sent between 15:24:45 and
15:25:05 reached the app together at 15:29:06, and a cold scan found them in
0.3 seconds the whole time. The reader was reading a corpse.

A counter that ticks on every rebuild, said or unsaid, makes the difference
visible in a single poll.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import load_companion_buffer  # noqa: E402

PULSE = re.compile(r"^0\|META\|FLUSH\|(\d+)$", re.MULTILINE)


def flush(harness) -> None:
    harness.addon_table.StartBufferFlush()
    harness.fire_tickers()


def pulse_of(harness) -> int:
    buffer = harness.db.wctbuf or ""
    found = PULSE.search(buffer)
    assert found, f"no pulse in the buffer: {buffer[:120]!r}"
    return int(found.group(1))


# ── the addon writes it ──────────────────────────────────────────────────────


def test_the_buffer_carries_a_pulse():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello", "RAW", "SAY", "Thrall")
    flush(h)

    assert pulse_of(h) >= 1


def test_the_pulse_comes_before_anything_that_varies_in_length():
    """A truncated read still has to carry it, and the player's name is the
    first thing in there whose length is not known in advance."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello", "RAW", "SAY", "Thrall")
    flush(h)

    lines = [line for line in (h.db.wctbuf or "").splitlines() if line.strip()]

    assert lines[1].startswith("0|META|FLUSH|"), f"the pulse is not the first record: {lines[:3]}"


def test_the_pulse_rises_with_every_rebuild():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("one", "RAW", "SAY", "Thrall")
    flush(h)
    first = pulse_of(h)

    h.addon_table.BufferAddEntry("two", "RAW", "SAY", "Thrall")
    h.fire_tickers()

    assert pulse_of(h) > first


def test_the_pulse_rises_even_when_nobody_says_anything():
    """This is the whole point. A pulse that only moved when a message arrived
    would leave a quiet chat looking exactly like a dead copy, which is the
    thing being fixed."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello", "RAW", "SAY", "Thrall")
    flush(h)
    first = pulse_of(h)

    # Two seconds of nothing being said.
    h.now += 2.5
    h.fire_tickers()

    assert pulse_of(h) > first, "the pulse stopped in an idle chat"


def test_an_idle_rebuild_keeps_the_messages():
    """Rebuilding on a timer must not quietly empty the ring."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("keep me", "RAW", "SAY", "Thrall")
    flush(h)

    h.now += 2.5
    h.fire_tickers()

    assert "keep me" in (h.db.wctbuf or "")


def test_the_pulse_survives_a_reload():
    """It is compared BETWEEN copies. A pulse that restarted at zero would make
    the live buffer look older than the corpse of the previous session."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello", "RAW", "SAY", "Thrall")
    flush(h)
    before = pulse_of(h)

    assert h.db.wctFlush == before, "the pulse is not saved, so a reload restarts it"


# ── and the scanner ranks by it ──────────────────────────────────────────────

SCANNER = pathlib.Path(__file__).resolve().parent.parent / "babelchat_scanner_win" / "src"


def scanner_source() -> str:
    """The whole crate, not one file of it.

    The scanner outgrew a single module and was split along its seams — the
    process it reads, the markers it reads for, the search, and the table slot.
    A check that read only lib.rs would quietly stop covering four fifths of
    what it is about."""
    return chr(10).join(path.read_text(encoding="utf-8") for path in sorted(SCANNER.glob("*.rs")))


def test_the_scanner_reads_the_pulse():
    source = scanner_source()

    assert "fn extract_flush" in source
    assert 'b"0|META|FLUSH|"' in source, "the scanner looks for a different record than the addon writes"


def test_the_scanner_keeps_the_best_copy_not_the_first():
    """Which of two copies a parallel scan reaches first is luck, and the dead
    one often lives at the lower address."""
    source = scanner_source()

    assert "fn rank(" in source
    assert "is_none_or(|(b, _, _)| score > *b)" in source, "candidates are not compared"
    # It may stop early, but only once it has a baseline to stop against: a
    # pulse above the highest ever seen cannot belong to a copy that stopped
    # being written. Stopping without one meant stopping at whichever candidate
    # the threads reached first, and that was a corpse — measured, a pulse
    # frozen at 271 for two and a half minutes.
    assert "if baseline > 0 && score.0 > baseline {" in source, (
        "the scan stops early with no baseline to stop against"
    )


def test_an_addon_without_a_pulse_still_works():
    """The app is updated separately from the addon — by hand, from a zip — so
    an app running ahead of its addon has to keep reading the old buffer."""
    source = scanner_source()

    assert "if flush > 0 { flush > c.last_flush } else { seq > min_seq }" in source, (
        "a buffer with no pulse is no longer handled"
    )


def test_the_scanner_can_describe_itself():
    source = scanner_source()

    assert 'pub extern "C" fn describe_state' in source
    for field in ("cached=", "addr=", "pulse=", "quiet_ms=", "scans="):
        assert field in source, f"the state does not report {field}"


def test_the_reader_writes_that_state_into_the_log():
    reader = pathlib.Path(__file__).resolve().parent.parent / "app" / "memory_reader_windows.py"
    body = reader.read_text(encoding="utf-8")

    assert "scanner_state()" in body, "the state is exported and never looked at"
    assert "_STATE_EVERY_N_MISSES" in body


def test_the_probe_that_renewed_the_stale_cache_is_gone():
    """It asked the scanner with a filter of zero, at which any parseable bytes
    at the cached address look fresh — so the question kept the dead address
    alive. It held one for two minutes at a time."""
    reader = pathlib.Path(__file__).resolve().parent.parent / "app" / "memory_reader_windows.py"
    body = reader.read_text(encoding="utf-8")

    assert "_probe_ignoring_sequence" not in body
    assert "_PROBE_EVERY_N_MISSES" not in body
