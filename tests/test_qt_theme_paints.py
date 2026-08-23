"""No window may show a stripe of a colour the theme never declared.

The tester's screenshot had white bars across the group headings on Windows 10.
They were not bars: the whole tab page was `#efefef`, and the group boxes are
dark and opaque, so the only place it showed through was the strip of margin a
group title sits in.

The cause was mine. Wrapping every tab and wizard page in a `QScrollArea` fixed
a real problem — those windows could not be made short enough for a laptop —
but a scroll area paints its viewport from the palette, and the stylesheet
never touched it. On a machine whose Qt palette is light, that viewport is
white; on a dark one nothing looks wrong, which is why it reached a tester and
not a test.

So the check is on the pixels, not on the stylesheet: render each window and
look for a long horizontal run of light pixels. Text gives short runs — a glyph
is a few pixels wide — and a page painted the wrong colour gives runs hundreds
of pixels long.
"""

from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("PyQt6", reason="this is the Qt frontend")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QStackedWidget, QTabWidget, QWidget  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.i18n import tr  # noqa: E402

#: Lighter than this counts as light. Measured, not guessed: Qt renders the
#: theme's accent #FFD200 as 214 in grey, its brightest text #e0e0e0 as 224,
#: and the palette white that caused all this as 239. The threshold sits above
#: the accent — the Save button's 145px border is a legitimate long light run —
#: and below the other two. Text is still caught, on purpose: it is the run
#: LENGTH that separates a letter from a painted page.
LIGHT = 0xDC

#: A run this long is not a letter. The widest glyph in these windows is under
#: 20px at the sizes they render; the bar in the screenshot was 856px.
BAR = 60

RUN = re.compile(b"[" + bytes([LIGHT]) + b"-\xff]{%d,}" % BAR)

WINDOW_SIZE = (880, 640)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def longest_light_run(widget: QWidget) -> int:
    """The longest horizontal run of light pixels anywhere in the widget.

    Read from a grayscale copy a row at a time, because a per-pixel loop over
    half a million pixels in Python is too slow to belong in a test suite.
    """
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    stride = image.bytesPerLine()
    raw = bytes(image.constBits().asarray(stride * image.height()))

    longest = 0
    for y in range(image.height()):
        row = raw[y * stride : y * stride + image.width()]
        for match in RUN.finditer(row):
            longest = max(longest, len(match.group()))
    return longest


def rendered(widget: QWidget) -> QWidget:
    widget.resize(*WINDOW_SIZE)
    widget.show()
    QApplication.processEvents()
    return widget


# ── the windows themselves ───────────────────────────────────────────────────


@pytest.mark.parametrize("tab", [0, 1, 2, 3], ids=["general", "overlay", "hotkeys", "about"])
def test_no_settings_tab_paints_a_light_page(tab, qt_app, monkeypatch):
    from app.settings_dialog import SettingsDialog

    monkeypatch.setattr(tr, "_lang", "RU")
    dialog = rendered(SettingsDialog(AppConfig(wow_path="")))
    try:
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None and tabs.count() > tab
        tabs.setCurrentIndex(tab)
        QApplication.processEvents()

        run = longest_light_run(dialog)
        assert run < BAR, f"a {run}px light stripe on the {tabs.tabText(tab)!r} tab"
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("page", [0, 1, 2, 3, 4])
def test_no_wizard_page_paints_a_light_page(page, qt_app, monkeypatch):
    """The wizard is what every fresh install sees first, and it was wrapped in
    the same scroll areas on the same day."""
    from app.setup_wizard import SetupWizard

    monkeypatch.setattr(tr, "_lang", "RU")
    wizard = rendered(SetupWizard(AppConfig(wow_path="")))
    try:
        stack = wizard.findChild(QStackedWidget)
        assert stack is not None and stack.count() > page
        stack.setCurrentIndex(page)
        QApplication.processEvents()

        run = longest_light_run(wizard)
        assert run < BAR, f"a {run}px light stripe on wizard page {page}"
    finally:
        wizard.deleteLater()


# ── and the measurement has to be able to see one ────────────────────────────


def test_the_measurement_finds_a_stripe_that_is_there(qt_app):
    """Every assertion above passes on a blank widget, and would go on passing
    if `longest_light_run` quietly returned zero."""
    from PyQt6.QtWidgets import QLabel

    painted = QLabel()
    painted.setStyleSheet("background: #efefef;")
    rendered(painted)

    assert longest_light_run(painted) >= WINDOW_SIZE[0] - 4, "a white widget did not read as light"


def test_the_measurement_does_not_call_ordinary_text_a_stripe(qt_app):
    """If it did, the tests above would fail on every window that has words in
    it, and the threshold would end up raised until it saw nothing at all."""
    from PyQt6.QtWidgets import QLabel

    label = QLabel("Прозрачность: 70%   Размер шрифта: 10   Поведение")
    label.setStyleSheet("background: #1a1a1a; color: #e0e0e0;")
    rendered(label)

    assert longest_light_run(label) < BAR


def test_the_theme_covers_the_scroll_area(qt_app):
    """The pixel checks are the real ones, but they can only run where a Qt
    platform plugin exists. This says the same thing about the stylesheet, and
    it says which rule went missing."""
    from app.qt_theme import WOW_THEME_STYLESHEET

    assert "QScrollArea" in WOW_THEME_STYLESHEET, "nothing in the theme paints the scroll areas"
    assert "qt_scrollarea_viewport" in WOW_THEME_STYLESHEET, "the viewport is what paints from the palette"
