"""The string table: complete, reachable, and actually used.

Three failure modes, all of which shipped. A key defined in English but not in
Russian shows English inside a Russian interface. A key that no longer exists
renders as its own name — `tr` returns the key on a miss, so `settings_tab_general`
appears on screen looking almost like a label. And a string written straight
into a widget never reaches the table at all, which is how the settings dialog
ended up half in English.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.i18n import _STRINGS, tr

LANGUAGES = ("EN", "RU", "ES")
APP = Path(__file__).resolve().parent.parent / "app"

# The Qt surfaces a Russian-speaking player actually reads.
LOCALISED_MODULES = (
    "settings_dialog.py",
    "setup_wizard.py",
    "provider_settings_qt.py",
    "wizard_pages_qt.py",
    "about_tab_qt.py",
)


def source(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


# ── completeness ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_key_exists_in_every_language(language):
    missing = sorted(key for key, values in _STRINGS.items() if language not in values)
    assert missing == [], f"{language} is missing: {missing}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_translation_is_blank(language):
    blank = sorted(key for key, values in _STRINGS.items() if not values.get(language, "").strip())
    assert blank == [], f"{language} has empty strings: {blank}"


def test_a_placeholder_is_present_in_every_language_that_has_the_key():
    """A format field dropped from one translation raises when that language is
    selected, and only then — the kind of bug that ships to one audience."""
    mismatched = []
    for key, values in _STRINGS.items():
        fields = {lang: set(re.findall(r"\{(\w+)\}", text)) for lang, text in values.items()}
        reference = fields.get("EN", set())
        for lang, found in fields.items():
            if found != reference:
                mismatched.append((key, lang, sorted(reference), sorted(found)))
    assert mismatched == [], f"placeholders differ between languages: {mismatched}"


# ── reachability ─────────────────────────────────────────────────────────────


def referenced_keys() -> set[str]:
    keys: set[str] = set()
    for path in APP.rglob("*.py"):
        # i18n.py defines tr; its docstrings show `tr("key")` as an example.
        if path.name == "i18n.py":
            continue
        keys.update(re.findall(r'tr\(\s*"([a-z0-9_.]+)"', path.read_text(encoding="utf-8")))
    return keys


def test_every_key_the_code_asks_for_exists():
    """`tr` returns the key itself on a miss, so a typo reaches the screen as a
    plausible-looking label rather than as an error."""
    unknown = sorted(referenced_keys() - set(_STRINGS))
    assert unknown == [], f"asked for but not defined: {unknown}"


def test_a_missing_key_is_visible_rather_than_silent():
    """Pinning the current behaviour: it returns the key. That is survivable
    only because the test above exists."""
    assert tr("no.such.key.exists") == "no.such.key.exists"


def test_a_string_with_a_placeholder_renders():
    assert "7" in tr("settings.privacy.cleared", n=7)


def test_a_missing_placeholder_argument_does_not_crash_the_interface():
    """A caller that forgets an argument should lose the substitution, not the
    window."""
    rendered = tr("settings.privacy.cleared")
    assert isinstance(rendered, str) and rendered


# ── strings that never reached the table ─────────────────────────────────────

# Text that is not language: a brand name, a URL, a placeholder shown as an
# example, or styling. Anything else in a widget constructor is a missed string.
_NOT_LANGUAGE = re.compile(
    r"^(?:"
    r"\s*|[\W\d_]+|"  # punctuation, digits, symbols, icons
    r"https?://\S+|"  # links
    r"[A-Za-z-]+\.(?:py|json|log|pem|ico|png|exe)|"  # filenames
    r"[A-Za-z]:[/\\].*|"  # example paths shown as placeholders
    r"(?:DeepL|GigaChat|MyMemory|Microsoft Translator|BabelChat|WoW|Azure|Sber)"
    r")$"
)

_WIDGET_TEXT = re.compile(
    r"(?:QCheckBox|QLabel|QPushButton|QGroupBox|setText|setToolTip|setPlaceholderText|addItem)"
    r"\(\s*\"([^\"]{4,})\""
)


@pytest.mark.parametrize("module", LOCALISED_MODULES)
def test_no_user_facing_string_is_written_straight_into_a_widget(module):
    """Seven of these shipped in the settings dialog — "Priority:", "Get key",
    "(other acts as fallback)" — and produced an interface half in English for
    every Russian-speaking user."""
    hardcoded = [
        text
        for text in _WIDGET_TEXT.findall(source(module))
        if not _NOT_LANGUAGE.match(text) and not text.startswith("<")
    ]
    assert hardcoded == [], f"{module} writes these past i18n: {hardcoded}"


def test_provider_notes_and_labels_go_through_the_table():
    """A provider declares its own copy, and that copy is shown to the user. If
    it bypasses the table, adding a provider adds English to a Russian screen."""
    from app.translators import all_providers

    untranslated = []
    for spec in all_providers():
        for text in (spec.note, *(f.label for f in spec.fields)):
            if text and text not in _STRINGS and not _is_translated_value(text):
                untranslated.append((spec.id, text[:50]))
    assert untranslated == [], f"provider copy not in the string table: {untranslated}"


def _is_translated_value(text: str) -> bool:
    """True if the text is a rendered value of some key in the table."""
    return any(text in values.values() for values in _STRINGS.values())
