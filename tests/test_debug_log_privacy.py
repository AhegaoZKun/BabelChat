"""What the app is allowed to write to disk about other people's chat.

The capture trace holds the full text of every message the addon sends —
whispers and guild chat included. Those are conversations between other players,
who never agreed to have them recorded on a stranger's machine. It used to be
written unconditionally.
"""

from __future__ import annotations

import logging

import pytest

from app import debug_log


@pytest.fixture(autouse=True)
def isolated_trace(tmp_path):
    """Keep every test's trace inside tmp_path, and always leave it off."""
    debug_log.configure(False, tmp_path / "trace.log")
    yield tmp_path / "trace.log"
    debug_log.configure(False, tmp_path / "trace.log")


def test_the_trace_is_off_unless_it_is_switched_on():
    assert debug_log.is_enabled() is False


def test_nothing_is_written_while_the_trace_is_off(isolated_trace):
    debug_log.record(1, "RAW", "WHISPER", "Stranger", "something private")

    assert not isolated_trace.exists(), "a whisper must not reach the disk unasked"


def test_switching_it_on_records_the_line(isolated_trace):
    debug_log.configure(True, isolated_trace)

    debug_log.record(7, "RAW", "SAY", "Bob", "hello there")

    written = isolated_trace.read_text(encoding="utf-8")
    assert "#7 [RAW] SAY|Bob|hello there" in written


def test_switching_it_on_says_what_the_file_contains(isolated_trace):
    """Someone who opens this file, or is asked to send it, should be able to
    see that it holds other players' private messages."""
    debug_log.configure(True, isolated_trace)

    header = isolated_trace.read_text(encoding="utf-8")
    assert "whispers" in header.lower()


def test_switching_it_on_starts_a_fresh_file(isolated_trace):
    debug_log.configure(True, isolated_trace)
    debug_log.record(1, "RAW", "SAY", "Bob", "first session")
    debug_log.configure(True, isolated_trace)

    assert "first session" not in isolated_trace.read_text(encoding="utf-8")


def test_switching_it_off_stops_new_lines(isolated_trace):
    debug_log.configure(True, isolated_trace)
    debug_log.record(1, "RAW", "SAY", "Bob", "recorded")
    debug_log.configure(False, isolated_trace)
    debug_log.record(2, "RAW", "WHISPER", "Stranger", "not recorded")

    written = isolated_trace.read_text(encoding="utf-8")
    assert "recorded" in written
    assert "not recorded" not in written


def test_an_unwritable_path_disables_the_trace_instead_of_crashing(tmp_path):
    unwritable = tmp_path / "no-such-directory" / "trace.log"

    debug_log.configure(True, unwritable)

    assert debug_log.is_enabled() is False
    debug_log.record(1, "RAW", "SAY", "Bob", "hi")  # must not raise


def test_a_config_default_never_turns_it_on():
    from app.config import AppConfig

    assert AppConfig().debug_capture_trace is False


# ── the pipeline's own logging ───────────────────────────────────────────────


def run_pipeline_on(line: str, tmp_path, *, channels=None, translator=None):
    """Push one raw chat line through a real pipeline and return it."""
    from app.parser import Channel
    from app.pipeline import PipelineConfig, TranslationPipeline

    config = PipelineConfig(
        chatlog_path=tmp_path / "WoWChatLog.txt",
        db_path=str(tmp_path / "cache.db"),
        enabled_channels=channels if channels is not None else {Channel.SAY, Channel.GUILD},
        use_memory_reader=False,
    )
    pipeline = TranslationPipeline(config, lambda _m: None)
    if translator is not None:
        pipeline._translator = translator
    pipeline._on_new_line(line)
    return pipeline


SECRET = "meet me behind the bank at nine"


def test_an_enabled_channel_message_is_not_quoted_at_info(tmp_path, caplog):
    """The previous attempt at this was a grep over pipeline.py for `logger.info`
    on the same physical line as a known variable name. It passed while four
    multi-line calls and two differently-named variables still quoted the text.
    This drives the real pipeline and reads the real log records instead."""
    with caplog.at_level(logging.INFO):
        run_pipeline_on(f"1/1 00:00:00.000  [Guild] Friend-Realm: {SECRET}", tmp_path)

    assert SECRET not in caplog.text


def test_an_enabled_channel_message_is_not_quoted_at_info_even_when_translated(tmp_path, caplog):
    """The translation path logs more than the intake path does."""

    class Translator:
        def translate(self, text, target_lang, source_lang=None, context=None):
            from app.translators.base import TranslationResult

            return TranslationResult(
                original=text,
                translated="встретимся у банка в девять",
                source_lang="EN",
                target_lang=target_lang,
                success=True,
                backend="fake",
            )

        @property
        def has_backend(self):
            return True

    with caplog.at_level(logging.INFO):
        run_pipeline_on(
            f"1/1 00:00:00.000  [Guild] Friend-Realm: {SECRET}", tmp_path, translator=Translator()
        )

    assert SECRET not in caplog.text
    assert "встретимся" not in caplog.text, "the translation is the message too"


def test_the_message_is_still_available_at_debug(tmp_path, caplog):
    """Silence at INFO must not mean the diagnostic is gone — otherwise the
    next person to debug capture turns something worse back on."""
    with caplog.at_level(logging.DEBUG):
        run_pipeline_on(f"1/1 00:00:00.000  [Guild] Friend-Realm: {SECRET}", tmp_path)

    assert SECRET in caplog.text


def test_a_whisper_from_a_disabled_channel_is_not_quoted_in_the_application_log(tmp_path, caplog):
    """End to end, at the loudest logging level a user can turn on: a message
    from a channel the user unticked is not quoted in the application log.

    Deliberately narrower than it once claimed. The opt-in capture trace is a
    separate file with a separate contract — see the tests below — and it does
    record everything the addon delivered, by design and by its own on-screen
    warning. Saying "anywhere" here was untrue of that file."""
    from app.parser import Channel
    from app.pipeline import PipelineConfig, TranslationPipeline

    config = PipelineConfig(
        chatlog_path=tmp_path / "WoWChatLog.txt",
        db_path=str(tmp_path / "cache.db"),
        enabled_channels={Channel.SAY},
        use_memory_reader=False,
    )
    pipeline = TranslationPipeline(config, lambda _m: None)

    line = "1/1 00:00:00.000  [Stranger-Realm] whispers: something private"
    with caplog.at_level(logging.DEBUG):
        pipeline._on_new_line(line)

    assert "something private" not in caplog.text


# ── the trace file, whose contract is different on purpose ───────────────────


def trace_path(tmp_path):
    return tmp_path / "babelchat_raw.log"


def test_the_trace_file_is_not_created_while_the_trace_is_off(tmp_path):
    """The default. Nothing about capture reaches disk unless asked."""
    path = trace_path(tmp_path)
    debug_log.configure(False, str(path))

    debug_log.record(1, "RAW", "WHISPER", "Stranger-Realm", SECRET)

    assert not path.exists()


def test_the_trace_records_a_disabled_channel_too_and_says_so(tmp_path):
    """Pinning the contract rather than pretending otherwise.

    The trace exists to diagnose capture — what the addon actually delivered —
    so filtering it by the user's channel toggles would remove the evidence
    someone turned it on to find. It therefore holds whispers from channels the
    user has unticked, which is exactly what the checkbox's hint warns about.
    That warning is part of the contract, so it is asserted here: if the copy
    ever softens, this test is where it gets caught."""
    from app.i18n import _STRINGS

    path = trace_path(tmp_path)
    debug_log.configure(True, str(path))
    debug_log.record(1, "RAW", "WHISPER", "Stranger-Realm", SECRET)
    debug_log.configure(False, str(path))

    assert SECRET in path.read_text(encoding="utf-8")

    hint = _STRINGS["settings.privacy.trace_hint"]
    assert "whisper" in hint["EN"].lower(), "the warning must still name whispers"
    assert "шёпот" in hint["RU"].lower()


def test_turning_the_trace_off_stops_it_writing(tmp_path):
    """The switch has to work in both directions, or "turn it on only while
    investigating" is advice the app does not honour."""
    path = trace_path(tmp_path)
    debug_log.configure(True, str(path))
    debug_log.record(1, "RAW", "SAY", "Friend", "first")
    debug_log.configure(False, str(path))
    debug_log.record(2, "RAW", "SAY", "Friend", "second")

    written = path.read_text(encoding="utf-8")
    assert "first" in written
    assert "second" not in written
