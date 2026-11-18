"""When the screen source reads text but nothing reaches the overlay, say why.

The reader can be working perfectly and still produce nothing: the chat is in a
channel that is switched off, or the text is not in a shape that reads as chat, or
the box is not on the chat at all. Each of those looks different in the text that
came back, and the user should not have to guess which one it is.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import numpy as np
import pytest
from lingua import Language

import app.ocr_reader as ocr_reader
from app.config import AppConfig, enabled_channels, enabled_filter_tabs
from app.i18n import tr
from app.ocr_capture import OcrRegion
from app.ocr_parse import frame_from_lines
from app.ocr_reader import OcrChatWatcher, OcrSettings, ReadReport
from app.ocr_report import format_report
from app.pipeline import PipelineConfig, TranslationPipeline


@pytest.fixture(autouse=True)
def english():
    previous = tr.get_language()
    tr.set_language("EN")
    yield
    tr.set_language(previous)


def _report(lines: list[str]) -> ReadReport:
    return ReadReport(lines=lines, entries=frame_from_lines(lines))


# ── the report ───────────────────────────────────────────────────────────────


def test_an_empty_read_says_there_was_nothing_to_read() -> None:
    assert format_report(_report([])) == tr("wizard.source.test_empty")


def test_a_chat_message_is_marked_as_arriving() -> None:
    text = format_report(_report(["[Guild] Thrall: anyone up for keys?"]))
    assert "✓" in text and "Guild" in text and "Thrall" in text


def test_every_channel_is_marked_as_arriving() -> None:
    """The screen source is not filtered by channel, so nothing is marked as ignored."""
    lines = ["[Guild] Thrall: gg", "[2. Trade - Orgrimmar] Vasya: WTS crest of heroism", "[Party] A: x"]
    text = format_report(_report(lines))
    assert text.count("✓") == 3
    assert "✗" not in text


def test_text_that_is_not_chat_is_marked_and_explained() -> None:
    text = format_report(_report(["You receive loot: [Thing]", "Mount up!", "12 gold"]))
    assert tr("wizard.source.test_none_chat") in text
    assert text.count("?  ") == 3


def test_the_header_counts_lines_and_chat_messages() -> None:
    text = format_report(_report(["[Guild] A: one", "Thrall has come online.", "[Party] B: two"]))
    assert text.splitlines()[0] == tr("wizard.source.test_header", lines=3, chat=2)


def test_long_text_is_clipped_to_a_readable_row() -> None:
    text = format_report(_report(["[Guild] A: " + "word " * 80]))
    assert max(len(row) for row in text.splitlines()) < 140


# ── the watcher ──────────────────────────────────────────────────────────────


class ScriptedCapture:
    name = "scripted"

    def __init__(self) -> None:
        self.count = 0

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def grab(self, region):
        self.count += 1
        frame = np.full((40, 200, 3), 30, dtype=np.uint8)
        frame[:, : (self.count * 7) % 190 + 5] = 200  # a different picture every grab
        return frame


class ScriptedEngine:
    name = "scripted"
    script = "default"

    def __init__(self, frames: list[list[str]]) -> None:
        self.frames, self.calls = frames, 0

    def recognise(self, image):
        lines = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return [(0.0, 20.0 * i, 400.0, 20.0 * i + 14.0, text, 0.95) for i, text in enumerate(lines)]


def _wait(condition, timeout: float = 6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def run_watcher():
    started: list[OcrChatWatcher] = []

    def go(frames, *, history=True):
        delivered, shown = [], []
        engine = ScriptedEngine(frames)
        watcher = OcrChatWatcher(
            lambda line, **_k: delivered.append(line),
            OcrSettings(region=OcrRegion(0, 0, 1, 1), interval=0.01),
            on_history=(shown.append if history else None),
        )
        patches = [
            patch.object(ocr_reader, "create_capture", lambda *_a, **_k: ScriptedCapture()),
            patch.object(ocr_reader, "create_engine", lambda *_a, **_k: engine),
        ]
        for p in patches:
            p.start()
        watcher._patches = patches  # type: ignore[attr-defined]
        started.append(watcher)
        watcher.start()
        return watcher, delivered, shown, engine

    yield go
    for watcher in started:
        watcher.stop()
        for p in getattr(watcher, "_patches", []):
            p.stop()


_BASE = ["[Guild] A: one", "[Guild] B: two"]


def test_the_chat_already_on_screen_is_handed_over_once_and_not_as_new(run_watcher) -> None:
    watcher, delivered, shown, engine = run_watcher([_BASE, _BASE, _BASE, _BASE])
    assert _wait(lambda: engine.calls >= 4)
    assert len(shown) == 1
    assert [line.split("  ", 1)[1] for line in shown[0]] == ["[Guild] A: one", "[Guild] B: two"]
    assert delivered == []


def test_history_is_not_sent_again_when_the_area_is_changed(run_watcher) -> None:
    watcher, _delivered, shown, engine = run_watcher([_BASE])
    assert _wait(lambda: len(shown) == 1)
    watcher.configure(OcrSettings(region=OcrRegion(0, 0.5, 1, 0.5), interval=0.01))
    assert _wait(lambda: engine.calls >= 6)
    assert len(shown) == 1


def test_a_watcher_with_no_history_callback_still_works(run_watcher) -> None:
    _watcher, delivered, shown, _engine = run_watcher(
        [_BASE, _BASE, [*_BASE[1:], "[Guild] C: three"], [*_BASE[1:], "[Guild] C: three"]], history=False,
    )
    assert _wait(lambda: len(delivered) == 1)
    assert shown == []


_NOT_CHAT = ["You receive loot: [Thing]", "Mount up!", "12 gold", "Quest accepted"]


def test_text_that_never_looks_like_chat_raises_a_named_problem(run_watcher) -> None:
    watcher, _delivered, _shown, _engine = run_watcher([_NOT_CHAT])
    assert _wait(lambda: watcher.problem == "ocr_unrecognised")


def test_the_problem_clears_the_moment_chat_is_recognised(run_watcher) -> None:
    watcher, _delivered, _shown, engine = run_watcher([_NOT_CHAT] * 6 + [_BASE])
    assert _wait(lambda: watcher.problem == "ocr_unrecognised")
    assert _wait(lambda: engine.calls >= 8 and watcher.problem == "")


def test_a_read_with_only_a_line_or_two_is_not_called_a_problem(run_watcher) -> None:
    watcher, _delivered, _shown, engine = run_watcher([["Mount up!", "12 gold"]])
    assert _wait(lambda: engine.calls >= 8)
    assert watcher.problem == ""


# ── the pipeline shows the history, untranslated ─────────────────────────────


@pytest.fixture
def pipeline_with_screen_source(tmp_path):
    shown: list = []
    config = PipelineConfig(
        chatlog_path=tmp_path / "WoWChatLog.txt",
        providers={"deepl": {"api_key": "fake"}},
        target_lang="RU",
        own_language=Language.ENGLISH,
        db_path=str(tmp_path / "cache.db"),
        use_memory_reader=False,
        capture_source="ocr",
        enabled_channels=enabled_channels(AppConfig()),  # Trade is off by default
    )
    return TranslationPipeline(config, shown.append), shown


def _line(channel: str, author: str, text: str) -> str:
    return f"10/12 12:00:00.000  [{channel}] {author}: {text}"


def test_history_from_the_screen_is_shown_untranslated(pipeline_with_screen_source) -> None:
    pipeline, shown = pipeline_with_screen_source
    pipeline._on_ocr_history([_line("Guild", "Thrall", "anyone up?")])
    assert len(shown) == 1
    assert shown[0].original.author == "Thrall" and shown[0].translation is None


def test_history_shows_every_channel_the_chat_shows(pipeline_with_screen_source) -> None:
    """Trade is off in the toggles, and the player can see it on screen."""
    pipeline, shown = pipeline_with_screen_source
    pipeline._on_ocr_history([_line("Trade", "Vasya", "WTS crest"), _line("Guild", "Thrall", "gg")])
    assert [m.original.author for m in shown] == ["Vasya", "Thrall"]


def test_history_rows_each_get_their_own_id(pipeline_with_screen_source) -> None:
    """The overlay updates a row in place by id; shared ids would overwrite each other."""
    pipeline, shown = pipeline_with_screen_source
    pipeline._on_ocr_history([_line("Guild", "A", "one"), _line("Guild", "B", "two"), _line("Guild", "C", "three")])
    assert len({m.msg_id for m in shown}) == 3


def test_a_history_line_that_scrolls_past_again_is_not_treated_as_new(pipeline_with_screen_source) -> None:
    pipeline, shown = pipeline_with_screen_source
    line = _line("Guild", "Thrall", "anyone up?")
    pipeline._on_ocr_history([line])
    shown.clear()
    pipeline._on_new_line(line)
    assert shown == []


def test_unparseable_history_lines_are_skipped(pipeline_with_screen_source) -> None:
    pipeline, shown = pipeline_with_screen_source
    pipeline._on_ocr_history(["not a chat line at all", ""])
    assert shown == []


# ── noticing that the chat changed ───────────────────────────────────────────
#
# Recognition is the expensive step, so the reader only runs it when the picture
# changed. That comparison once averaged the whole region, which cannot see one
# short line appearing in a large, quiet chat: the average moved by a third of a
# level against a threshold of six tenths, recognition never ran, and the message
# was never delivered. The Test button has no such comparison, so it read fine.


def _picture(lines: list[str], width: int = 760, height: int = 420) -> np.ndarray:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), (14, 14, 18))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)
    for i, text in enumerate(lines):
        y = height - 8 - 20 * (len(lines) - i)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            draw.text((6 + dx, y + dy), text, font=font, fill=(0, 0, 0))
        draw.text((6, y), text, font=font, fill=(235, 235, 235))
    return np.asarray(image)


_FULL_CHAT = [f"[Guild] Player{i}: an ordinary chat message number {i} about nothing" for i in range(18)]


def test_one_short_line_in_an_otherwise_quiet_chat_counts_as_a_change() -> None:
    from app.ocr_reader import _changed, _signature

    assert _changed(_signature(_picture([])), _signature(_picture(["[Guild] Bob: gg"])))


def test_a_line_scrolling_into_a_full_chat_counts_as_a_change() -> None:
    from app.ocr_reader import _changed, _signature

    before, after = _picture(_FULL_CHAT), _picture([*_FULL_CHAT[1:], "[Guild] Bob: gg"])
    assert _changed(_signature(before), _signature(after))


def test_the_same_picture_is_not_a_change() -> None:
    from app.ocr_reader import _changed, _signature

    assert not _changed(_signature(_picture(_FULL_CHAT)), _signature(_picture(_FULL_CHAT)))


def test_video_noise_is_not_a_change() -> None:
    from app.ocr_reader import _changed, _signature

    rng = np.random.default_rng(1)
    base = _picture(_FULL_CHAT).astype(np.int16)
    noisy = np.clip(base + rng.integers(-3, 4, base.shape), 0, 255).astype(np.uint8)
    assert not _changed(_signature(base.astype(np.uint8)), _signature(noisy))


def test_a_game_world_drifting_behind_the_chat_is_a_change() -> None:
    from app.ocr_reader import _changed, _signature

    frame = _picture(_FULL_CHAT)
    brighter = np.clip(frame.astype(np.int16) + 3, 0, 255).astype(np.uint8)
    assert _changed(_signature(frame), _signature(brighter))


@pytest.mark.parametrize("shape", [(1, 1, 3), (5, 10, 3), (3, 200, 3), (200, 3, 3)])
def test_a_tiny_region_does_not_break_the_comparison(shape) -> None:
    from app.ocr_reader import _changed, _signature

    frame = np.full(shape, 90, dtype=np.uint8)
    assert not _changed(_signature(frame), _signature(frame.copy()))


class PictureCapture:
    """Hands out a scripted sequence of pictures, repeating the last one."""

    name = "pictures"

    def __init__(self, pictures: list[np.ndarray]) -> None:
        self.pictures, self.count = pictures, 0

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def grab(self, region):
        picture = self.pictures[min(self.count, len(self.pictures) - 1)]
        self.count += 1
        return picture


def _run_on_pictures(pictures, engine_frames, monkeypatch, *, seconds_before_forced_read: float):
    monkeypatch.setattr(ocr_reader, "_MAX_SECONDS_WITHOUT_READING", seconds_before_forced_read)
    engine = ScriptedEngine(engine_frames)
    capture = PictureCapture(pictures)
    delivered: list[str] = []
    watcher = OcrChatWatcher(
        lambda line, **_k: delivered.append(line),
        OcrSettings(region=OcrRegion(0, 0, 1, 1), interval=0.02),
    )
    monkeypatch.setattr(ocr_reader, "create_capture", lambda *_a, **_k: capture)
    monkeypatch.setattr(ocr_reader, "create_engine", lambda *_a, **_k: engine)
    watcher.start()
    return watcher, delivered, engine


def test_a_first_short_message_in_a_quiet_chat_is_delivered(monkeypatch) -> None:
    """The case that never reached the overlay. The forced re-read is switched off
    (a very long interval), so only noticing the change can deliver it."""
    quiet, spoken = _picture([]), _picture(["[Guild] Bob: gg"])
    # The engine is only asked when the picture changed, so its answers are in
    # order of READS, not of captures: first the empty chat, then the message.
    watcher, delivered, engine = _run_on_pictures(
        [quiet] * 8 + [spoken], [[], ["[Guild] Bob: gg"]], monkeypatch, seconds_before_forced_read=999,
    )
    try:
        assert _wait(lambda: len(delivered) == 1, timeout=5)
    finally:
        watcher.stop()
    assert delivered[0].endswith("[Guild] Bob: gg")


def test_a_still_picture_is_not_read_over_and_over(monkeypatch) -> None:
    watcher, _delivered, engine = _run_on_pictures(
        [_picture(_FULL_CHAT)], [_FULL_CHAT], monkeypatch, seconds_before_forced_read=999,
    )
    try:
        time.sleep(0.6)
    finally:
        watcher.stop()
    assert engine.calls == 1


def test_whatever_the_comparison_misses_is_caught_by_the_periodic_read(monkeypatch) -> None:
    """A picture that never changes at all, with text that does: the safety net."""
    still = _picture(_FULL_CHAT)
    watcher, delivered, _engine = _run_on_pictures(
        [still], [[], [], [], ["[Guild] Bob: gg"]], monkeypatch, seconds_before_forced_read=0.1,
    )
    try:
        assert _wait(lambda: len(delivered) == 1, timeout=5)
    finally:
        watcher.stop()


# ── the screen source is not filtered by channel ─────────────────────────────


def _pipeline(tmp_path, source: str):
    shown: list = []
    config = PipelineConfig(
        chatlog_path=tmp_path / "WoWChatLog.txt",
        providers={"deepl": {"api_key": "fake"}},
        target_lang="RU",
        own_language=Language.ENGLISH,
        db_path=str(tmp_path / "cache.db"),
        use_memory_reader=False,
        capture_source=source,
        translation_enabled=False,  # deliver as read, with no network involved
        enabled_channels=enabled_channels(AppConfig()),  # Trade is off by default
    )
    return TranslationPipeline(config, shown.append), shown


def test_a_live_message_from_a_channel_the_toggles_leave_off_still_arrives_from_the_screen(tmp_path) -> None:
    pipeline, shown = _pipeline(tmp_path, "ocr")
    pipeline._on_new_line(_line("Trade", "Vasya", "WTS crest of heroism"))
    assert [m.original.author for m in shown] == ["Vasya"]


def test_the_toggles_still_filter_the_memory_source(tmp_path) -> None:
    """They exist for sources that see every channel whether it is shown or not."""
    pipeline, shown = _pipeline(tmp_path, "memory")
    pipeline._on_new_line(_line("Trade", "Vasya", "WTS crest of heroism"))
    pipeline._on_new_line(_line("Guild", "Thrall", "anyone up for keys?"))
    assert [m.original.author for m in shown] == ["Thrall"]


def test_the_screen_source_gets_a_filter_tab_for_every_channel() -> None:
    all_tabs = enabled_filter_tabs(AppConfig(capture_source="ocr"))
    assert enabled_filter_tabs(AppConfig(capture_source="memory")) < all_tabs
    assert {"Trade", "General"} <= all_tabs
