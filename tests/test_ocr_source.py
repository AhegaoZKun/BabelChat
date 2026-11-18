"""The screen (OCR) chat source: config defaults, pipeline wiring, watcher loop.

Nothing here needs a display, a GPU, a Wayland session or an OCR model. The
watcher is driven with a scripted capture and a scripted engine, so what is
under test is the loop's own behaviour: what it reports, what it stays quiet
about, and what it says when it cannot work.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from lingua import Language

import app.ocr_reader as ocr_reader
from app.config import AppConfig
from app.ocr_capture import CaptureUnavailable, OcrRegion
from app.ocr_engine import OcrUnavailable, create_engine, script_for_language
from app.ocr_reader import OcrChatWatcher, OcrSettings
from app.parser import Channel, parse_line
from app.pipeline import PipelineConfig, TranslationPipeline

# ── config ───────────────────────────────────────────────────────────────────


def test_a_new_install_reads_the_screen_not_the_game() -> None:
    assert AppConfig().capture_source == "ocr"


def test_an_existing_config_keeps_reading_the_game(tmp_path) -> None:
    """Upgrading must not turn a working setup into one waiting for a chat area."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"own_language": "EN"}), encoding="utf-8")
    assert AppConfig.load(str(path)).capture_source == "memory"


def test_an_explicit_choice_survives_loading(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"capture_source": "ocr"}), encoding="utf-8")
    assert AppConfig.load(str(path)).capture_source == "ocr"


# ── region ───────────────────────────────────────────────────────────────────


def test_a_region_survives_a_round_trip_through_json() -> None:
    region = OcrRegion(0.05, 0.6, 0.3, 0.25, monitor=1, abs=(100, 600, 800, 300))
    assert OcrRegion.from_dict(json.loads(json.dumps(region.to_dict()))) == region


@pytest.mark.parametrize(
    "data",
    [None, {}, {"x": 0, "y": 0}, {"x": 0, "y": 0, "w": 0, "h": 0.5}, {"x": "a", "y": 0, "w": 1, "h": 1}],
)
def test_a_broken_region_is_no_region(data) -> None:
    assert OcrRegion.from_dict(data) is None


def test_a_region_is_clamped_into_the_screen() -> None:
    region = OcrRegion.from_dict({"x": -0.5, "y": 2.0, "w": 3.0, "h": 0.5})
    assert region is not None
    assert (region.x, region.y, region.w) == (0.0, 1.0, 1.0)


def test_crop_takes_the_right_pixels_whatever_the_resolution() -> None:
    region = OcrRegion(0.25, 0.5, 0.5, 0.25)
    for width, height in ((1920, 1080), (3840, 2160)):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        crop = region.crop(frame)
        assert crop.shape[1] == pytest.approx(width * 0.5, abs=2)
        assert crop.shape[0] == pytest.approx(height * 0.25, abs=2)


# ── engine selection ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("language", "script"),
    [
        ("RU", "eslav"), ("ru", "eslav"), ("DE", "latin"), ("KO", "korean"),
        ("JA", "japan"), ("EN", "default"), ("XX", "default"), ("", "default"),
    ],
)
def test_the_script_follows_the_users_language(language: str, script: str) -> None:
    assert script_for_language(language) == script


def _unavailable(reason: str):
    def factory(_script: str):
        raise OcrUnavailable(reason)

    return factory


def test_no_engine_is_reported_with_every_reason() -> None:
    with (
        patch("app.ocr_engine.RapidOcrEngine", _unavailable("no rapidocr")),
        patch("app.ocr_engine.TesseractEngine", _unavailable("no tesseract")),
        pytest.raises(OcrUnavailable) as excinfo,
    ):
        create_engine("default")
    message = str(excinfo.value)
    assert "no rapidocr" in message and "no tesseract" in message


# ── pipeline wiring ──────────────────────────────────────────────────────────


@pytest.fixture
def make_config(tmp_path):
    def build(**overrides) -> PipelineConfig:
        base = {
            "chatlog_path": tmp_path / "WoWChatLog.txt",
            "providers": {"deepl": {"api_key": "fake"}},
            "target_lang": "RU",
            "own_language": Language.RUSSIAN,
            "db_path": str(tmp_path / "cache.db"),
            "use_memory_reader": False,
        }
        base.update(overrides)
        return PipelineConfig(**base)

    return build


def test_ocr_mode_builds_the_screen_source_and_not_the_memory_one(make_config) -> None:
    pipeline = TranslationPipeline(make_config(capture_source="ocr", use_memory_reader=True), lambda _m: None)
    assert pipeline._ocr_watcher is not None
    assert pipeline._memory_watcher is None
    assert pipeline.chat_source is pipeline._ocr_watcher


def test_memory_mode_builds_no_screen_source(make_config) -> None:
    pipeline = TranslationPipeline(make_config(capture_source="memory"), lambda _m: None)
    assert pipeline._ocr_watcher is None


def test_starting_in_ocr_mode_does_not_also_start_the_file_watcher(make_config) -> None:
    pipeline = TranslationPipeline(make_config(capture_source="ocr"), lambda _m: None)
    with patch.object(OcrChatWatcher, "start") as start, patch.object(pipeline._watcher, "start") as file_start:
        pipeline.start()
    start.assert_called_once()
    file_start.assert_not_called()


def test_switching_the_source_live_swaps_the_watcher_and_stops_the_old_one(make_config) -> None:
    fake_memory = MagicMock()
    with (
        patch("app.pipeline.MemoryChatWatcher", return_value=fake_memory),
        patch("app.pipeline.HAS_MEMORY_READER", True),
    ):
        pipeline = TranslationPipeline(make_config(capture_source="memory", use_memory_reader=True), lambda _m: None)
        assert pipeline._memory_watcher is fake_memory
        with patch.object(OcrChatWatcher, "start"):
            pipeline.start()
            pipeline.update_config(make_config(capture_source="ocr", use_memory_reader=True))
    fake_memory.stop.assert_called_once()
    assert pipeline._memory_watcher is None
    assert pipeline._ocr_watcher is not None


def test_changing_the_region_reconfigures_the_running_watcher(make_config) -> None:
    pipeline = TranslationPipeline(make_config(capture_source="ocr"), lambda _m: None)
    watcher = pipeline._ocr_watcher
    region = {"x": 0.1, "y": 0.6, "w": 0.3, "h": 0.3}
    with patch.object(watcher, "configure") as configure:
        pipeline.update_config(make_config(capture_source="ocr", ocr_region=region))
    configure.assert_called_once()
    assert configure.call_args.args[0].region == OcrRegion.from_dict(region)


# ── the watcher loop ─────────────────────────────────────────────────────────


class ScriptedCapture:
    """A screen that shows a different (non-black) picture on every grab."""

    name = "scripted"

    def __init__(self, black: bool = False) -> None:
        self.black = black
        self.count = 0

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def grab(self, region):
        self.count += 1
        if self.black:
            return np.zeros((40, 200, 3), dtype=np.uint8)
        frame = np.full((40, 200, 3), 30, dtype=np.uint8)
        frame[:, : (self.count * 7) % 190 + 5] = 200  # changes every call
        return frame


class ScriptedEngine:
    """Returns the boxes for the Nth frame; the last entry repeats."""

    name = "scripted"
    script = "default"

    def __init__(self, frames: list[list[str]]) -> None:
        self.frames = frames
        self.calls = 0

    def recognise(self, image):
        lines = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return [(0.0, 20.0 * i, 400.0, 20.0 * i + 14.0, text, 0.95) for i, text in enumerate(lines)]


def wait_for(condition, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def watcher_factory():
    created: list[OcrChatWatcher] = []

    def build(settings: OcrSettings, *, capture=None, engine=None, capture_error=None, engine_error=None):
        got: list[str] = []
        watcher = OcrChatWatcher(lambda line, **_k: got.append(line), settings)

        def make_capture(*_a, **_k):
            if capture_error:
                raise capture_error
            return capture

        def make_engine(*_a, **_k):
            if engine_error:
                raise engine_error
            return engine

        patches = [
            patch.object(ocr_reader, "create_capture", make_capture),
            patch.object(ocr_reader, "create_engine", make_engine),
        ]
        for p in patches:
            p.start()
        watcher._test_patches = patches  # type: ignore[attr-defined]
        created.append(watcher)
        return watcher, got

    yield build
    for w in created:
        w.stop()
        for p in getattr(w, "_test_patches", []):
            p.stop()


_REGION = OcrRegion(0, 0, 1, 1)
_FAST = {"interval": 0.01}


def test_without_a_region_it_says_so_and_reads_nothing(watcher_factory) -> None:
    watcher, got = watcher_factory(
        OcrSettings(region=None, **_FAST), capture=ScriptedCapture(), engine=ScriptedEngine([[]]),
    )
    watcher.start()
    assert wait_for(lambda: watcher.problem == "ocr_no_region")
    assert not watcher.is_attached and got == []


def test_the_first_frame_is_a_baseline_and_a_new_line_is_delivered_once(watcher_factory) -> None:
    engine = ScriptedEngine([
        ["[Guild] A: one", "[Guild] B: two"],            # already on screen: not reported
        ["[Guild] A: one", "[Guild] B: two"],
        ["[Guild] B: two", "[Guild] Thrall: hello there"],  # scrolled by one
        ["[Guild] B: two", "[Guild] Thrall: hello there"],
    ])
    watcher, got = watcher_factory(OcrSettings(region=_REGION, **_FAST), capture=ScriptedCapture(), engine=engine)
    watcher.start()
    assert wait_for(lambda: len(got) >= 1)
    assert wait_for(lambda: engine.calls >= 4)
    assert len(got) == 1
    parsed = parse_line(got[0])
    assert parsed is not None
    assert (parsed.channel, parsed.author, parsed.text) == (Channel.GUILD, "Thrall", "hello there")
    assert watcher.is_attached and watcher.problem == ""


def test_an_unchanged_picture_is_not_recognised_again(watcher_factory) -> None:
    class StillCapture(ScriptedCapture):
        def grab(self, region):
            self.count += 1
            return np.full((40, 200, 3), 90, dtype=np.uint8)

    engine = ScriptedEngine([["[Guild] A: one"]])
    watcher, _got = watcher_factory(OcrSettings(region=_REGION, **_FAST), capture=StillCapture(), engine=engine)
    watcher.start()
    assert wait_for(lambda: watcher._attached)
    time.sleep(0.4)
    assert engine.calls == 1


def test_all_black_frames_are_named_rather_than_mistaken_for_quiet_chat(watcher_factory) -> None:
    watcher, _got = watcher_factory(
        OcrSettings(region=_REGION, **_FAST), capture=ScriptedCapture(black=True), engine=ScriptedEngine([[]]),
    )
    watcher.start()
    assert wait_for(lambda: watcher.problem == "ocr_black")


def test_a_capture_that_cannot_start_is_reported(watcher_factory) -> None:
    watcher, _got = watcher_factory(
        OcrSettings(region=_REGION, **_FAST),
        capture_error=CaptureUnavailable("no portal"),
        engine=ScriptedEngine([[]]),
    )
    watcher.start()
    assert wait_for(lambda: watcher.problem == "ocr_capture")
    assert not watcher.is_attached


def test_a_missing_engine_is_reported(watcher_factory) -> None:
    watcher, _got = watcher_factory(
        OcrSettings(region=_REGION, **_FAST), capture=ScriptedCapture(), engine_error=OcrUnavailable("no models"),
    )
    watcher.start()
    assert wait_for(lambda: watcher.problem == "ocr_no_engine")


def test_choosing_a_region_while_running_starts_reading(watcher_factory) -> None:
    engine = ScriptedEngine([["[Guild] A: one"], ["[Guild] A: one"], ["[Guild] A: one", "[Guild] B: two"]])
    watcher, got = watcher_factory(OcrSettings(region=None, **_FAST), capture=ScriptedCapture(), engine=engine)
    watcher.start()
    assert wait_for(lambda: watcher.problem == "ocr_no_region")
    watcher.configure(OcrSettings(region=_REGION, **_FAST))
    assert wait_for(lambda: len(got) == 1)
    assert watcher.problem == ""


def test_stop_ends_the_thread(watcher_factory) -> None:
    watcher, _got = watcher_factory(
        OcrSettings(region=_REGION, **_FAST), capture=ScriptedCapture(), engine=ScriptedEngine([[]]),
    )
    watcher.start()
    assert wait_for(lambda: watcher._thread is not None and watcher._thread.is_alive())
    thread = watcher._thread
    watcher.stop()
    assert thread is not None and not thread.is_alive()
