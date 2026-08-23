"""Two things a tester asked for after a good session.

The overlay collapsed towards its top-left, so one parked along the bottom of
the screen jumped into the middle of it when minimised — which is the opposite
of what minimising it is for.

And during a mythic keystone run the overlay simply stopped. That part is not
ours to fix: while the key is live the game hands chat text to addons as a
secret value, which reports as a string and raises on every operation, so
nothing can read it, forward it or translate it. What was ours to fix is that
nothing said so.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PyQt6", reason="this is the Qt frontend")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.config import AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qt_app):
    from app.overlay import ChatOverlay

    widget = ChatOverlay(AppConfig(wow_path=""))
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.deleteLater()


# ── collapsing downwards ─────────────────────────────────────────────────────


def test_the_bottom_edge_stays_put_when_collapsing(overlay):
    """The point of the request: parked on the bottom edge of the screen, it
    has to stay on the bottom edge."""
    overlay.move(200, 400)
    overlay.resize(450, 300)
    QApplication.processEvents()
    bottom = overlay.y() + overlay.height()

    overlay._toggle_minimize()
    QApplication.processEvents()

    assert overlay.y() + overlay.height() == pytest.approx(bottom, abs=2)


def test_it_grows_back_from_the_same_edge(overlay):
    overlay.move(200, 400)
    overlay.resize(450, 300)
    QApplication.processEvents()
    bottom = overlay.y() + overlay.height()

    overlay._toggle_minimize()
    QApplication.processEvents()
    overlay._toggle_minimize()
    QApplication.processEvents()

    assert overlay.y() + overlay.height() == pytest.approx(bottom, abs=2)
    assert overlay.height() == 300, "the restored height is wrong"


def test_the_horizontal_position_is_left_alone(overlay):
    """Only the vertical anchor was asked about, and moving sideways as well
    would be a surprise."""
    overlay.move(321, 400)
    QApplication.processEvents()

    overlay._toggle_minimize()
    QApplication.processEvents()

    assert overlay.x() == 321


def test_collapsing_near_the_top_does_not_push_it_off_the_screen(overlay):
    """Growing back upwards from the bottom edge can ask for a negative y, and
    a window whose title bar is above the screen cannot be dragged back."""
    from app.overlay import _on_screen_y

    assert _on_screen_y(overlay, -500) >= 0


def test_collapsing_near_the_bottom_does_not_push_it_off_either(overlay):
    from app.overlay import _on_screen_y

    screen = overlay.screen() or QApplication.primaryScreen()
    available = screen.availableGeometry()

    assert _on_screen_y(overlay, available.bottom() + 500) < available.bottom()


# ── and saying why the chat went quiet ───────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_the_addon_saying_it_is_locked_reaches_the_reader():
    pytest.importorskip("pymem")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    reader._deliver_new_messages("0|META|LOCKED|1\n1|RAW|SAY|Player|hello")

    assert reader.problem == module.CHAT_LOCKED


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_the_lock_survives_a_successful_poll():
    """`_problem` is cleared every time something arrives, and the buffer keeps
    arriving during a key — it just has nothing readable in it. A lock that was
    reported once and then forgotten would be worse than none."""
    pytest.importorskip("pymem")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    reader._deliver_new_messages("0|META|LOCKED|1")
    reader._problem = ""  # what a successful poll does

    assert reader.problem == module.CHAT_LOCKED


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_the_lock_lifts_when_the_addon_says_so():
    """The run ends and translation has to come back on its own."""
    pytest.importorskip("pymem")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    reader._deliver_new_messages("0|META|LOCKED|1")
    assert reader.problem == module.CHAT_LOCKED

    reader._deliver_new_messages("0|META|LOCKED|0")

    assert reader.problem == ""


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_ordinary_chat_never_reports_a_lock():
    """Otherwise the notice is noise on every working setup."""
    pytest.importorskip("pymem")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    reader._deliver_new_messages("0|META|PLAYER|Someone-Realm\n1|RAW|SAY|Player|hello")

    assert reader.problem == ""


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows memory reader")
def test_a_real_fault_is_reported_ahead_of_the_lock():
    """Being refused the process is something the user can act on; a keystone
    run is not."""
    pytest.importorskip("pymem")
    import app.memory_reader_windows as module

    reader = module.WoWAddonBufReader(lambda *_a, **_k: None)
    reader._deliver_new_messages("0|META|LOCKED|1")
    reader._problem = module.ACCESS_DENIED

    assert reader.problem == module.ACCESS_DENIED


def test_the_indicator_explains_the_lock_in_every_language():
    from app.locales import LANGUAGE_MODULES
    from app.overlay import ChatOverlay

    assert "chat_locked" in ChatOverlay._WOW_STATES
    _label, colour, key = ChatOverlay._WOW_STATES["chat_locked"]

    assert colour != ChatOverlay._WOW_STATES["attached"][1], "a lock is not a healthy connection"
    for language, module in LANGUAGE_MODULES.items():
        assert key in module.STRINGS, f"{key} has no {language} copy"
        text = module.STRINGS[key]
        assert len(text.split()) >= 15, f"{key} in {language} does not explain enough"


def test_the_copy_says_it_is_not_our_fault_and_not_permanent():
    """A message that only says "no translation" invites a bug report. This one
    has to say whose limit it is and that it lifts by itself."""
    from app.locales import LANGUAGE_MODULES

    russian = LANGUAGE_MODULES["RU"].STRINGS["overlay.wow.chat_locked"]

    assert "Blizzard" in russian
    assert "рейд" in russian.lower(), "it does not say where translation still works"


def test_the_addon_reports_being_refused():
    import pathlib

    body = (pathlib.Path(__file__).resolve().parent.parent / "addon" / "BabelChat" / "CompanionBuffer.lua").read_text(
        encoding="utf-8"
    )

    assert "lastRefusal = GetTime()" in body, "a refused value is dropped without a word, as before"
    assert '"0|META|LOCKED|" .. locked' in body, "the buffer does not carry the lock"
    assert "REFUSAL_MEMORY" in body, "the lock never lifts, or lifts on the next message"


def test_the_bottom_is_measured_before_anything_can_grow_the_window():
    """Behaviour cannot pin this: under the offscreen platform used in tests,
    showing the widgets back does not change the height until the event loop
    runs, so a measurement taken after them still reads the collapsed size and
    the test passes. On a real screen it does not — the window is already tall
    by then and the restored one lands a hundred pixels low. So the order is
    pinned in the source, which is where the mistake would be made."""
    import pathlib

    body = (pathlib.Path(__file__).resolve().parent.parent / "app" / "overlay.py").read_text(encoding="utf-8")
    restore = body[body.index("        else:\n            # Restore") :]
    restore = restore[: restore.index("def _on_opacity_changed")]

    measured = restore.index("bottom = self.y() + self.height()")
    for grows in ("self._toolbar.show()", "self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)"):
        assert measured < restore.index(grows), f"the bottom edge is measured after {grows}, which has already grown it"
