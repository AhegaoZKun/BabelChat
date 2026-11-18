"""On-screen chat recovery: classification, line grouping, wrap joining, diffing.

The screen source sees only rendered text, so these tests pin the three things
that have to come out right for it to be usable: a recognised line becomes the
same log line the addon path would have produced, a wrapped message is read as
one message, and a frame that merely re-shows the chat reports nothing new.
"""

from __future__ import annotations

import pytest

from app.ocr_parse import (
    ChatDiffer,
    boxes_to_lines,
    classify_line,
    frame_from_lines,
    normalise_key,
)
from app.parser import Channel, parse_line

# ── classification ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "token", "author", "text"),
    [
        ("[Guild] Thrall: anyone up for keys?", "GUILD", "Thrall", "anyone up for keys?"),
        ("[Party] Артас: Привет всем", "PARTY", "Артас", "Привет всем"),
        ("[Party Leader] Jaina: pull in 5", "PARTY_LEADER", "Jaina", "pull in 5"),
        ("[Raid] Name-Realm: ready", "RAID", "Name-Realm", "ready"),
        ("[Гильдия] Вася: рейд в 20:00", "GUILD", "Вася", "рейд в 20:00"),
        ("[Hermandad] Pepe: hola", "GUILD", "Pepe", "hola"),
        ("[Instance] Bob: go", "INSTANCE_CHAT", "Bob", "go"),
        ("Sylvanas whispers: you free?", "WHISPER", "Sylvanas", "you free?"),
        ("Вася шепчет: привет", "WHISPER", "Вася", "привет"),
        ("To Thrall: on my way", "WHISPER_INFORM", "Thrall", "on my way"),
        ("Кому Вася: иду", "WHISPER_INFORM", "Вася", "иду"),
        ("Thrall says: hello there", "SAY", "Thrall", "hello there"),
        ("Thrall yells: LOK'TAR", "YELL", "Thrall", "LOK'TAR"),
    ],
)
def test_recognised_lines(line: str, token: str, author: str, text: str) -> None:
    msg = classify_line(line)
    assert msg is not None
    assert (msg.token, msg.author, msg.text) == (token, author, text)


def test_a_clock_in_front_is_ignored() -> None:
    plain = classify_line("[Guild] Thrall: hi")
    for prefix in ("[12:34] ", "12:34:56 ", "[12:34 PM] "):
        stamped = classify_line(prefix + "[Guild] Thrall: hi")
        assert stamped == plain


def test_ocr_reading_the_colon_as_a_semicolon_still_parses() -> None:
    msg = classify_line("[Guild] Thrall; anyone up")
    assert msg is not None and msg.author == "Thrall" and msg.text == "anyone up"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "You receive loot: [Thunderfury, Blessed Blade]",
        "Thrall has come online.",
        "A word: this is not a whisper",          # "A" is only a lead when bracketed
        "High King Anduin says: For the Alliance",  # NPC names have spaces
        "just some text with no structure",
    ],
)
def test_lines_that_are_not_player_chat_are_not_messages(line: str) -> None:
    assert classify_line(line) is None


def test_unclassified_visible_chat_text_is_preserved_for_the_pipeline() -> None:
    """OCR reads the configured chat frame, so unknown formatting must not drop text."""
    frame = frame_from_lines(["A message in an unfamiliar client format"])
    assert len(frame) == 1
    assert frame[0].msg is not None
    assert frame[0].msg.token == "CHANNEL:0:OCR"
    assert frame[0].msg.author == "Unknown"
    assert frame[0].msg.text == "A message in an unfamiliar client format"
    parsed = parse_line(frame[0].msg.to_log_line() or "")
    assert parsed is not None
    assert parsed.channel == Channel.CUSTOM
    assert parsed.text == "A message in an unfamiliar client format"


def test_unclassified_lines_are_emitted_by_the_frame_differ() -> None:
    differ = ChatDiffer()
    first = frame_from_lines(["first visible line"])
    second = frame_from_lines(["first visible line", "second visible line"])
    assert differ.update(first) == []  # initial frame is the history baseline
    new_messages = differ.update(second)
    assert [message.text for message in new_messages] == ["second visible line"]


def test_the_overlays_own_rows_are_never_read_back_as_chat() -> None:
    """If the capture region overlaps the overlay, its rows must be ignored."""
    assert classify_line("[G] Thrall: anyone up for keys?  →  кто-нибудь за ключи?") is None


# ── public channels use the shared classifier ────────────────────────────────


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("[2. Trade - Orgrimmar] Vasya: WTS crest", Channel.TRADE),
        ("[1. General - Durotar] Bob: anyone?", Channel.GENERAL),
        ("[4. LookingForGroup] Bob: LFM m+", Channel.LOOKING_FOR_GROUP),
        ("[2. Торговля - Оргриммар] Вася: продам", Channel.TRADE),
        ("[5. mychannel] Bob: hi", Channel.CUSTOM),
    ],
)
def test_public_channels_land_where_the_addon_path_would_put_them(line: str, expected: Channel) -> None:
    msg = classify_line(line)
    assert msg is not None
    parsed = parse_line(msg.to_log_line() or "")
    assert parsed is not None and parsed.channel == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("[Ġuild] Bob: gg", Channel.GUILD),                         # a stray mark on a Latin letter
        ("[Gulld] Bob: gg", Channel.GUILD),                         # l read for i
        ("[Pa rty] Bob: gg", Channel.PARTY),                        # a space OCR put inside the word
        ("[Pxxty] Bob: gg", Channel.CUSTOM),                        # too far gone to guess at
        ("[2. Trode - Orgrimmar] Bob: WTS", Channel.TRADE),
        ("[2. Genaral - Durotar] Bob: hi", Channel.GENERAL),
    ],
)
def test_a_misread_channel_tag_snaps_to_the_nearest_known_name(line: str, expected: Channel) -> None:
    msg = classify_line(line)
    assert msg is not None
    parsed = parse_line(msg.to_log_line() or "")
    assert parsed is not None and parsed.channel == expected


@pytest.mark.parametrize("name", ["TradeHub", "my trade channel", "GeneralChat", "lfg-wow"])
def test_a_player_made_channel_is_custom_even_when_named_like_a_builtin(name: str) -> None:
    """The addon path settled this: only the built-in channels are Trade/General/...

    A channel someone created called "TradeHub" must not land in Trade, which the
    user may have switched on, because a word in its name matched.
    """
    msg = classify_line(f"[5. {name}] Bob: hi")
    assert msg is not None
    parsed = parse_line(msg.to_log_line() or "")
    assert parsed is not None and parsed.channel == Channel.CUSTOM


def test_cyrillic_is_not_damaged_by_the_diacritic_cleanup() -> None:
    """"й" is a base letter plus a mark; stripping marks naively would change the word."""
    msg = classify_line("[1. Общий - Дуротар] Вася: привет")
    assert msg is not None
    parsed = parse_line(msg.to_log_line() or "")
    assert parsed is not None and parsed.channel == Channel.GENERAL


@pytest.mark.parametrize(
    ("line", "channel", "author", "text"),
    [
        ("[Guild] Thrall: hi", Channel.GUILD, "Thrall", "hi"),
        ("Sylvanas whispers: yo", Channel.WHISPER_FROM, "Sylvanas", "yo"),
        ("To Thrall: yo", Channel.WHISPER_TO, "Thrall", "yo"),
        ("Thrall says: yo", Channel.SAY, "Thrall", "yo"),
        ("[Party] Артас: Привет", Channel.PARTY, "Артас", "Привет"),
    ],
)
def test_every_recognised_line_round_trips_through_the_real_parser(
    line: str, channel: Channel, author: str, text: str
) -> None:
    msg = classify_line(line)
    assert msg is not None
    parsed = parse_line(msg.to_log_line() or "")
    assert parsed is not None
    assert (parsed.channel, parsed.author, parsed.text) == (channel, author, text)


# ── boxes -> lines ───────────────────────────────────────────────────────────


def _box(x0: float, y0: float, x1: float, y1: float, text: str, conf: float = 0.95):
    return (x0, y0, x1, y1, text, conf)


def test_boxes_on_one_line_are_joined_left_to_right() -> None:
    boxes = [
        _box(120, 0, 300, 14, "Thrall: hi"),
        _box(0, 1, 100, 15, "[Guild]"),
        _box(0, 20, 200, 34, "second line"),
    ]
    assert boxes_to_lines(boxes) == ["[Guild] Thrall: hi", "second line"]


def test_low_confidence_boxes_are_dropped() -> None:
    boxes = [_box(0, 0, 100, 14, "real text"), _box(0, 20, 100, 34, "noise", conf=0.1)]
    assert boxes_to_lines(boxes) == ["real text"]


def test_no_boxes_no_lines() -> None:
    assert boxes_to_lines([]) == []


# ── wrapped lines ────────────────────────────────────────────────────────────


def test_a_wrapped_message_is_one_entry() -> None:
    frame = frame_from_lines([
        "[Guild] Thrall: this is a long message that the chat frame",
        "had to wrap onto a second line",
        "[Guild] Jaina: short",
    ])
    assert len(frame) == 2
    assert frame[0].msg is not None
    assert frame[0].msg.text == "this is a long message that the chat frame had to wrap onto a second line"
    assert frame[1].msg is not None and frame[1].msg.author == "Jaina"


def test_a_system_line_is_not_glued_onto_the_message_above_it() -> None:
    frame = frame_from_lines([
        "[Guild] Thrall: gz",
        "You receive loot: [Thunderfury]",
    ])
    assert len(frame) == 2
    assert frame[0].msg is not None and frame[0].msg.text == "gz"
    assert frame[1].msg is None


# ── diffing ──────────────────────────────────────────────────────────────────

_CHAT = [
    "[Guild] A: one", "[Guild] B: two", "[Guild] C: three", "[Guild] D: four", "[Guild] E: five",
]


def _frame(lines: list[str]):
    return frame_from_lines(lines)


def test_the_first_frame_reports_nothing() -> None:
    """Whatever was on screen before the app started is not new."""
    assert ChatDiffer().update(_frame(_CHAT)) == []


def test_an_unchanged_frame_reports_nothing() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    assert d.update(_frame(_CHAT)) == []


def test_a_scrolled_frame_reports_only_the_new_message() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    scrolled = _CHAT[1:] + ["[Guild] F: six"]
    new = d.update(_frame(scrolled))
    assert [m.text for m in new] == ["six"]


def test_several_new_messages_arrive_in_order() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    scrolled = _CHAT[3:] + ["[Guild] F: six", "[Guild] G: seven", "[Guild] H: eight"]
    assert [m.text for m in d.update(_frame(scrolled))] == ["six", "seven", "eight"]


def test_a_window_that_is_not_full_yet_grows_without_scrolling() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT[:3]))
    assert [m.text for m in d.update(_frame(_CHAT[:5]))] == ["four", "five"]


def test_one_misread_line_does_not_replay_the_chat() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    noisy = ["[Guild] A: one", "[Guild] B: tw0", "[Guild] C: three", "[Guild] D: four", "[Guild] E: flve"]
    assert d.update(_frame(noisy)) == []


def test_a_misread_line_in_the_overlap_still_finds_the_new_message() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    scrolled = ["[Guild] B: tw0", "[Guild] C: three", "[Guild] D: four", "[Guild] E: five", "[Guild] F: six"]
    assert [m.text for m in d.update(_frame(scrolled))] == ["six"]


def test_a_burst_longer_than_the_window_is_all_new_but_never_replays_old_lines() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    burst = ["[Guild] P: a1", "[Guild] Q: a2", "[Guild] R: a3", "[Guild] S: a4", "[Guild] T: a5"]
    assert [m.text for m in d.update(_frame(burst))] == ["a1", "a2", "a3", "a4", "a5"]
    # A garbled frame that happens to contain old lines must not re-deliver them.
    replay = ["[Guild] A: one", "[Guild] B: two", "[Guild] U: fresh"]
    assert [m.text for m in d.update(_frame(replay))] == ["fresh"]


def test_a_cleared_or_empty_frame_changes_nothing() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    assert d.update([]) == []
    assert d.update(_frame(_CHAT)) == []


def test_system_lines_scroll_with_the_chat_but_are_never_reported() -> None:
    d = ChatDiffer()
    base = ["[Guild] A: one", "You receive loot: [X]", "[Guild] B: two"]
    d.update(_frame(base))
    scrolled = ["You receive loot: [X]", "[Guild] B: two", "Thrall has come online.", "[Guild] C: three"]
    assert [m.text for m in d.update(_frame(scrolled))] == ["three"]


def test_reset_makes_the_next_frame_a_baseline_again() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    d.reset()
    assert d.update(_frame(_CHAT[1:] + ["[Guild] F: six"])) == []


# ── keys ─────────────────────────────────────────────────────────────────────


def test_a_timestamp_does_not_change_a_lines_key() -> None:
    assert normalise_key("[12:34] [Guild] Thrall: hi") == normalise_key("[Guild] Thrall: hi")


# ── chats that insert new lines at the top ───────────────────────────────────
#
# The default chat adds a line at the bottom and scrolls the rest up. A chat set
# to insert at the top does the reverse: the newest line is first on screen and
# everything else is pushed down. The differ finds out which by itself.

_TOP = [
    "[Guild] E: five", "[Guild] D: four", "[Guild] C: three", "[Guild] B: two", "[Guild] A: one",
]  # newest first


def test_a_chat_that_inserts_at_the_top_reports_the_new_line() -> None:
    d = ChatDiffer()
    d.update(_frame(_TOP))
    pushed = ["[Guild] F: six", *_TOP[:-1]]
    assert [m.text for m in d.update(_frame(pushed))] == ["six"]
    assert d.direction == "top"


def test_new_lines_from_the_top_are_delivered_oldest_first() -> None:
    d = ChatDiffer()
    d.update(_frame(_TOP))
    pushed = ["[Guild] H: eight", "[Guild] G: seven", "[Guild] F: six", *_TOP[:-3]]
    assert [m.text for m in d.update(_frame(pushed))] == ["six", "seven", "eight"]


def test_a_top_inserting_window_that_is_not_full_yet_grows() -> None:
    d = ChatDiffer()
    d.update(_frame(_TOP[2:]))                       # three lines
    assert [m.text for m in d.update(_frame(_TOP))] == ["four", "five"]
    assert d.direction == "top"


def test_the_default_order_is_recognised_as_bottom() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    d.update(_frame(_CHAT[1:] + ["[Guild] F: six"]))
    assert d.direction == "bottom"


def test_a_bottom_inserting_chat_that_is_still_filling_is_not_mistaken_for_top() -> None:
    """With room to spare, the wrong order can match by finding nothing new."""
    d = ChatDiffer()
    d.update(_frame(_CHAT[:3]))
    assert [m.text for m in d.update(_frame(_CHAT[:4]))] == ["four"]
    assert [m.text for m in d.update(_frame(_CHAT[:5]))] == ["five"]
    assert d.direction == "bottom"


def test_one_misread_line_in_a_top_inserting_chat_does_not_replay_it() -> None:
    d = ChatDiffer()
    d.update(_frame(_TOP))
    d.update(_frame(["[Guild] F: six", *_TOP[:-1]]))
    noisy = ["[Guild] F: six", "[Guild] E: flve", "[Guild] D: four", "[Guild] C: thrce", "[Guild] B: two"]
    assert d.update(_frame(noisy)) == []


def test_an_unchanged_top_inserting_frame_reports_nothing() -> None:
    d = ChatDiffer()
    d.update(_frame(_TOP))
    d.update(_frame(["[Guild] F: six", *_TOP[:-1]]))
    assert d.update(_frame(["[Guild] F: six", *_TOP[:-1]])) == []


def test_a_burst_longer_than_the_window_in_a_top_chat_keeps_its_order_and_replays_nothing() -> None:
    d = ChatDiffer()
    d.update(_frame(_TOP))
    d.update(_frame(["[Guild] F: six", *_TOP[:-1]]))
    burst = ["[Guild] T5: n5", "[Guild] T4: n4", "[Guild] T3: n3", "[Guild] T2: n2", "[Guild] T1: n1"]
    assert [m.text for m in d.update(_frame(burst))] == ["n1", "n2", "n3", "n4", "n5"]


def test_the_direction_is_forgotten_after_repeated_misses_and_learned_again() -> None:
    d = ChatDiffer()
    d.update(_frame(_CHAT))
    d.update(_frame(_CHAT[1:] + ["[Guild] F: six"]))
    assert d.direction == "bottom"
    # The user switched the chat to insert at the top: three unrelated frames...
    for i in range(3):
        d.update(_frame([f"[Guild] X{i}: a{i}", f"[Guild] Y{i}: b{i}", f"[Guild] Z{i}: c{i}"]))
    assert d.direction is None
    # ...and then it behaves as a top-inserting chat.
    base = ["[Guild] M3: m3", "[Guild] M2: m2", "[Guild] M1: m1"]
    d.update(_frame(base))
    assert [m.text for m in d.update(_frame(["[Guild] M4: m4", *base[:-1]]))] == ["m4"]
    assert d.direction == "top"


# ── the baseline ─────────────────────────────────────────────────────────────


def test_the_baseline_is_what_was_on_screen_but_is_not_reported_as_new() -> None:
    d = ChatDiffer()
    assert d.baseline() == []
    assert d.update(_frame(_CHAT)) == []
    assert [m.text for m in d.baseline()] == ["one", "two", "three", "four", "five"]
