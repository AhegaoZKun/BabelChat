"""Which language the interface opens in before anything has been saved.

The app defaults to Russian, which is right for the audience it was written
for and wrong for everyone else on their very first launch: the setup wizard
is the first thing a new player sees, and it was showing them Russian whatever
their machine was set to. The OS locale is the only signal available at that
point, so it decides.

The dangerous half is knowing when NOT to consult it. The wizard reopens
whenever no provider is configured — an expired key, a config migrated from
before the provider registry — and that is not a first run: a real preference
is sitting in the config file. The wizard seeds its dropdown from the language
on screen and writes it back on finish, so guessing there does not merely
mislabel a window, it overwrites the choice the user made.
"""

from __future__ import annotations

import pytest

from app.i18n import UI_LANGUAGES, guess_ui_language, startup_ui_language

#: Every variable the guesser reads. Cleared wholesale per test so the machine
#: running the suite cannot answer for the machine being simulated.
LOCALE_VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


@pytest.fixture
def env(monkeypatch):
    """A machine with no locale opinion, plus a lever to give it one."""
    for var in LOCALE_VARS:
        monkeypatch.delenv(var, raising=False)
    # getlocale() reads the process's own setting, which pytest inherits from
    # whoever ran it. Neutralise it; the tests that care set it themselves.
    monkeypatch.setattr("app.i18n.locale.getlocale", lambda *a: (None, None))
    return monkeypatch


# ── reading the environment ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("es_ES.UTF-8", "ES"),
        ("en_GB", "EN"),
        ("ru_RU.UTF-8", "RU"),
        ("es", "ES"),
        ("en_US.iso88591", "EN"),
        ("ru_RU.UTF-8@cyrillic", "RU"),
    ],
)
def test_a_locale_names_its_language(env, value, expected):
    env.setenv("LANG", value)

    assert guess_ui_language() == expected


def test_language_wins_over_the_lc_variables(env):
    """gettext resolves LANGUAGE first and LANG nearly last. Ordering them the
    other way round is the easy mistake — LANG is the famous one — and it
    silently ignores the variable the user set precisely to be obeyed."""
    env.setenv("LANGUAGE", "es")
    env.setenv("LC_ALL", "ru_RU.UTF-8")
    env.setenv("LC_MESSAGES", "ru_RU.UTF-8")
    env.setenv("LANG", "ru_RU.UTF-8")

    assert guess_ui_language() == "ES"


def test_lc_all_wins_over_lang(env):
    env.setenv("LC_ALL", "es_ES.UTF-8")
    env.setenv("LANG", "en_US.UTF-8")

    assert guess_ui_language() == "ES"


def test_language_is_a_preference_list(env):
    """LANGUAGE holds colon-separated fallbacks, unlike the others. Reading it
    as a single locale would see "de:es" as a language named "de:es"."""
    env.setenv("LANGUAGE", "de:es:ru")

    assert guess_ui_language() == "ES"


def test_an_unsupported_language_does_not_end_the_search(env):
    """A German desktop with LANG=de_DE has no German UI to offer, but its
    LC_MESSAGES may still name one this build has. Returning the default at
    the first variable that says anything would never look."""
    env.setenv("LANGUAGE", "de")
    env.setenv("LANG", "es_ES.UTF-8")

    assert guess_ui_language() == "ES"


# ── falling back ─────────────────────────────────────────────────────────────


def test_no_locale_at_all_falls_back(env):
    assert guess_ui_language() == "RU"


def test_a_language_this_build_cannot_show_falls_back(env):
    """Translating the UI is not the same as translating chat: the app speaks
    twenty languages to WoW and three to its own user."""
    env.setenv("LANG", "ja_JP.UTF-8")

    assert guess_ui_language() == "RU"


@pytest.mark.parametrize("value", ["", "C", "POSIX", "C.UTF-8"])
def test_the_uninformative_locales_say_nothing(env, value):
    env.setenv("LANG", value)

    assert guess_ui_language() == "RU"


def test_the_process_locale_answers_when_the_environment_is_silent(env):
    """Windows sets none of these variables; getlocale() is all there is."""
    env.setattr("app.i18n.locale.getlocale", lambda *a: ("es_ES", "UTF-8"))

    assert guess_ui_language() == "ES"


def test_a_malformed_locale_setting_is_not_a_crash(env):
    """getlocale() raises on a setting it cannot parse. Failing to guess a
    language must not stop the app from starting."""

    def explode(*_args):
        raise ValueError("unknown locale format")

    env.setattr("app.i18n.locale.getlocale", explode)

    assert guess_ui_language() == "RU"


def test_the_caller_chooses_the_fallback(env):
    assert guess_ui_language(default="EN") == "EN"


def test_it_only_ever_returns_a_language_the_ui_has(env):
    """The supported set is UI_LANGUAGES, not a list copied beside it — a
    fourth translation should not need this function edited to be reachable."""
    for value in ("es_ES", "en_US", "ru_RU", "zz_ZZ", "de_DE", ""):
        env.setenv("LANG", value)

        assert guess_ui_language() in UI_LANGUAGES


# ── when the guess is allowed to speak at all ────────────────────────────────


def test_a_saved_choice_is_honoured(env):
    """The ordinary case: the config file exists, so it decides."""
    env.setenv("LANG", "de_DE.UTF-8")

    assert startup_ui_language(config_exists=True, saved="ES") == "ES"


def test_the_guess_does_not_overrule_a_saved_choice(env):
    """The wizard reopens whenever no provider is configured, not only on a
    first run — an expired key does it, so does a config migrated from before
    the provider registry. Treating that as a first run consults the OS locale
    over a preference that already exists, and because the welcome page seeds
    its dropdown from the language on screen and finish() writes it back, a
    user who clicked through would find Spanish saved as German."""
    env.setenv("LANG", "de_DE.UTF-8")

    assert startup_ui_language(config_exists=True, saved="ES") != "RU"
    assert startup_ui_language(config_exists=True, saved="ES") == "ES"


def test_a_first_run_has_nothing_to_honour(env):
    """No config file: `saved` is only the dataclass default, not a choice."""
    env.setenv("LANG", "es_ES.UTF-8")

    assert startup_ui_language(config_exists=False, saved="RU") == "ES"


def test_a_first_run_on_a_machine_with_no_locale_keeps_the_default(env):
    assert startup_ui_language(config_exists=False, saved="RU") == "RU"


def test_both_entry_points_ask_the_same_question():
    """This decision lived in one frontend and not the other for a release,
    which is how Linux users ended up unable to change the interface language
    at all. Whatever it grows into, both callers get the same answer."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    for entry in ("main.py", "main_gtk.py"):
        source = (root / "app" / entry).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "startup_ui_language" in called, f"{entry} decides the startup language by itself"
        assert "guess_ui_language" not in called, f"{entry} reaches past the shared rule"
