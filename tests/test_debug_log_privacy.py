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


def test_no_pipeline_log_quotes_chat_above_debug():
    """The `Parsed:` line ran at INFO — the default level — and ran BEFORE the
    channel filter, so unticking Whispers did not stop whispers reaching the
    log file."""
    import pathlib

    source = pathlib.Path("app/pipeline.py").read_text(encoding="utf-8")
    quoting = [
        line.strip()
        for line in source.splitlines()
        if "logger.info(" in line and ("msg.text" in line or "cleaned_text" in line or "line[" in line)
    ]
    assert quoting == [], f"these INFO lines quote chat: {quoting}"


def test_the_parsed_line_sits_below_the_channel_filter():
    import pathlib

    source = pathlib.Path("app/pipeline.py").read_text(encoding="utf-8")
    filter_at = source.index("if msg.channel not in cfg.enabled_channels:")
    parsed_at = source.index('"Parsed: [%s] %s: %s (dict=%s)"')
    assert parsed_at > filter_at, "a filtered-out channel must not be logged"


def test_a_whisper_from_a_disabled_channel_never_reaches_the_log(tmp_path, caplog):
    """End to end, at the loudest level a user can turn on: a message from a
    channel the user unticked leaves no trace of its text anywhere."""
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
