"""The Windows (PyQt) side of the screen source: wizard, settings, area picker.

Runs on Qt's offscreen platform. What is pinned here is the behaviour a user
meets: the screen source is the default and is labelled as recommended, choosing
it drops the WoW-path step, the choice survives a language switch (which
restarts the wizard from the config), and the settings dialog saves it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.ocr_capture import OcrRegion  # noqa: E402
from app.region_picker_qt import _PickerSurface  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402
from app.setup_wizard import (  # noqa: E402
    PAGE_API_KEY,
    PAGE_LANGUAGE,
    PAGE_READY,
    PAGE_SOURCE,
    PAGE_WELCOME,
    PAGE_WOW_PATH,
    TOTAL_PAGES,
    SetupWizard,
)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def english():
    previous = tr.get_language()
    tr.set_language("EN")
    yield
    tr.set_language(previous)


@pytest.fixture
def config(monkeypatch) -> AppConfig:
    monkeypatch.setattr(AppConfig, "save", lambda self, *a, **k: None)
    # Arriving on the WoW-path page with nothing auto-detected opens a modal folder
    # dialog, which would block a headless run forever.
    monkeypatch.setattr(SetupWizard, "_browse_wow_path", lambda self: None)
    return AppConfig()


_REGION = OcrRegion(0.05, 0.6, 0.3, 0.25, monitor=0, px=(1920, 1080))


def _walk(wizard: SetupWizard) -> list[int]:
    """Click Next from the first page to the last, recording every page seen."""
    seen = [wizard._stack.currentIndex()]
    while wizard._stack.currentIndex() != wizard._active_pages()[-1]:
        wizard._go_next()
        seen.append(wizard._stack.currentIndex())
    return seen


# ── wizard ───────────────────────────────────────────────────────────────────


def test_a_new_install_defaults_to_the_screen_source_and_says_it_is_recommended(qapp, config) -> None:
    wizard = SetupWizard(config)
    assert wizard._capture.source == "ocr"
    assert "recommended" in wizard._capture._ocr_radio.text().lower()
    assert "default" in wizard._capture._ocr_radio.text().lower()


def test_the_wizard_has_a_page_for_the_source_after_the_welcome(qapp, config) -> None:
    wizard = SetupWizard(config)
    assert TOTAL_PAGES == 6
    assert (PAGE_WELCOME, PAGE_SOURCE, PAGE_API_KEY, PAGE_WOW_PATH, PAGE_LANGUAGE, PAGE_READY) == (0, 1, 2, 3, 4, 5)
    assert wizard._stack.count() == TOTAL_PAGES


def test_reading_the_screen_skips_the_wow_path_page(qapp, config) -> None:
    wizard = SetupWizard(config)
    assert _walk(wizard) == [PAGE_WELCOME, PAGE_SOURCE, PAGE_API_KEY, PAGE_LANGUAGE, PAGE_READY]


def test_reading_game_memory_keeps_the_wow_path_page(qapp, config) -> None:
    wizard = SetupWizard(config)
    wizard._capture._memory_radio.setChecked(True)
    assert _walk(wizard) == [PAGE_WELCOME, PAGE_SOURCE, PAGE_API_KEY, PAGE_WOW_PATH, PAGE_LANGUAGE, PAGE_READY]


def test_the_step_counter_follows_the_pages_actually_shown(qapp, config) -> None:
    wizard = SetupWizard(config)
    wizard._go_next()
    assert "2 of 5" in wizard._step_text.text() and "Chat source" in wizard._step_text.text()
    wizard._capture._memory_radio.setChecked(True)
    assert "2 of 6" in wizard._step_text.text()


def test_going_back_from_the_language_page_skips_the_wow_path_page(qapp, config) -> None:
    wizard = SetupWizard(config)
    _walk(wizard)
    wizard._go_back()  # ready -> language
    wizard._go_back()  # language -> api key (WoW path skipped)
    assert wizard._stack.currentIndex() == PAGE_API_KEY


def test_the_addon_box_only_shows_for_the_memory_source(qapp, config) -> None:
    wizard = SetupWizard(config)
    _walk(wizard)
    assert wizard._addon_group.isHidden()
    wizard._capture._memory_radio.setChecked(True)
    wizard._update_summary()
    assert not wizard._addon_group.isHidden()


def test_the_summary_names_the_source_and_the_area(qapp, config) -> None:
    wizard = SetupWizard(config)
    wizard._update_summary()
    text = wizard._summary_label.text()
    assert tr("wizard.summary.source_ocr") in text
    assert tr("wizard.source.area_none") in text
    wizard._capture._picked(_REGION)
    wizard._update_summary()
    assert tr("wizard.source.area_set") in wizard._summary_label.text()


def test_finishing_saves_the_source_and_the_area(qapp, config) -> None:
    wizard = SetupWizard(config)
    wizard._capture._picked(_REGION)
    wizard._finish()
    assert config.capture_source == "ocr"
    assert OcrRegion.from_dict(config.ocr_region) == _REGION


def test_the_choice_survives_a_language_switch_restart(qapp, config) -> None:
    """Changing the interface language restarts the wizard from the config."""
    wizard = SetupWizard(config)
    wizard._capture._memory_radio.setChecked(True)
    wizard._apply_capture_to(config)
    again = SetupWizard(config)
    assert again._capture.source == "memory"

    config.capture_source = "ocr"
    config.ocr_region = _REGION.to_dict()
    assert SetupWizard(config)._capture.region == _REGION


# ── settings dialog ──────────────────────────────────────────────────────────


def test_settings_shows_the_saved_source_region_script_and_interval(qapp, config) -> None:
    config.capture_source = "ocr"
    config.ocr_region = _REGION.to_dict()
    config.ocr_script = "eslav"
    config.ocr_interval = 1.5
    dialog = SettingsDialog(config)
    assert dialog._capture.region == _REGION
    assert dialog._capture.script == "eslav"
    assert dialog._capture.interval == pytest.approx(1.5)


def test_settings_saves_what_the_user_changed(qapp, config) -> None:
    dialog = SettingsDialog(config)
    dialog._capture._picked(_REGION)
    dialog._capture._script_combo.setCurrentIndex(dialog._capture._script_combo.findData("korean"))
    dialog._capture._interval.setValue(2.0)
    dialog._save_and_accept()
    assert config.capture_source == "ocr"
    assert OcrRegion.from_dict(config.ocr_region) == _REGION
    assert config.ocr_script == "korean"
    assert config.ocr_interval == pytest.approx(2.0)


def test_settings_can_switch_back_to_game_memory(qapp, config) -> None:
    dialog = SettingsDialog(config)
    dialog._capture._memory_radio.setChecked(True)
    dialog._save_and_accept()
    assert config.capture_source == "memory"


def test_the_auto_script_is_resolved_from_the_own_language_for_a_test(qapp, config) -> None:
    dialog = SettingsDialog(config)
    dialog._own_lang.setCurrentIndex(dialog._own_lang.findData("RU"))
    assert dialog._script_for_test() == "eslav"
    dialog._capture._script_combo.setCurrentIndex(dialog._capture._script_combo.findData("japan"))
    assert dialog._script_for_test() == "japan"


# ── area picker ──────────────────────────────────────────────────────────────


class _FakeScreen:
    def geometry(self) -> QRect:
        return QRect(1920, 0, 1000, 600)


def _surface(results: list) -> _PickerSurface:
    surface = _PickerSurface(_FakeScreen(), (1920, 1080), 1, results.append)
    surface.resize(1000, 600)
    surface.show()
    return surface


def test_dragging_a_box_returns_a_region_in_fractions_of_the_monitor(qapp) -> None:
    results: list = []
    surface = _surface(results)
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=QPoint(100, 300))
    QTest.mouseMove(surface, QPoint(400, 500))
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=QPoint(400, 500))
    assert len(results) == 1
    region = results[0]
    assert (region.x, region.y, region.w, region.h) == pytest.approx((0.1, 0.5, 0.3, 1 / 3))
    assert region.monitor == 1 and region.px == (1920, 1080)
    assert region.abs == (1920 + 100, 300, 300, 200)


def test_a_box_dragged_backwards_is_the_same_box(qapp) -> None:
    results: list = []
    surface = _surface(results)
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=QPoint(400, 500))
    QTest.mouseMove(surface, QPoint(100, 300))
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=QPoint(100, 300))
    assert results[0].x == pytest.approx(0.1) and results[0].w == pytest.approx(0.3)


def test_a_click_or_a_tiny_box_is_not_a_chat_window(qapp) -> None:
    results: list = []
    surface = _surface(results)
    QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=QPoint(110, 108))
    assert results == []


def _drag_through_the_window_system(surface: _PickerSurface) -> None:
    """Mouse events by the same route a real mouse takes, which honours modality.

    (QTest's widget overloads call the widget directly and would hide the bug.)
    """
    window = surface.windowHandle()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=QPoint(100, 300))
    QTest.mouseMove(window, QPoint(400, 500))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=QPoint(400, 500))


def _inside_a_modal_dialog(body) -> None:
    """Run `body` while an application-modal dialog is open, as the wizard is."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QDialog

    dialog = QDialog()

    def run() -> None:
        try:
            body(dialog)
        finally:
            dialog.accept()

    QTimer.singleShot(200, run)
    dialog.exec()


def test_the_picker_gets_the_mouse_while_a_modal_dialog_is_open(qapp) -> None:
    """On Windows every click answered with the "blocked" chime and nothing dragged.

    The wizard and settings are modal, and Qt blocks input to any window outside
    the modal dialog's hierarchy. The picker has to be a child of the dialog.
    """
    results: list = []

    def body(dialog) -> None:
        surface = _PickerSurface(_FakeScreen(), (1920, 1080), 0, results.append, parent=dialog)
        surface.resize(1000, 600)
        surface.show()
        QApplication.processEvents()
        _drag_through_the_window_system(surface)
        QApplication.processEvents()
        surface.close()

    _inside_a_modal_dialog(body)
    assert len(results) == 1


def test_an_unparented_picker_is_blocked_by_a_modal_dialog(qapp) -> None:
    """The control: this is the bug, kept as a test so the cause stays on record."""
    results: list = []

    def body(_dialog) -> None:
        surface = _PickerSurface(_FakeScreen(), (1920, 1080), 0, results.append)
        surface.resize(1000, 600)
        surface.show()
        QApplication.processEvents()
        _drag_through_the_window_system(surface)
        QApplication.processEvents()
        surface.close()

    _inside_a_modal_dialog(body)
    assert results == []


def test_the_pick_button_hands_the_picker_its_dialog_as_parent(qapp, config) -> None:
    from unittest.mock import patch

    wizard = SetupWizard(config)
    with patch("app.region_picker_qt.RegionPicker") as picker:
        wizard._capture._on_pick()
    assert picker.call_args.kwargs["parent"] is wizard._capture.window()


def test_escape_cancels(qapp) -> None:
    results: list = []
    surface = _surface(results)
    QTest.keyClick(surface, Qt.Key.Key_Escape)
    assert results == [None]


# ── the channel toggles do not apply to the screen source ────────────────────


def test_the_channels_are_greyed_out_while_the_screen_is_the_source(qapp, config) -> None:
    """The screen source translates whatever the chat window shows; the player picks
    the channels in the game. Switches here would only look as if they mattered."""
    config.capture_source = "ocr"
    dialog = SettingsDialog(config)
    assert all(not box.isEnabled() for box in dialog._channel_boxes.values())
    assert not dialog._channels_note.isHidden()


def test_the_channels_come_back_for_the_memory_source(qapp, config) -> None:
    config.capture_source = "memory"
    dialog = SettingsDialog(config)
    assert all(box.isEnabled() for box in dialog._channel_boxes.values())
    assert dialog._channels_note.isHidden()


def test_switching_the_source_in_the_dialog_switches_the_channels_with_it(qapp, config) -> None:
    config.capture_source = "ocr"
    dialog = SettingsDialog(config)
    dialog._capture._memory_radio.setChecked(True)
    assert all(box.isEnabled() for box in dialog._channel_boxes.values())
    dialog._capture._ocr_radio.setChecked(True)
    assert all(not box.isEnabled() for box in dialog._channel_boxes.values())


def test_the_toggles_keep_their_values_while_greyed_out(qapp, config) -> None:
    """Greyed out is not reset: going back to the memory source finds them as left."""
    config.capture_source = "ocr"
    config.channels_trade = True
    dialog = SettingsDialog(config)
    dialog._save_and_accept()
    assert config.channels_trade is True
