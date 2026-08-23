"""A channel you joined, and a lull that is not a fault.

Both of these come from one test session, and both were mine.

The first: joining a player-made channel and typing in it produced nothing at
all. The message was captured in milliseconds and then dropped, because the
Custom toggle inherited General's setting and General is off. Inheriting was
defensible — an unrecognised public channel used to be reported as General, so
General's answer had been governing these messages — but the result was silence
with the reason in a debug log.

The second: the reader accused the addon of not writing its buffer while the
addon was working. `_rust_find_buffer` returns None both for "no buffer in this
process" and for "nothing in it newer than what you already have", so every
quiet minute looked like a fault. It had already read the player's name out of
that same buffer.
"""

from __future__ import annotations

import sys

import pytest

from app.config import AppConfig, _migrate_split_channels, enabled_channels

# ── a channel you joined on purpose ──────────────────────────────────────────


def test_a_player_made_channel_is_translated_by_default():
    """You are in Trade and General whether you like it or not, and they are
    firehoses. A channel you typed /join for is the opposite."""
    assert AppConfig(wow_path="").channels_custom is True


def test_the_firehoses_stay_off():
    """The fix must not sweep the spam channels along with it — those are off
    for a reason that still holds."""
    config = AppConfig(wow_path="")

    assert config.channels_trade is False
    assert config.channels_general is False


def test_an_upgraded_config_gets_custom_channels_even_with_general_off():
    """The upgrade path is the one that produced the silence: an existing
    config has no channels_custom key at all, and inheriting General's off gave
    exactly the failure this file is named after."""
    data = {"channels_general": False, "channels_say": True}

    _migrate_split_channels(data)

    assert data["channels_custom"] is True


def test_a_config_that_already_answers_is_left_alone():
    """Someone who turned it off did so on purpose, and an upgrade must not
    turn it back on."""
    data = {"channels_custom": False, "channels_general": True}

    _migrate_split_channels(data)

    assert data["channels_custom"] is False


def test_the_enabled_set_actually_contains_it():
    """The flag is one thing; the set the pipeline filters against is what
    decides, and it is built separately. `Channel.CUSTOM not enabled` in a
    debug log is what the whole session came down to."""
    from app.parser import Channel

    assert Channel.CUSTOM in enabled_channels(AppConfig(wow_path=""))
    assert Channel.TRADE not in enabled_channels(AppConfig(wow_path=""))


# ── and a quiet chat is not a broken addon ───────────────────────────────────

pytestmark_windows = pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_a_lull_after_a_message_is_not_reported_as_a_missing_addon(monkeypatch):
    """This fired on the very first real run, against a working addon that had
    already handed over the player's name."""
    pytest.importorskip("pymem", reason="the Windows memory reader")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(module, "_rust_lib", None)
    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: "1|RAW|SAY|Player|hello")

    reader._pid = 4321
    reader._poll()  # the buffer is there and delivers

    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: None)
    for _ in range(10):
        clock["now"] += module._SILENCE_BEFORE_COMPLAINT
        reader._poll()

    assert reader.problem == "", "ten minutes of quiet chat were called a broken addon"


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_a_buffer_that_was_never_there_is_still_reported(monkeypatch):
    """The complaint has to survive the fix — silencing it altogether would be
    the easy way to make the false alarm go away, and would take the true one
    with it."""
    pytest.importorskip("pymem", reason="the Windows memory reader")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(module, "_rust_lib", None)
    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: None)

    reader._pid = 4321
    reader._poll()
    clock["now"] += module._SILENCE_BEFORE_COMPLAINT + 1
    reader._poll()

    assert reader.problem == module.NO_BUFFER


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_reattaching_forgets_that_the_buffer_was_ever_seen(monkeypatch):
    """WoW restarted without the addon is a new process and a new question. A
    reader that remembered the old answer would never complain again."""
    pytest.importorskip("pymem", reason="the Windows memory reader")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    monkeypatch.setattr(module, "_rust_lib", None)
    monkeypatch.setattr(module, "_pymem_find_buffer", lambda *_a: "1|RAW|SAY|Player|hello")
    reader._pid = 4321
    reader._poll()
    assert reader._buffer_ever_seen is True

    monkeypatch.setattr(module, "_find_wow_pid", lambda: 9999)
    monkeypatch.setattr(module, "describe_access", lambda _pid: "")
    reader._attach()

    assert reader._buffer_ever_seen is False


# ── a channel that is off says so where it can be seen ───────────────────────


def test_a_disabled_channel_is_named_once_at_a_level_the_user_sees(caplog, monkeypatch, tmp_path):
    """The reason a message vanished was logged at DEBUG, behind a console that
    is off by default. That is where a tester's evening went.

    Driven through the real `_on_new_line`, not by repeating its `if` here — a
    test that re-implements the branch it is checking passes whatever the
    production code goes on to do."""
    import logging

    import app.pipeline as pipeline_module
    from app.parser import Channel
    from app.pipeline import PipelineConfig, TranslationPipeline

    parsed = type("Parsed", (), {"channel": Channel.CUSTOM, "author": "Player", "text": "secret words"})()
    monkeypatch.setattr(pipeline_module, "parse_line", lambda _line: parsed)

    config = PipelineConfig(
        chatlog_path=tmp_path / "WoWChatLog.txt",
        db_path=str(tmp_path / "cache.db"),
        enabled_channels={Channel.SAY},
        use_memory_reader=False,
    )
    pipeline = TranslationPipeline(config, lambda _m: None)

    with caplog.at_level(logging.WARNING):
        for index in range(5):
            pipeline._dedup.is_duplicate = lambda _key, _i=index: False
            pipeline._on_new_line(f"line {index}")

    said = [record for record in caplog.records if "switched off" in record.message]
    assert len(said) == 1, f"the notice appeared {len(said)} times, not once"
    assert "Custom" in said[0].getMessage(), "the notice does not say which channel"
    assert "secret words" not in caplog.text, "the notice carried the message text"


def test_an_enabled_channel_produces_no_such_notice(caplog, monkeypatch, tmp_path):
    """Otherwise the warning is just noise on every working setup."""
    import logging

    import app.pipeline as pipeline_module
    from app.parser import Channel
    from app.pipeline import PipelineConfig, TranslationPipeline

    parsed = type("Parsed", (), {"channel": Channel.SAY, "author": "Player", "text": "hello"})()
    monkeypatch.setattr(pipeline_module, "parse_line", lambda _line: parsed)

    pipeline = TranslationPipeline(
        PipelineConfig(
            chatlog_path=tmp_path / "WoWChatLog.txt",
            db_path=str(tmp_path / "cache.db"),
            enabled_channels={Channel.SAY},
            use_memory_reader=False,
        ),
        lambda _m: None,
    )

    with caplog.at_level(logging.WARNING):
        pipeline._on_new_line("a line")

    assert [r for r in caplog.records if "switched off" in r.message] == []
