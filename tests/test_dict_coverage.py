"""Coverage of the abbreviations players actually type, measured two ways.

The other dictionary tests answer "does the engine still work". This one
answers "does the dictionary still know the words", which is a different
failure: a term that was never added glosses nothing, and nothing in the suite
notices, because every assertion is written against terms that exist.

So there are two halves here, and both are needed:

* a **floor** over a fixed corpus of common WoW chat abbreviations, which
  catches a term being deleted or renamed in bulk; and
* **behavioural** checks that push a realistic chat line through the real Lua
  engine and assert the Russian that comes out. The floor alone would pass on
  an entry whose key is right and whose ruRU cell is wrong, and a wrong Russian
  gloss is worse for this addon's audience than no gloss at all.

The corpus deliberately contains words that are *not* in the dictionary and
must stay out — see ``DELIBERATELY_ABSENT``. "one", "great", "hour" and friends
are ordinary English; glossing them would fire on most sentences a player
types, which is noise, not translation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import AddonHarness  # noqa: E402

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"
DATA_DIR = ADDON_DIR / "Data"

_KEY_RE = re.compile(r'^\s*\["([^"]+)"\]\s*=\s*\{', re.M)

# Abbreviations seen in trade, LFG, guild and party chat. Fixed corpus: a term
# is added here only when it is something players type, never to make the
# number go up.
COMMON_ABBREVIATIONS = [
    # chat courtesies
    "inv", "plz", "pls", "ty", "thx", "np", "yw", "gl", "hf", "glhf", "brb", "bio", "afk", "omw", "wb", "ttyl", "cya",
    # looking for group / trade
    "lfg", "lfm", "lf1m", "lf2m", "wts", "wtb", "wtt", "sold", "cod", "mats", "bis", "lf",
    # roles and combat calls
    "tank", "heal", "dps", "rdps", "mdps", "ranged", "melee", "cc", "kick", "int", "purge", "dispel", "bl", "hero",
    "lust",
    # state during a fight
    "res", "rez", "brez", "combat", "ooc", "oom", "mana", "rage", "energy", "focus",
    # getting there and getting up
    "port", "summ", "portal", "meeting", "stone", "ss", "soulstone",
    # a pull, and how it went
    "gogo", "pull", "wipe", "wiped", "trash", "boss", "adds", "phase", "enrage", "soft", "hard", "heroic", "mythic",
    "normal", "lfr",
    # keystones
    "key", "keystone", "depleted", "timed", "chest", "vault", "great",
    # gear and performance
    "gear", "ilvl", "upgrade", "sim", "simc", "parse", "log", "logs",
    # who you run with
    "pug", "guild", "ginv", "gbank", "raid", "group", "party",
    # specs
    "disc", "holy", "prot", "fury", "arms", "frost", "fire", "arcane",
    # pvp
    "bg", "arena", "rated", "rbg", "conquest", "honor",
    # collections
    "mount", "transmog", "xmog", "toy", "pet", "achiev",
    # time
    "one", "sec", "moment", "min", "mins", "hour",
    # reactions
    "sorry", "sry", "my", "bad", "nvm", "nm", "ez", "gg", "wp", "noob", "newbie",
]

# At least this many of the corpus must be in the shipped data. Not all of it:
# the rest is DELIBERATELY_ABSENT.
COVERAGE_FLOOR = 120

# Ordinary English words and time abbreviations that share a spelling with chat
# shorthand. Each one would match in ordinary sentences — "one moment", "that
# was great", "log out", "in an hour" — so the dictionary leaves them alone.
# "meeting" and "stone" are single words that are only WoW terms together, and
# ship as the multi-word key "meeting stone" instead.
DELIBERATELY_ABSENT = (
    "one",
    "moment",
    "great",
    "hard",
    "soft",
    "my",
    "bad",
    "log",
    "min",
    "mins",
    "hour",
    "meeting",
    "stone",
)

ALL_CATEGORIES = (
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
)


def _shipped_keys() -> set[str]:
    """Every key defined across the shipped Data/*.lua files, lower-cased."""
    keys: set[str] = set()
    for path in sorted(DATA_DIR.glob("*.lua")):
        keys |= {key.lower() for key in _KEY_RE.findall(path.read_text(encoding="utf-8"))}
    return keys


@pytest.fixture(scope="module")
def shipped():
    """The engine with every shipped dictionary loaded, glossing into Russian."""
    harness = AddonHarness()
    for path in sorted(DATA_DIR.glob("*.lua")):
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


def glosses(harness, line: str) -> dict[str, str]:
    """The engine's gloss for a line, as {source term: translation}."""
    text, changed = harness.addon_table.TranslateChat(line)
    if not changed:
        return {}
    tail = text[len(line) :].replace("|cff808080", "").replace("|r", "").strip()
    pairs = [part.split(" = ", 1) for part in tail.split(" · ") if " = " in part]
    return {term.strip(): translation.strip() for term, translation in pairs}


# ── the floor ────────────────────────────────────────────────────────────────


def test_the_corpus_is_the_size_it_claims_to_be():
    """A duplicate slipping into the corpus would quietly lower the bar."""
    assert len(COMMON_ABBREVIATIONS) == len(set(COMMON_ABBREVIATIONS))
    assert len(COMMON_ABBREVIATIONS) == 135


def test_common_abbreviations_clear_the_coverage_floor():
    keys = _shipped_keys()
    missing = sorted(term for term in COMMON_ABBREVIATIONS if term not in keys)
    present = len(COMMON_ABBREVIATIONS) - len(missing)
    assert present >= COVERAGE_FLOOR, (
        f"only {present}/{len(COMMON_ABBREVIATIONS)} common abbreviations are in the "
        f"dictionary (floor is {COVERAGE_FLOOR}); missing: {missing}"
    )


def test_ordinary_english_words_stay_out_of_the_dictionary():
    """The other half of coverage: a term that fires on every sentence is a
    regression, not an improvement."""
    keys = _shipped_keys()
    intruders = sorted(word for word in DELIBERATELY_ABSENT if word in keys)
    assert not intruders, f"ordinary words added as dictionary keys: {intruders}"


# ── behaviour: the term glosses, in a line a player would type ───────────────


@pytest.mark.parametrize(
    ("line", "term", "expected"),
    [
        # group forming
        ("inv plz", "inv", "Инвайт"),
        ("inv plz", "plz", "Плиз"),
        ("lfm mythic", "mythic", "Эпохальный (сложность)"),
        ("heroic clear tonight", "heroic", "Героический (сложность)"),
        ("need rdps", "rdps", "Дальний ДД"),
        ("mdps stack behind", "mdps", "Ближний ДД"),
        # pulling and dying
        ("gogo pull", "gogo", "Погнали"),
        ("gogo pull", "pull", "Пулл (начать бой)"),
        ("boss adds", "boss", "Босс"),
        ("boss adds", "adds", "Аддсы"),
        ("wiped on phase 3", "wiped", "Вайпнулись"),
        ("wiped on phase 3", "phase", "Фаза (боя с боссом)"),
        # travel and resurrection — Russian client terminology
        ("port pls", "port", "Портал"),
        ("ss on tank", "ss", "Камень души"),
        ("soulstone me", "soulstone", "Камень души"),
        ("meeting stone up", "meeting stone", "Камень встречи"),
        # keystones
        ("chest timed", "timed", "Уложились во время (ключ в тайм)"),
        ("depleted, sorry", "depleted", "Ключ слит (не уложились во время)"),
        ("upgrade my ilvl", "upgrade", "Улучшение предмета"),
        # resources and PvP
        ("oom, low mana", "mana", "Мана"),
        ("purge that", "purge", "Пурж (снять баффы)"),
        ("arena rated", "rated", "Рейтинговый"),
        # small talk
        ("cya guys", "cya", "Пока"),
        ("wb!", "wb", "С возвращением"),
        ("sorry about that", "sorry", "Извини"),
        ("brb bio", "bio", "Отойду (перерыв)"),
    ],
)
def test_a_new_term_glosses_into_russian_in_a_real_line(shipped, line, term, expected):
    result = glosses(shipped, line)
    assert term in result, f"{line!r} did not gloss {term!r} (got {result})"
    assert result[term] == expected, f"{term!r} glossed as {result[term]!r}, expected {expected!r}"


# ── behaviour: the words we left out stay silent ────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "one moment please",
        "that was a great run",
        "I will be there in an hour",
        "log out and back in",
    ],
)
def test_an_ordinary_english_sentence_glosses_nothing(shipped, line):
    """Every word here is in COMMON_ABBREVIATIONS as a spelling, and none of
    them is a WoW term. The line must come back untouched."""
    assert glosses(shipped, line) == {}
