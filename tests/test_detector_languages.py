"""The detector loads the languages the app can act on, and no more.

All seventy-five cost 862 MB and 5.8 seconds to load, measured, and the app
builds two detectors — that was most of the gigabyte it sat on and most of the
wait before the overlay appeared. Twenty cost 305 MB and 1.8 seconds.

The saving is only worth having if the detector still answers correctly for the
languages that matter, and if a language outside the list is handled rather than
misread. Both are checked here.
"""

from __future__ import annotations

import pytest
from lingua import Language

from app.detector import SUPPORTED_LANGUAGES, ChatLanguageDetector


def test_every_language_the_app_translates_is_loaded():
    """`_LANG_CODE_TO_LINGUA` is what the interface offers. A language the user
    can pick as their own but the detector cannot recognise would make every
    message they send look foreign."""
    from app.main import _LANG_CODE_TO_LINGUA

    missing = sorted(lang.name for lang in _LANG_CODE_TO_LINGUA.values() if lang not in SUPPORTED_LANGUAGES)

    assert missing == [], f"the app offers these but cannot detect them: {missing}"


def test_the_cyrillic_neighbours_are_loaded():
    """They are not translation targets. They are here because lingua reaches
    for them on short Russian words, and the detector has to recognise the
    mistake in order to correct it — which is how "сука" ended up at a
    translation service."""
    from app.detector import _CYRILLIC_SIBLING_LANGUAGES

    missing = sorted(lang.name for lang in _CYRILLIC_SIBLING_LANGUAGES if lang not in SUPPORTED_LANGUAGES)

    assert missing == [], f"the correction cannot fire for: {missing}"


def test_the_list_is_short_enough_to_be_worth_it():
    """Twenty languages, 305 MB. Seventy-five, 862 MB and three times the wait.
    A list that creeps back towards all of them gives the memory back."""
    assert len(SUPPORTED_LANGUAGES) <= 25, (
        f"{len(SUPPORTED_LANGUAGES)} languages is most of the way back to all of them"
    )
    assert len(set(SUPPORTED_LANGUAGES)) == len(SUPPORTED_LANGUAGES), "a language is listed twice"


def test_nothing_builds_from_every_language():
    """One call is all it takes to put the gigabyte back."""
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent / "app" / "detector.py").read_text(encoding="utf-8")

    assert "from_all_languages" not in source, "the detector is built from every language again"
    assert source.count("from_languages(*SUPPORTED_LANGUAGES)") == 2, "both detectors must use the same list"


# ── and it still detects ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello everyone looking for two more damage dealers", Language.ENGLISH),
        ("ищем двоих в ключ, пишите в шёпот", None),  # own language — nothing to do
        ("wir brauchen noch zwei damage dealer für den schlüssel", Language.GERMAN),
        ("cherchons deux personnes pour la clef mythique", Language.FRENCH),
        ("buscamos dos personas más para la llave mítica", Language.SPANISH),
    ],
    ids=["english", "russian_is_own", "german", "french", "spanish"],
)
def test_the_languages_that_matter_are_still_recognised(text, expected):
    assert ChatLanguageDetector(Language.RUSSIAN).detect(text) == expected


def test_a_language_outside_the_list_is_handed_on_rather_than_guessed():
    """Finnish is not in the list. The wrong outcome would be calling it one of
    the twenty and translating from the wrong source; the right one is admitting
    ignorance, because the translation services auto-detect and are better at it
    than we are."""
    detector = ChatLanguageDetector(Language.RUSSIAN)

    answer = detector.detect("etsimme kahta lisää pelaajaa avaimeen")

    assert answer != Language.RUSSIAN, "a foreign language was mistaken for the user's own and never translated"
    assert answer is not None, "it was dropped instead of being handed to the translator"
