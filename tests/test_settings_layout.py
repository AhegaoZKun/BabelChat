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
        tabs.tabText(index)
        for index in range(tabs.count())
        if not isinstance(tabs.widget(index), QScrollArea)
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
