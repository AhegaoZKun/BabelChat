"""CompanionBuffer.lua under a real Lua 5.1 interpreter.

The buffer is the addon's only channel to the companion app, and it runs inside
a chat event filter — so an error here does not lose one message, it breaks
chat handling for every line that follows. These tests pin the two properties
that matter: a secret value never enters the buffer, and an error never escapes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import load_companion_buffer  # noqa: E402


def flush(harness) -> None:
    """Serialize the ring buffer into BabelChatDB.wctbuf."""
    harness.addon_table.StartBufferFlush()
    harness.fire_tickers()


# ── ordinary traffic ─────────────────────────────────────────────────────────


def test_entry_reaches_the_buffer():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello there", "RAW", "SAY", "Thrall-Sargeras")
    flush(h)

    assert h.buffer_entries() == ["1|RAW|SAY|Thrall-Sargeras|hello there"]


def test_buffer_is_wrapped_in_markers():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hi", "RAW", "SAY", "Bob")
    flush(h)

    text = h.buffer_text()
    assert text.startswith("__WCT_BUF_0001__\n")
    assert text.endswith("\n__WCT_END__")
    assert "0|META|PLAYER|Tester-TestRealm" in text


def test_missing_author_and_event_fall_back():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("orphan line", "RAW")
    flush(h)

    assert h.buffer_entries() == ["1|RAW|SAY|Unknown|orphan line"]


def test_ring_buffer_is_bounded():
    h = load_companion_buffer()
    for i in range(60):
        h.addon_table.BufferAddEntry(f"msg {i}", "RAW", "SAY", f"Player{i}")
    flush(h)

    entries = h.buffer_entries()
    assert len(entries) == 50, "ring buffer must cap at MSG_LIMIT"
    assert entries[0].endswith("msg 10"), "oldest entries are the ones dropped"
    assert entries[-1].endswith("msg 59")


def test_sequence_numbers_are_monotonic():
    h = load_companion_buffer()
    for i in range(3):
        h.addon_table.BufferAddEntry(f"m{i}", "RAW", "SAY", "Bob")
    flush(h)

    seqs = [int(line.split("|", 1)[0]) for line in h.buffer_entries()]
    assert seqs == [1, 2, 3]


# ── dedup ────────────────────────────────────────────────────────────────────


def test_same_message_from_multiple_chat_frames_is_deduped():
    """The filter fires once per ChatFrame showing the event — three windows, one entry."""
    h = load_companion_buffer()
    for _ in range(3):
        h.addon_table.BufferAddEntry("pull in 5", "RAW", "RAID", "Thrall")
    flush(h)

    assert len(h.buffer_entries()) == 1


def test_repeat_after_ttl_is_not_deduped():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("wts crest", "RAW", "CHANNEL:Trade", "Vasya")
    h.advance(3.0)  # DEDUP_TTL is 2.0
    h.addon_table.BufferAddEntry("wts crest", "RAW", "CHANNEL:Trade", "Vasya")
    flush(h)

    assert len(h.buffer_entries()) == 2, "a genuine repeat later on is not a duplicate"


def test_same_text_from_different_authors_is_kept():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("inv", "RAW", "CHANNEL:LFG", "Alice")
    h.addon_table.BufferAddEntry("inv", "RAW", "CHANNEL:LFG", "Bob")
    flush(h)

    assert len(h.buffer_entries()) == 2


# ── values the buffer must refuse (chat messaging lockdown) ──────────────────
#
# In an instance the chat arguments arrive as secrets: values that report as
# strings but raise on string.len. The addon guards them in two steps — a type
# check, then a pcall around the length probe.
#
# The harness cannot model the second step. Plain Lua has no way to build a
# value that answers `type()` with "string" and then raises, so the stand-in is
# a table and every test below stops at the TYPE check — the same branch a
# number or a boolean takes. That is worth having: it is the branch that runs
# first, and the ring-buffer and sequence-number consequences below are real
# either way. It is not proof that a secret cannot reach the buffer. The probe
# behind the type check is verified in game, not here.


def test_a_value_that_is_not_text_is_rejected_without_raising():
    """In a dungeon the message text arrives as a secret. It must be dropped,
    quietly — no error in the player's chat frame, no gap in the sequence."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry(h.secret(), "RAW", "PARTY", "Healer")
    flush(h)

    assert h.buffer_entries() == []


def test_an_author_that_is_not_text_degrades_to_unknown_and_keeps_the_message():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("heal me", "RAW", "PARTY", h.secret())
    flush(h)

    assert h.buffer_entries() == ["1|RAW|PARTY|Unknown|heal me"]


def test_dict_and_raw_records_have_the_same_shape():
    """`kind` records that the addon glossed the line; it carries no extra field."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("ty", "DICT", "PARTY", "Bob")
    flush(h)

    assert h.buffer_entries() == ["1|DICT|PARTY|Bob|ty"]


def test_unknown_kind_is_recorded_as_raw():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hi", "SOMETHING_ELSE", "SAY", "Bob")
    flush(h)

    assert h.buffer_entries() == ["1|RAW|SAY|Bob|hi"]


def test_a_rejected_value_does_not_poison_later_messages():
    """The regression this guards: a secret stored in the dedup ring or the
    accumulator used to raise again on every subsequent message and every flush."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry(h.secret(), "RAW", "PARTY", h.secret())
    h.addon_table.BufferAddEntry("still working", "RAW", "PARTY", "Bob")
    flush(h)

    assert h.buffer_entries() == ["1|RAW|PARTY|Bob|still working"]


def test_repeated_rejections_do_not_consume_sequence_numbers():
    h = load_companion_buffer()
    for _ in range(5):
        h.addon_table.BufferAddEntry(h.secret(), "RAW", "INSTANCE_CHAT", "Tank")
    h.addon_table.BufferAddEntry("first real one", "RAW", "PARTY", "Bob")
    flush(h)

    assert h.buffer_entries() == ["1|RAW|PARTY|Bob|first real one"]


def test_flush_survives_a_buffer_full_of_traffic_after_rejections():
    h = load_companion_buffer()
    for i in range(30):
        h.addon_table.BufferAddEntry(h.secret(), "RAW", "PARTY", "Tank")
        h.addon_table.BufferAddEntry(f"real {i}", "RAW", "PARTY", "Bob")
    flush(h)

    entries = h.buffer_entries()
    assert len(entries) == 30
    # The real property: 30 rejected values leave 30 good ones, numbered 1..30
    # with no gaps. Checking that the word "secret" is absent was trivially true
    # — nothing ever concatenates the stand-in.
    assert [e.split("|")[0] for e in entries] == [str(n) for n in range(1, 31)]
    assert [e.split("|")[-1] for e in entries] == [f"real {i}" for i in range(30)]


# ── hostile message content ──────────────────────────────────────────────────
#
# Chat text is written by other players. Anything that reaches the buffer is
# therefore attacker-controlled, and the buffer's own structural characters are
# the interesting target.


def test_a_newline_in_the_text_does_not_split_the_record():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("first\nsecond", "RAW", "SAY", "Bob")
    flush(h)

    entries = h.buffer_entries()
    assert entries == ["1|RAW|SAY|Bob|first second"], "one message must stay one record"


def test_a_tab_in_the_text_does_not_split_the_record():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("before\tafter", "DICT", "SAY", "Bob")
    flush(h)

    assert h.buffer_entries() == ["1|DICT|SAY|Bob|before after"]


def test_a_player_typing_the_end_marker_cannot_truncate_the_buffer():
    """Anyone in Trade chat can type __WCT_END__; the companion scans for it."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("lol __WCT_END__ gotcha", "RAW", "CHANNEL:Trade", "Griefer")
    h.addon_table.BufferAddEntry("this must still arrive", "RAW", "SAY", "Bob")
    flush(h)

    text = h.buffer_text()
    assert text.count("__WCT_END__") == 1, "only the real frame marker may appear"
    assert text.index("__WCT_END__") == len(text) - len("__WCT_END__")
    assert len(h.buffer_entries()) == 2


def test_a_player_typing_the_start_marker_cannot_forge_a_header():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("__WCT_BUF_9999__", "RAW", "SAY", "Griefer")
    flush(h)

    assert h.buffer_text().count("__WCT_BUF_") == 1


# The naive marker is only the first attempt. A replacement that itself ends in
# an underscore lets the marker re-form across the seam gsub has already passed:
# gsub resumes scanning after each match and never revisits what it just wrote.
@pytest.mark.parametrize(
    "typed",
    [
        "__WCT_END__",
        "__WCT__WCT_END__",
        "___WCT_END__",
        "____WCT_END__",
        "__WCT___WCT_END__",
    ],
)
def test_no_arrangement_of_underscores_reconstructs_the_end_marker(typed):
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry(typed, "RAW", "CHANNEL:Trade", "Griefer")
    h.addon_table.BufferAddEntry("must still arrive", "RAW", "SAY", "Bob")
    flush(h)

    text = h.buffer_text()
    assert text.count("__WCT_END__") == 1, f"{typed!r} put a second end marker in the buffer"
    assert text.endswith("__WCT_END__")
    assert len(h.buffer_entries()) == 2


@pytest.mark.parametrize("typed", ["__WCT_BUF_1__", "__WCT__WCT_BUF_1__", "___WCT_BUF_1__"])
def test_no_arrangement_of_underscores_reconstructs_the_start_marker(typed):
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry(typed, "RAW", "CHANNEL:Trade", "Griefer")
    flush(h)

    assert h.buffer_text().count("__WCT_BUF_") == 1, f"{typed!r} forged a second header"


# ── values that are not strings ──────────────────────────────────────────────


def test_a_number_is_not_mistaken_for_usable_text():
    """`string.len(42)` succeeds in Lua 5.1 — numbers coerce. A probe built only
    on that check therefore waves numbers through, and the caller then indexes
    one as a string."""
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry(12345, "RAW", "SAY", "Bob")
    flush(h)

    assert h.buffer_entries() == []


def test_a_number_author_degrades_instead_of_being_stringified():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hello", "RAW", "SAY", 42)
    flush(h)

    assert h.buffer_entries() == ["1|RAW|SAY|Unknown|hello"]


def test_a_boolean_never_reaches_the_buffer():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry(True, "RAW", "SAY", "Bob")
    flush(h)

    assert h.buffer_entries() == []


def test_a_pipe_in_the_channel_name_does_not_shift_the_fields():
    h = load_companion_buffer()
    h.addon_table.BufferAddEntry("hi", "RAW", "CHANNEL:we|ird", "Bob")
    flush(h)

    entry = h.buffer_entries()[0]
    assert entry.split("|")[2] == "CHANNEL:we/ird"
    assert entry.split("|", 4)[4] == "hi", "the text field must still be the fifth"


def test_hyperlinks_in_the_message_survive_intact():
    """WoW item links are built from pipes; mangling them would break the overlay."""
    h = load_companion_buffer()
    link = "|cffa335ee|Hitem:245770::::::::90:62|h[Alnara's Cane]|h|r"
    h.addon_table.BufferAddEntry(f"wts {link} 500k", "RAW", "CHANNEL:Trade", "Vasya")
    flush(h)

    assert link in h.buffer_entries()[0]


# ── companion disabled ───────────────────────────────────────────────────────


def test_nothing_is_buffered_while_the_companion_is_off():
    h = load_companion_buffer()
    h.lua.execute("BabelChatDB.companion.enabled = false")
    h.addon_table.BufferAddEntry("not for you", "RAW", "SAY", "Bob")
    flush(h)

    assert h.buffer_entries() == []
