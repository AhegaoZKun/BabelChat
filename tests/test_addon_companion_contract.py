"""The addon writes the buffer; the companion reads it. This pins the contract.

Both halves used to be tested separately, or not at all, and they drifted: the
addon appended its dictionary gloss after a tab, but the gloss carried a newline
and the buffer is newline-delimited, so the reader saw a headless fragment and
dropped it. Nothing failed — the field simply never arrived.

These tests run the real Lua through lupa, hand the resulting buffer to the real
reader, and assert on what comes out the far end.
"""

from __future__ import annotations

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from app.memory_reader_windows import WoWAddonBufReader  # noqa: E402
from tests.lua_harness import load_companion_buffer  # noqa: E402


@pytest.fixture
def reader():
    """A reader that collects delivered log lines instead of running a thread.

    Nothing is redirected any more: the capture trace is off unless switched on,
    so a test run leaves no chat transcript on disk. This fixture used to have
    to point it at a temp file, which was the test compensating for the defect
    rather than the code not having it.
    """
    delivered: list[tuple[str, bool]] = []

    def on_new_line(line: str, dict_translated: bool = False, **_kwargs) -> None:
        delivered.append((line, dict_translated))

    instance = WoWAddonBufReader(on_new_line)
    instance.delivered = delivered  # type: ignore[attr-defined]
    return instance


def buffer_content(harness) -> str:
    """The payload the reader receives: everything between the frame markers.

    This mirrors the real scanner deliberately, including its bluntness: it
    takes the FIRST occurrence of the end marker anywhere in the buffer, with no
    requirement that it start a line — `_pymem_find_buffer` does exactly
    `raw.find(MARKER_END, content_start)`. A gentler helper here would let a
    crafted message pass a truncation test that the shipped code fails.
    """
    text = harness.buffer_text()
    start = text.index("__\n") + len("__")
    end = text.index("__WCT_END__")
    return text[start:end]


def deliver(reader, harness) -> list[tuple[str, bool]]:
    reader._deliver_new_messages(buffer_content(harness))
    return reader.delivered


def flush(harness) -> None:
    harness.addon_table.StartBufferFlush()
    harness.fire_tickers()


# ── the round trip ───────────────────────────────────────────────────────────


def test_a_message_survives_the_round_trip(reader):
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello everyone", "RAW", "SAY", "Thrall-Sargeras")
    flush(h)

    delivered = deliver(reader, h)
    assert len(delivered) == 1
    line, was_glossed = delivered[0]
    assert line.endswith("[Say] Thrall-Sargeras: hello everyone")
    assert was_glossed is False


def test_a_glossed_message_is_flagged_but_carries_no_extra_field(reader):
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("ty for the run", "DICT", "PARTY", "Bob")
    flush(h)

    delivered = deliver(reader, h)
    assert len(delivered) == 1
    line, was_glossed = delivered[0]
    assert line.endswith("[Party] Bob: ty for the run")
    assert was_glossed is True


def test_the_player_name_is_picked_up_from_the_meta_line(reader):
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("anything", "RAW", "SAY", "Bob")
    flush(h)
    deliver(reader, h)

    assert reader.player_name == "Tester-TestRealm"


def test_every_message_in_a_full_buffer_arrives(reader):
    h = load_companion_buffer()
    for i in range(12):
        h.addon_table.BufferAddEntry(f"message {i}", "RAW", "GUILD", f"Player{i}")
    flush(h)

    delivered = deliver(reader, h)
    assert len(delivered) == 12
    assert delivered[0][0].endswith("message 0")
    assert delivered[-1][0].endswith("message 11")


# ── hostile content crossing the boundary ────────────────────────────────────


def test_the_end_marker_in_chat_does_not_hide_later_messages(reader):
    """The reader locates the buffer's end by scanning for the literal marker."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("__WCT_END__", "RAW", "CHANNEL:Trade", "Griefer")
    h.addon_table.BufferAddEntry("still here", "RAW", "SAY", "Bob")
    flush(h)

    delivered = deliver(reader, h)
    assert len(delivered) == 2, "a crafted message must not swallow the rest"
    assert delivered[-1][0].endswith("still here")


def test_a_newline_in_chat_does_not_become_a_second_record(reader):
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("line one\nline two", "RAW", "SAY", "Bob")
    flush(h)

    delivered = deliver(reader, h)
    assert len(delivered) == 1
    assert delivered[0][0].endswith("line one line two")


def test_an_item_link_arrives_unmangled(reader):
    h = load_companion_buffer()
    link = "|cffa335ee|Hitem:245770::::::::90:62|h[Alnara's Cane]|h|r"
    h.addon_table.BufferAddEntry(f"wts {link}", "RAW", "CHANNEL:Trade", "Vasya")
    flush(h)

    delivered = deliver(reader, h)
    assert link in delivered[0][0]


# ── buffers written by older addon versions ──────────────────────────────────


def test_a_legacy_buffer_with_a_tab_separated_gloss_delivers_only_the_message(reader):
    """3.3.0 and earlier appended the gloss after a tab.

    The addon is copied into the game folder by hand, so a user who updates the
    app without updating the addon keeps sending those records. Delivering the
    whole field would send the gloss to the translation API and print it in the
    overlay — so the gloss is dropped, not concatenated.
    """
    legacy = "\n0|META|PLAYER|Old-Realm\n1|DICT|SAY|Bob|ty for the run\tty = спасибо"

    reader._deliver_new_messages(legacy)

    assert len(reader.delivered) == 1
    line, was_glossed = reader.delivered[0]
    assert line.endswith("[Say] Bob: ty for the run")
    assert "спасибо" not in line, "the gloss must not be glued onto the message"
    assert was_glossed is True


def test_a_current_message_containing_no_tab_is_untouched(reader):
    """The legacy path must not nibble at ordinary text."""
    current = "\n0|META|PLAYER|Realm\n1|DICT|SAY|Bob|ty for the run"

    reader._deliver_new_messages(current)

    assert reader.delivered[0][0].endswith("[Say] Bob: ty for the run")


def test_a_headless_fragment_is_ignored_rather_than_crashing(reader):
    """Exactly what an embedded newline used to produce."""
    fragment = "\n0|META|PLAYER|Old-Realm\n1|RAW|SAY|Bob|real message\n   → ty → спасибо"

    reader._deliver_new_messages(fragment)

    assert len(reader.delivered) == 1
    assert reader.delivered[0][0].endswith("real message")
