"""The settings window has to fit on the screen and show what it renders.

Both defects here were reported by looking at the window, not by anything
failing: the quota figure was half cut off, and the dialog could not be made
short enough to fit a laptop screen. Neither raises, so neither had a test.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6", reason="this is the Qt frontend")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.i18n import tr  # noqa: E402

#: The shortest screen worth supporting, minus a taskbar. A 1366x768 laptop is
#: still the second most common resolution among PC players.
SMALL_SCREEN = (1366, 700)


@pytest.fixture
def dialog(monkeypatch):
    from app.settings_dialog import SettingsDialog

    monkeypatch.setattr(tr, "_lang", "RU")
    app = QApplication.instance() or QApplication([])
    assert app is not None

    widget = SettingsDialog(AppConfig(wow_path=""))
    yield widget
    widget.deleteLater()


def visible_labels(widget: QWidget) -> list[QLabel]:
    return [
        label
        for label in widget.findChildren(QLabel)
        if label.isVisibleTo(widget) and label.text().strip() and not label.text().startswith("<")
    ]


# ── it has to fit ────────────────────────────────────────────────────────────


def test_the_window_can_be_made_small_enough_for_a_laptop(dialog):
    """With four providers to configure the General tab needed 1020px, and the
    dialog had no scroll area — so on any screen below about 1100px the Save
    button sat past the bottom edge with no way to reach it."""
    width, height = SMALL_SCREEN
    minimum = dialog.minimumSizeHint()

    assert minimum.height() <= height, f"the dialog cannot be shorter than {minimum.height()}px"
    assert minimum.width() <= width, f"the dialog cannot be narrower than {minimum.width()}px"


def test_every_tab_scrolls(dialog):
    """A tab that does not scroll is a tab whose contents get squeezed instead,
    which is how an 11px caption ended up in a 9px box."""
    from PyQt6.QtWidgets import QScrollArea, QTabWidget

    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None

    not_scrolling = [
        tabs.tabText(index) for index in range(tabs.count()) if not isinstance(tabs.widget(index), QScrollArea)
    ]

    assert not_scrolling == [], f"these tabs cannot scroll: {not_scrolling}"


# ── and it has to show what it renders ───────────────────────────────────────


def test_no_label_is_shorter_than_the_text_inside_it(dialog):
    """The quota figure was rendered into a 9px box in an 11px font, so its
    bottom half was cut off — visible to the user, invisible to every test."""
    dialog.resize(*SMALL_SCREEN)
    dialog.show()
    QApplication.processEvents()

    clipped = [
        (label.text()[:40], label.height(), label.fontMetrics().height())
        for label in visible_labels(dialog)
        if label.height() < label.fontMetrics().height()
    ]

    assert clipped == [], f"text taller than the box it is drawn in: {clipped}"


def test_the_quota_figure_is_shown_in_full(dialog):
    """It is the one number in this window that tells you whether you are about
    to run out, so half of it is worse than none of it."""
    dialog.resize(*SMALL_SCREEN)
    dialog.show()
    QApplication.processEvents()

    usage = dialog._provider_group._rows["deepl"].usage
    assert usage.show_detail("42/500,000 (0%)") is True
    QApplication.processEvents()

    label = usage._detail
    assert label.text(), "the quota figure is empty"
    assert label.height() >= label.fontMetrics().height(), "the figure is cut off vertically"
    assert label.sizeHint().width() <= label.width(), "the figure is cut off horizontally"


def test_a_provider_without_a_quota_shows_no_bar(dialog):
    """MyMemory reports words a day, not a percentage; a bar stuck at zero would
    read as "you have used none of your allowance, ever"."""
    usage = dialog._provider_group._rows["mymemory"].usage

    assert usage.show_detail("valid — 5,000 words a day") is False
    assert usage.isHidden()


# ── the wizard, which every fresh install goes through ───────────────────────


@pytest.fixture
def wizard(monkeypatch):
    from app.setup_wizard import SetupWizard

    monkeypatch.setattr(tr, "_lang", "RU")
    app = QApplication.instance() or QApplication([])
    assert app is not None

    widget = SetupWizard(AppConfig(wow_path=""))
    yield widget
    widget.deleteLater()


def test_the_wizard_fits_the_screen_it_declares_it_fits(wizard):
    """It declared a 550x480 minimum while its layout demanded 1241x767 in
    Russian. A Qt layout short of vertical space does not clip, it squeezes —
    so the declared minimum was not a size the window worked at, it was a size
    the window was broken at."""
    minimum = wizard.minimumSizeHint()
    declared = wizard.minimumSize()

    assert minimum.height() <= max(declared.height(), 1), (
        f"the layout needs {minimum.height()}px but the window says it works at {declared.height()}px"
    )
    assert minimum.width() <= max(declared.width(), 1)


def test_every_credential_field_on_the_provider_page_is_usable(wizard):
    """This is step two of five on every fresh install. The fields rendered at
    6-14px against a 32px minimum, and the Validate buttons came out as blank
    slivers."""
    from PyQt6.QtWidgets import QLineEdit

    wizard.resize(560, 500)
    wizard.show()
    QApplication.processEvents()
    wizard._stack.setCurrentIndex(1)
    QApplication.processEvents()

    fields = [edit for edit in wizard.findChildren(QLineEdit) if edit.isVisible()]

    assert fields, "the provider page shows no credential fields at all"
    too_short = [(f.placeholderText()[:30], f.height()) for f in fields if f.height() < 24]
    assert too_short == [], f"unusable input boxes: {too_short}"


def test_every_wizard_page_scrolls(wizard):
    """A page that cannot scroll is a page whose contents get squeezed."""
    from PyQt6.QtWidgets import QScrollArea

    not_scrolling = [
        index for index in range(wizard._stack.count()) if not isinstance(wizard._stack.widget(index), QScrollArea)
    ]

    assert not_scrolling == [], f"pages that cannot scroll: {not_scrolling}"


@pytest.mark.parametrize("language", ["RU", "EN", "ES"])
def test_neither_window_demands_more_room_than_a_laptop_has(language, monkeypatch):
    """Checked in every language, because the longest translation decides."""
    from app.settings_dialog import SettingsDialog
    from app.setup_wizard import SetupWizard

    monkeypatch.setattr(tr, "_lang", language)
    app = QApplication.instance() or QApplication([])
    assert app is not None

    width, height = SMALL_SCREEN
    for build in (SettingsDialog, SetupWizard):
        window = build(AppConfig(wow_path=""))
        minimum = window.minimumSizeHint()
        window.deleteLater()
        assert minimum.height() <= height, f"{build.__name__} in {language} needs {minimum.height()}px"
        assert minimum.width() <= width, f"{build.__name__} in {language} needs {minimum.width()}px"


# ── the window opens wide enough to read ─────────────────────────────────────


@pytest.mark.parametrize("build_name", ["SettingsDialog", "SetupWizard"])
def test_the_window_opens_at_the_width_of_its_content(build_name, monkeypatch):
    """A scroll area reports a small size hint whatever it holds, and horizontal
    scrolling is off on purpose — so a window sized from its own hint opened
    narrower than its rows and clipped their right-hand side with nothing to
    scroll. The first screenshot of the provider page showed exactly that."""
    from PyQt6.QtWidgets import QScrollArea

    import app.settings_dialog as settings_module
    import app.setup_wizard as wizard_module

    monkeypatch.setattr(tr, "_lang", "RU")
    app = QApplication.instance() or QApplication([])
    assert app is not None

    build = getattr(settings_module, build_name, None) or getattr(wizard_module, build_name)
    window = build(AppConfig(wow_path=""))
    try:
        contents = [area.widget() for area in window.findChildren(QScrollArea) if area.widget() is not None]
        assert contents, "nothing scrollable to measure"
        # What the rows need, not what a word-wrapped paragraph claims it needs
        # on one line — that is twelve hundred pixels, and wrapping is exactly
        # what those labels are for.
        from app.qt_widgets import content_width

        widest = max(content_width(content) for content in contents)

        assert window.width() >= widest, f"opens at {window.width()}px against {widest}px of rows"
    finally:
        window.deleteLater()


def test_every_credential_field_says_what_goes_in_it(dialog):
    """A placeholder disappears the moment you type, and these fields echo as
    dots — so a filled-in form gave no way to tell which value went where."""
    from PyQt6.QtWidgets import QLabel

    captions = {label.text().split(" —")[0] for label in dialog.findChildren(QLabel) if label.text()}

    for field in dialog._provider_group._rows["gigachat"].spec.fields:
        assert field.label_text() in captions, f"{field.key} has no visible caption"


def test_no_button_is_narrower_than_the_glyph_on_it(dialog):
    """The reveal button was fixed at 30px against a 42px glyph and rendered as
    an empty box."""
    from PyQt6.QtWidgets import QPushButton

    dialog.resize(*SMALL_SCREEN)
    dialog.show()
    QApplication.processEvents()

    squashed = [
        (button.text(), button.sizeHint().width(), button.width())
        for button in dialog.findChildren(QPushButton)
        if button.isVisible() and button.text() and button.sizeHint().width() > button.width() + 2
    ]

    assert squashed == [], f"buttons narrower than their own labels: {squashed}"
