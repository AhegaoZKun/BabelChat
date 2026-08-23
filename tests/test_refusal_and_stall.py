"""Three things one live session turned up.

A refusal shown as a translation. GigaChat answers 200 with a paragraph about
what it will not discuss, and that paragraph went into the overlay where the
translation belongs — for a one-word message.

A word sent abroad for no reason. lingua read "сука" as Belarusian, so it was
treated as a foreign language and sent to a translation service. Bulgarian and
Ukrainian were already excused for a Russian-speaking user; Belarusian was not,
for no reason anyone wrote down.

And the stall behind "it works for a minute and then stops". The native scanner
caches the address it found the buffer at and reads from it on every poll. When
Lua's garbage collector moves the buffer, the bytes left behind still parse —
same markers, same last sequence — so the fast path reads that ghost forever
and never rescans. Nothing arrives again until the app is restarted.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.translators.gigachat_provider import REFUSED, looks_like_a_refusal

REFUSAL = (
    "Как и любая языковая модель, GigaChat не обладает собственным мнением и не транслирует "
    "мнение своих разработчиков. Ответ сгенерирован нейросетевой моделью, обученной на открытых "
    "данных, в которых может содержаться неточная или ошибочная информация. Во избежание "
    "неправильного толкования, разговоры на некоторые темы временно ограничены."
)


# ── a refusal is not a translation ───────────────────────────────────────────


def test_the_paragraph_the_overlay_showed_is_recognised():
    assert looks_like_a_refusal("сука", REFUSAL) is True


def test_a_real_translation_about_language_models_is_not_a_refusal():
    """Matching on the wording alone would flag this, and someone discussing
    machine translation in guild chat is not a rare event."""
    source = "as a language model it translates text between languages"
    reply = "как языковая модель, она переводит текст между языками"

    assert looks_like_a_refusal(source, reply) is False


def test_a_short_reply_is_never_a_refusal():
    """Length is the other half of the test: a refusal is a canned paragraph
    and dwarfs the chat line that provoked it."""
    assert looks_like_a_refusal("hello", "привет") is False


def test_a_terse_phrase_rendered_at_length_is_not_a_refusal():
    """The length test on its own would flag this, and an LLM expanding two
    letters into a polite sentence is exactly what an LLM does. Both halves
    have to be present, which is what this pins."""
    source = "ty"
    reply = "спасибо большое, это было очень приятно с вашей стороны"

    assert len(reply) > len(source) * 3 + 40, "the fixture no longer exercises the length branch"
    assert looks_like_a_refusal(source, reply) is False


def test_an_ordinary_translation_of_a_long_message_survives():
    long_source = "we need two more damage dealers for the mythic plus key, whisper me if interested"
    long_reply = "нам нужны ещё два дамагера на ключ, пишите в шёпот если интересно"

    assert looks_like_a_refusal(long_source, long_reply) is False


def test_the_provider_reports_a_refusal_rather_than_passing_it_on():
    """The whole point: a 200 carrying a refusal must leave the provider as a
    failure, so the registry tries the next one — MyMemory has no opinion about
    the word it was handed."""
    from app.translators.base import RetryPolicy
    from app.translators.gigachat_provider import CHAT_URL, OAUTH_URL, GigaChatBackend
    from tests.translator_fakes import FakeResponse, FakeSession

    backend = GigaChatBackend("key", retry=RetryPolicy(attempts=1, delay=0))
    session = FakeSession()
    backend._session = session
    session.script(OAUTH_URL, FakeResponse(200, {"access_token": "t", "expires_at": 9e12}))
    session.script(CHAT_URL, FakeResponse(200, {"choices": [{"message": {"content": REFUSAL}}]}))

    result = backend.translate("сука", "RU")

    assert result.success is False
    assert result.error == REFUSED
    assert result.translated == "сука", "the original has to survive"
    assert "языковая модель" not in (result.translated or "")


def test_the_overlay_has_something_short_to_say_instead():
    from app.locales import LANGUAGE_MODULES

    for language, module in LANGUAGE_MODULES.items():
        assert "overlay.refused" in module.STRINGS, f"no {language} copy"
        assert len(module.STRINGS["overlay.refused"]) < 60, f"the {language} note is not short"


# ── Belarusian is not a foreign language to a Russian speaker ────────────────


@pytest.mark.parametrize("text", ["сука", "прывітанне", "дзякуй", "як справы"])
def test_short_cyrillic_read_as_belarusian_is_left_alone(text):
    """Bulgarian and Ukrainian were already excused; Belarusian was not, and
    that is how a one-word message ended up at a translation service."""
    from lingua import Language

    from app.detector import ChatLanguageDetector

    assert ChatLanguageDetector(Language.RUSSIAN).detect(text) is None


def test_an_actually_foreign_language_is_still_translated():
    """The excuse must not become a blanket "never translate anything"."""
    from lingua import Language

    from app.detector import ChatLanguageDetector

    assert ChatLanguageDetector(Language.RUSSIAN).detect("hello everyone lfm dps") == Language.ENGLISH


# ── the cached address has to expire ─────────────────────────────────────────

SCANNER = pathlib.Path(__file__).resolve().parent.parent / "babelchat_scanner_win" / "src" / "lib.rs"


def scanner_source() -> str:
    return SCANNER.read_text(encoding="utf-8")


def test_the_scanner_source_is_where_it_is_expected():
    """Every check below reads this file; if the path were wrong they would all
    pass on an empty string."""
    source = scanner_source()

    assert "find_and_read_buffer" in source
    assert len(source.splitlines()) > 200


def test_a_quiet_cached_address_is_not_trusted_for_ever():
    """`FastRead::NoNew` returned 0 unconditionally, and a ghost buffer reads
    exactly like an idle one. That is the stall."""
    source = scanner_source()

    assert "CACHE_MAX_QUIET_MS" in source, "nothing bounds how long a silent cache is believed"
    assert "last_fresh_ms" in source, "the cache does not record when it last produced anything"

    quiet = re.search(r"const CACHE_MAX_QUIET_MS: u64 = ([0-9_]+);", source)
    assert quiet, "the limit is not a named constant"
    milliseconds = int(quiet.group(1).replace("_", ""))
    assert 3_000 <= milliseconds <= 30_000, f"{milliseconds}ms is not a sane limit"


def test_the_idle_fast_path_still_exists():
    """Rescanning on every idle poll is what the cache was introduced to stop,
    and undoing that would trade a stall for a pegged CPU."""
    source = scanner_source()

    assert "SCAN_MIN_GAP_MS" in source
    assert "if now_ms().saturating_sub(c.last_fresh_ms) < CACHE_MAX_QUIET_MS {" in source, (
        "the quiet path no longer returns early, so every idle poll scans"
    )


def test_a_fresh_read_resets_the_clock():
    """Without this the cache expires on a fixed schedule however busy the chat
    is, and a full scan lands in the middle of a raid."""
    source = scanner_source()

    assert "c.last_fresh_ms = now_ms();" in source


# ── and the Python side can climb back down ──────────────────────────────────


def test_the_reader_checks_whether_it_has_run_ahead_of_the_buffer():
    """The min_seq filter lives inside the scanner, so a buffer whose highest
    sequence is below ours is invisible — and the recovery for that case sits
    behind a scanner result that can never arrive."""
    import sys

    if sys.platform != "win32":
        pytest.skip("the Windows memory reader")
    pytest.importorskip("pymem")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    reader._pid = 4321
    reader._last_seq = 500

    asked = []

    def scanner(pid, min_seq):
        asked.append(min_seq)
        return "10|RAW|SAY|Player|hello" if min_seq == 0 else None

    module_scanner = "_pymem_find_buffer"
    original = getattr(module, module_scanner)
    module._rust_lib = None
    setattr(module, module_scanner, scanner)
    try:
        for _ in range(module._PROBE_EVERY_N_MISSES):
            reader._poll()
    finally:
        setattr(module, module_scanner, original)

    assert 0 in asked, "the reader never asks without its own sequence filter"
    assert reader._last_seq < 500, "it noticed the buffer was behind it and did not rewind"


# ── the colour escape The War Within added ───────────────────────────────────


def test_the_new_colour_escape_is_stripped():
    """`|cnIQ4:` — colour by NAME rather than by hex — is what a keystone link
    carries now. Only the eight-hex-digit form was stripped, so the overlay
    showed `|cnIQ4:[Ключ: Арена Шрама Бездны (2)] есть кто?` verbatim."""
    from app.parser import _RE_COLOR_CODES

    raw = "|cnIQ4:|Hkeystone:180653:585:2:165:0:0:0:0|h[Ключ: Арена Шрама Бездны (2)]|h|r есть кто?"

    cleaned = _RE_COLOR_CODES.sub("", raw)

    assert "|cn" not in cleaned
    assert "IQ4" not in cleaned
    assert "|Hkeystone:" in cleaned, "the hyperlink itself must survive — it is what the link is"
    assert cleaned.endswith("есть кто?")


def test_the_old_colour_escape_still_goes():
    from app.parser import _RE_COLOR_CODES

    assert _RE_COLOR_CODES.sub("", "|cff3fc7ebИмя-Сервер|r: привет") == "Имя-Сервер: привет"


def test_a_bare_pipe_in_ordinary_text_is_left_alone():
    """Players type pipes. Widening the pattern until it eats them would be a
    different bug with the same symptom."""
    from app.parser import _RE_COLOR_CODES

    assert _RE_COLOR_CODES.sub("", "a | b |cn| c") == "a | b |cn| c"
