"""The in-chat gloss: what it matches, in what order, and how it reads.

The engine's output was the project owner's headline complaint, and every part
of it was a separate defect: the same term repeated once per occurrence, entries
ordered by dictionary traversal rather than by the message, alternatives printed
verbatim ("Спасибо/спс"), the same arrow glyph used for two different meanings,
and a newline that doubled the height of every glossed line.
"""

from __future__ import annotations

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import AddonHarness  # noqa: E402


def build(dictionary: dict[str, str], *, babble: dict[str, str] | None = None, **db_overrides):
    """A harness with one synthetic category loaded and the engine rebuilt."""
    harness = AddonHarness()
    lua = harness.lua

    entries = lua.eval("{}")
    for term, translation in dictionary.items():
        entries[term] = lua.table_from({"enUS": translation})
    harness.addon_table.SocialDict = entries

    harness.load("DictEngine.lua")

    lua.execute(
        """
        BabelChatDB.dict = {
            enabled = true,
            targetLocale = "enUS",
            mode = "always",
            chatColor = "808080",
            settings = { showSocial = true, showZones = true, showSets = true },
        }
        BabelChatDB.companion = { enabled = false }
        """
    )
    for key, value in db_overrides.items():
        lua.globals().BabelChatDB.dict[key] = value

    _install_libbabble(harness, babble or {})
    harness.addon_table.InitLibBabble()
    harness.addon_table.RebuildMasterDict()
    return harness


def _install_libbabble(harness, zones: dict[str, str]) -> None:
    """A LibStub that answers the way the real one does.

    The engine asks LibStub for the library, asks the library for its unstrict
    lookup table, and indexes that. Stubbing LibStub to return nil — which the
    fixture used to do — leaves the index empty, so every babble test passed by
    exercising nothing at all.
    """
    lua = harness.lua
    table = lua.eval("{}")
    for english, localised in zones.items():
        table[english] = localised
    harness.lua.globals()._babble_zones = table
    lua.execute(
        """
        local library = {
            GetUnstrictLookupTable = function() return _babble_zones end,
        }
        LibStub = function(name, silent)
            if name == "LibBabble-SubZone-3.0" then return library end
            if silent then return nil end
            return nil
        end
        """
    )


def gloss(harness, text: str) -> tuple[str, bool]:
    result, changed = harness.addon_table.TranslateChat(text)
    return result, bool(changed)


# ── the reported symptoms ────────────────────────────────────────────────────


def test_a_repeated_term_is_glossed_once():
    """ "ty ty ty" produced three identical entries."""
    h = build({"ty": "спасибо"})

    text, changed = gloss(h, "ty ty ty for the run")

    assert changed is True
    assert text.count("ty = спасибо") == 1


def test_entries_follow_the_order_of_the_message():
    """They followed the order of the dictionary's hash traversal, so the gloss
    read in a different order from the sentence above it."""
    h = build({"wts": "продаю", "bis": "лучшее в слоте", "ty": "спасибо"})

    text, _ = gloss(h, "ty for the wts bis")

    assert text.index("ty =") < text.index("wts =") < text.index("bis =")


def test_an_alternatives_list_shows_only_the_first():
    """The data says "Спасибо/спс" — a lexicographer's note, printed verbatim."""
    h = build({"ty": "Спасибо/спс"})

    text, _ = gloss(h, "ty")

    assert "Спасибо" in text
    assert "/спс" not in text


def test_the_gloss_stays_on_one_line():
    """A newline doubled the height of every glossed message and broke
    copy-chat, which is what made a busy Trade channel unreadable."""
    h = build({"ty": "спасибо"})

    text, _ = gloss(h, "ty")

    assert "\n" not in text
    assert text.startswith("ty")


def test_the_arrow_glyph_is_gone():
    """It meant both "annotation follows" and "maps to" in the same line."""
    h = build({"ty": "спасибо"})

    text, _ = gloss(h, "ty")

    assert "→" not in text


def test_a_long_list_is_capped_with_a_count():
    """Which three matters: the cap keeps the first three the message says, so
    the gloss still reads alongside the sentence above it. Counting three pairs
    and a "+2" would pass on any three."""
    h = build({"a": "1", "b": "2", "c": "3", "d": "4", "e": "5"})

    text, _ = gloss(h, "e d c b a")

    assert text.count(" = ") == 3
    assert "+2" in text
    assert "e = 5" in text
    assert "d = 4" in text
    assert "c = 3" in text
    assert "b = 2" not in text
    assert "a = 1" not in text


# ── word boundaries ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("term", "message"),
    [
        ("sec", "wait a second"),
        ("ez", "I need to freeze the boss"),
        ("on it", "money on itself"),
        ("gg", "the logging is broken"),
    ],
)
def test_a_term_inside_a_longer_word_is_not_matched(term, message):
    h = build({term: "TRANSLATED"})

    text, changed = gloss(h, message)

    assert changed is False, f"{term!r} matched inside {message!r}"
    assert text == message


def test_a_term_next_to_punctuation_is_still_matched():
    h = build({"ty": "спасибо"})

    _text, changed = gloss(h, "ty, that was great")

    assert changed is True


def test_a_cyrillic_term_is_not_matched_inside_a_longer_cyrillic_word():
    """Lua patterns are byte-based and %w is ASCII-only, so a naive boundary
    check treats every Cyrillic byte as a separator."""
    h = build({"да": "yes"})

    _text, changed = gloss(h, "надо подождать")

    assert changed is False


# ── multi-word phrases ───────────────────────────────────────────────────────


def test_a_phrase_is_matched_as_a_whole():
    h = build({"raid finder": "поиск рейда"})

    text, changed = gloss(h, "queue for raid finder now")

    assert changed is True
    assert "raid finder = поиск рейда" in text


def test_the_longer_phrase_wins_at_the_same_position():
    h = build({"raid": "рейд", "raid finder": "поиск рейда"})

    text, _ = gloss(h, "raid finder")

    assert "raid finder = поиск рейда" in text
    assert text.count(" = ") == 1


# ── links and colour codes ───────────────────────────────────────────────────


def test_nothing_inside_an_item_link_is_glossed():
    h = build({"ring": "кольцо"})
    link = "|cffa335ee|Hitem:245770::::::::90:62|h[Ring of Power]|h|r"

    text, changed = gloss(h, f"wts {link}")

    assert changed is False
    assert text == f"wts {link}"


def test_a_named_colour_code_does_not_swallow_the_rest_of_the_line():
    """The old range finder looked for the closing |r ten characters in, which
    is right for |cffRRGGBB and wrong for the named form — so everything after
    a short block was treated as inside it and never matched."""
    h = build({"ty": "спасибо"})

    _text, changed = gloss(h, "|cnRED_FONT_COLOR:warning|r ty")

    assert changed is True, "a term after a named colour block must still match"


def test_a_message_that_is_only_a_link_is_returned_untouched():
    h = build({"ring": "кольцо"})
    link = "|cffa335ee|Hitem:1::::::::90:62|h[Ring]|h|r"

    text, changed = gloss(h, link)

    assert changed is False
    assert text == link


# ── nothing to say ───────────────────────────────────────────────────────────


def test_a_message_with_no_matches_comes_back_byte_for_byte():
    h = build({"ty": "спасибо"})

    text, changed = gloss(h, "nothing here matches anything")

    assert changed is False
    assert text == "nothing here matches anything"


def test_a_non_string_does_not_raise():
    h = build({"ty": "спасибо"})

    _text, changed = gloss(h, 42)

    assert changed is False


# ── who speaks: the addon or the overlay ─────────────────────────────────────


def test_the_gloss_stays_quiet_when_the_companion_is_set_up():
    """Both were printing an answer for the same message: a term list in chat
    and a full sentence in the overlay, differing in wording."""
    h = build({"ty": "спасибо"}, mode="auto")
    h.lua.execute("BabelChatDB.companion.enabled = true")

    _text, changed = gloss(h, "ty")

    assert changed is False


def test_the_gloss_speaks_when_the_companion_is_not_set_up():
    h = build({"ty": "спасибо"}, mode="auto")

    _text, changed = gloss(h, "ty")

    assert changed is True


def test_always_overrides_the_companion():
    h = build({"ty": "спасибо"}, mode="always")
    h.lua.execute("BabelChatDB.companion.enabled = true")

    _text, changed = gloss(h, "ty")

    assert changed is True


def test_the_mode_defaults_to_auto_when_the_saved_config_predates_it():
    h = build({"ty": "спасибо"})
    h.lua.execute("BabelChatDB.dict.mode = nil; BabelChatDB.companion.enabled = true")

    _text, changed = gloss(h, "ty")

    assert changed is False, "an older config must not start double-printing"


# ── the punctuation a Russian message is actually written with ───────────────


@pytest.mark.parametrize(
    "message",
    [
        "«спс» за помощь",
        "спс — очень выручил",
        "спс… побежал",
        "спс\u00a0за инвайт",  # a non-breaking space, which Word and Discord insert
        "(спс)",
        "спс!",
        "…спс",
    ],
)
def test_a_term_wrapped_in_russian_punctuation_is_still_matched(message):
    """Word boundaries were decided one byte at a time, and every byte above 127
    counted as a letter. Guillemets, the em dash, the ellipsis and the
    non-breaking space are all multi-byte, so each of them glued itself to the
    word and the term stopped matching — on exactly the punctuation a Russian
    player types."""
    h = build({"спс": "thanks"})

    _text, changed = gloss(h, message)

    assert changed is True, f"nothing matched in {message!r}"


def test_a_capitalised_russian_term_matches_its_lower_case_key():
    """Lua's string.lower is ASCII-only, so "Спс" never met the key "спс" — and
    a message starts with a capital letter."""
    h = build({"спс": "thanks"})

    _text, changed = gloss(h, "Спс за помощь")

    assert changed is True


# Р is the first letter whose lower case crosses into a different UTF-8 lead
# byte, so a fold that stops one code short of it looks right everywhere else.
@pytest.mark.parametrize("word", ["Ёлка", "Ярость", "Отряд", "Рейд", "Победа", "Ясно"])
def test_the_whole_cyrillic_alphabet_folds(word):
    """Р-Я cross into a different UTF-8 lead byte and Ё sits outside the block
    altogether, so a fold that only handles А-П works for half the alphabet."""
    h = build({word.lower(): "TRANSLATED"})

    _text, changed = gloss(h, f"{word} тут")

    assert changed is True


def test_punctuation_does_not_make_a_term_match_inside_a_word():
    """Trimming must not go so far that boundaries stop being checked."""
    h = build({"да": "yes"})

    _text, changed = gloss(h, "«надо» подождать")

    assert changed is False


def test_a_phrase_wrapped_in_punctuation_is_not_downgraded_to_its_first_word():
    """Phrases were looked up on the raw token, so "«raid finder»" missed the
    phrase and fell through to the single word — glossing "raid", which is a
    shorter answer and the wrong one."""
    h = build({"raid": "рейд", "raid finder": "поиск рейда"})

    text, _ = gloss(h, "«raid finder» сейчас")

    assert "raid finder = поиск рейда" in text
    assert "raid = рейд" not in text


# ── zone and item-set names from LibBabble ───────────────────────────────────


def test_a_zone_name_is_glossed_from_the_babble_table():
    """LibBabble is where every localised zone and item-set name comes from, and
    it had no coverage at all: the fixture stubbed LibStub to return nil, so the
    index the tests appeared to exercise was always empty."""
    h = build({}, babble={"Elwynn Forest": "Элвиннский лес"})

    text, changed = gloss(h, "meet me in Elwynn Forest")

    assert changed is True
    assert "Elwynn Forest = Элвиннский лес" in text


def test_a_babble_entry_that_translates_to_itself_is_not_shown():
    """A partially localised table returns English for the entries nobody got
    to, and "Elwynn Forest = Elwynn Forest" is noise in a chat line."""
    h = build({}, babble={"Stormwind City": "Stormwind City"})

    _text, changed = gloss(h, "heading to Stormwind City")

    assert changed is False


def test_a_very_short_babble_entry_is_not_indexed():
    """Two- and three-letter zone names collide with ordinary words far more
    often than they help."""
    h = build({}, babble={"Orb": "Сфера"})

    _text, changed = gloss(h, "grab the Orb")

    assert changed is False


def test_the_longest_babble_name_wins_at_the_same_position():
    h = build({}, babble={"Elwynn Forest": "Элвиннский лес", "Elwynn Forest Mine": "Шахта Элвиннского леса"})

    text, _ = gloss(h, "at Elwynn Forest Mine")

    assert "Элвиннского леса" in text
    assert text.count(" = ") == 1


def test_a_babble_name_is_still_matched_on_a_word_boundary():
    h = build({}, babble={"Elwynn Forest": "Элвиннский лес"})

    _text, changed = gloss(h, "NotElwynn Forest")

    assert changed is False


def test_the_dictionary_and_the_babble_table_can_both_speak():
    h = build({"ty": "спасибо"}, babble={"Elwynn Forest": "Элвиннский лес"})

    text, _ = gloss(h, "ty for the run to Elwynn Forest")

    assert "ty = спасибо" in text
    assert "Elwynn Forest = Элвиннский лес" in text
