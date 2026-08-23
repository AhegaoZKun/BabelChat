"""The gloss against the dictionary that actually ships, on lines players type.

Every other engine test builds a synthetic dictionary of two or three words.
That is right for pinning a rule, and it is how a regression got through: the
rewrite split words on whitespace and then trimmed punctuation off the ends,
which is indistinguishable from correct until a term is glued to another one in
the middle of a token.

"lfm dps/heal" is not an edge case. It is the shape of an LFG line, and nine of
the twenty lines below lost their gloss entirely before this was caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import AddonHarness  # noqa: E402

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"

ALL_CATEGORIES = [
    "showSocial",
    "showZones",
    "showSets",
    "showDungeons",
    "showClasses",
    "showCombat",
    "showTrade",
    "showStats",
    "showGroups",
    "showGuild",
    "showProfessions",
    "showRoles",
    "showStatus",
    "showSlang",
    "showEndgame",
]


@pytest.fixture(scope="module")
def shipped():
    """The engine with every shipped dictionary loaded and every category on."""
    harness = AddonHarness()
    for path in sorted((ADDON_DIR / "Data").glob("*.lua")):
        harness.load(f"Data/{path.name}")
    harness.lua.execute("LibStub = function() return nil end")
    harness.load("DictEngine.lua")
    settings = ", ".join(f"{key} = true" for key in ALL_CATEGORIES)
    harness.lua.execute(
        f"""
        BabelChatDB.dict = {{
            enabled = true, targetLocale = "ruRU", mode = "always",
            chatColor = "808080", settings = {{ {settings} }},
        }}
        BabelChatDB.companion = {{ enabled = false }}
        """
    )
    harness.addon_table.InitLibBabble()
    harness.addon_table.RebuildMasterDict()
    return harness


def glossed_terms(harness, line: str) -> set[str]:
    """The terms the engine reported for a line, as a set of source words."""
    text, changed = harness.addon_table.TranslateChat(line)
    if not changed:
        return set()
    tail = text[len(line):]
    # The gloss is wrapped in the chat colour code the panel configures.
    tail = tail.replace("|cff808080", "").replace("|r", "").strip()
    return {pair.split(" = ")[0].strip() for pair in tail.split(" · ") if " = " in pair}


# ── the shape of a real LFG line ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("lfm dps/heal m+10", {"lfm", "dps", "heal"}),
        ("lf tank/heal", {"lf", "tank", "heal"}),
        ("wts crest/gold", {"wts", "crest"}),
        ("gg,wp all", {"gg", "wp"}),
        ("brb/afk 5 min", {"brb", "afk"}),
        ("ty/gl", {"ty", "gl"}),
        ("hi-ty", {"ty"}),
    ],
)
def test_a_term_glued_to_another_by_punctuation_is_still_glossed(shipped, line, expected):
    """A slash, a comma or a hyphen between two terms is the ordinary way a
    player writes them, and neither one may be lost to it."""
    assert expected <= glossed_terms(shipped, line), f"{line!r} lost terms"


@pytest.mark.parametrize(
    "line",
    [
        "ty for the run",
        "wts bis ring 500k",
        "inv plz dps",
        "ty, that was great",
    ],
)
def test_an_ordinary_line_still_glosses(shipped, line):
    assert glossed_terms(shipped, line), f"{line!r} glossed nothing"


# ── and nothing gained that should not have been ─────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "wait a second",
        "I need to freeze the boss adds",
        "the logging is broken again",
    ],
)
def test_a_term_inside_a_longer_word_is_still_not_matched(shipped, line):
    """Splitting on interior punctuation must not become splitting on nothing.
    Each of these contains a shipped term inside a longer word."""
    words = {word.strip(".,!?").lower() for word in line.split()}
    assert glossed_terms(shipped, line) <= words, f"{line!r} matched inside a word"


def test_nothing_inside_an_item_link_is_glossed(shipped):
    """A finer tokeniser sees more candidate positions inside a link, so the
    protected ranges have to keep holding."""
    link = "|cffa335ee|Hitem:245770::::::::90:62|h[Ring of Power]|h|r"

    _text, changed = shipped.addon_table.TranslateChat(link)

    assert changed is False


def test_a_link_in_a_sentence_leaves_the_sentence_glossable(shipped):
    link = "|cffa335ee|Hitem:245770::::::::90:62|h[Ring of Power]|h|r"

    assert "wts" in glossed_terms(shipped, f"wts {link} 500k")
