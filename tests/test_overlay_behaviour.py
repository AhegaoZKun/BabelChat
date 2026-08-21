"""What the overlay does, from the outside.

This is the surface the whole project exists to put on screen, and until now it
had three assertions against it — all about lookup tables, none about
behaviour. That is a poor position from which to move a thousand lines of it
into other modules, so these come first: they describe what the overlay does
now, so that a refactor which changes it says so.

Everything here drives the real widget under the offscreen platform. Nothing
asserts on private layout details that a reorganisation is allowed to change —
only on what a player would see.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6", reason="this is the Qt frontend")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.parser import Channel, ChatMessage  # noqa: E402
from app.pipeline import TranslatedMessage  # noqa: E402
from app.translators.base import TranslationResult  # noqa: E402


def message(
    text: str = "hello everyone",
    *,
    channel: Channel = Channel.SAY,
    author: str = "Vasya",
    translated: str | None = "всем привет",
    msg_id: int = 1,
    is_update: bool = False,
) -> TranslatedMessage:
    result = (
        TranslationResult(
            original=text,
            translated=translated,
            source_lang="EN",
            target_lang="RU",
            success=True,
            backend="fake",
        )
        if translated is not None
        else None
    )
    return TranslatedMessage(
        original=ChatMessage(timestamp="1/1 12:00:00.000", channel=channel, author=author, server="Realm", text=text),
        translation=result,
        source_lang="EN",
        msg_id=msg_id,
        is_update=is_update,
    )


@pytest.fixture
def overlay():
    from app.overlay import ChatOverlay

    app = QApplication.instance() or QApplication([])
    assert app is not None

    widget = ChatOverlay(AppConfig(wow_path=""))
    widget.resize(500, 320)
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.deleteLater()


def shown_text(overlay) -> str:
    """Everything currently rendered in the chat area."""
    return overlay._chat_area.toPlainText()


# ── messages arrive and are shown ────────────────────────────────────────────


def test_a_message_appears_with_its_author_and_text(overlay):
    overlay.add_message(message("need a tank", author="Petya"))
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert "Petya" in rendered
    assert "need a tank" in rendered


def test_the_translation_is_shown_alongside_the_original(overlay):
    """The original appears immediately and the translation arrives after it;
    both have to be readable, or the overlay is answering a question the player
    can no longer see."""
    overlay.add_message(message("need a tank", translated="нужен танк"))
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert "need a tank" in rendered
    assert "нужен танк" in rendered


def test_a_message_with_no_translation_still_shows_the_original(overlay):
    """A provider that failed must degrade to showing the message untranslated,
    not to showing nothing."""
    overlay.add_message(message("wts crest", translated=None))
    QApplication.processEvents()

    assert "wts crest" in shown_text(overlay)


def test_a_streaming_update_replaces_the_line_rather_than_adding_one(overlay):
    """The pipeline emits the original first and the translation second under
    the same msg_id. Appending both would show every message twice."""
    overlay.add_message(message("inv plz", translated=None, msg_id=7))
    QApplication.processEvents()
    overlay.add_message(message("inv plz", translated="пригласите", msg_id=7, is_update=True))
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert rendered.count("inv plz") == 1, rendered
    assert "пригласите" in rendered


def test_history_is_loaded_in_order(overlay):
    overlay.load_history([message(f"line {n}", msg_id=n) for n in range(1, 4)])
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert rendered.index("line 1") < rendered.index("line 2") < rendered.index("line 3")


# ── the filter tabs ──────────────────────────────────────────────────────────


def test_switching_to_a_channel_tab_hides_the_other_channels(overlay):
    overlay.add_message(message("guild talk", channel=Channel.GUILD, msg_id=1))
    overlay.add_message(message("trade talk", channel=Channel.TRADE, msg_id=2))
    QApplication.processEvents()

    overlay._on_filter_changed("Guild")
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert "guild talk" in rendered
    assert "trade talk" not in rendered


def test_the_all_tab_shows_everything_again(overlay):
    overlay.add_message(message("guild talk", channel=Channel.GUILD, msg_id=1))
    overlay.add_message(message("trade talk", channel=Channel.TRADE, msg_id=2))
    QApplication.processEvents()
    overlay._on_filter_changed("Guild")
    QApplication.processEvents()

    overlay._on_filter_changed("All")
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert "guild talk" in rendered and "trade talk" in rendered


def test_a_custom_channel_message_has_a_tab_that_shows_it(overlay):
    """Custom and Emote were added to the parser and to no filter table, so a
    message from either appeared under no tab but All."""
    overlay.add_message(message("private chat", channel=Channel.CUSTOM, msg_id=1))
    QApplication.processEvents()

    overlay._on_filter_changed("Custom")
    QApplication.processEvents()

    assert "private chat" in shown_text(overlay)


# ── translation on and off ───────────────────────────────────────────────────


def test_turning_translation_off_stops_new_translations_being_shown(overlay):
    overlay._toggle_translation()
    QApplication.processEvents()

    overlay.add_message(message("need a tank", translated="нужен танк"))
    QApplication.processEvents()

    rendered = shown_text(overlay)
    assert "need a tank" in rendered, "the original must still be shown"
    assert "нужен танк" not in rendered


def test_turning_translation_back_on_shows_them_again(overlay):
    overlay._toggle_translation()
    overlay._toggle_translation()
    QApplication.processEvents()

    overlay.add_message(message("need a tank", translated="нужен танк"))
    QApplication.processEvents()

    assert "нужен танк" in shown_text(overlay)


# ── the things a player clicks ───────────────────────────────────────────────


def test_the_overlay_can_be_minimised_and_restored(overlay):
    full_height = overlay.height()

    overlay._toggle_minimize()
    QApplication.processEvents()
    minimised = overlay.height()

    overlay._toggle_minimize()
    QApplication.processEvents()

    assert minimised < full_height, "minimising did not make it smaller"
    assert overlay.height() == full_height


def test_the_clipboard_action_survives_an_empty_clipboard(overlay):
    """It is reachable from a global hotkey, so it can be pressed at any moment
    including before anything has been copied."""
    QApplication.clipboard().setText("")

    overlay.translate_clipboard()


def test_the_wow_status_is_reported(overlay):
    """The title bar's connection badge is how a player finds out the companion
    cannot see the game — the commonest support question."""
    states = iter(["attached", "searching", "offline"])
    overlay.set_wow_status_checker(lambda: next(states, "offline"))

    # set_wow_status_checker does the first read itself.
    attached = overlay._wow_status.text()
    overlay._update_wow_status()
    searching = overlay._wow_status.text()
    overlay._update_wow_status()
    offline = overlay._wow_status.text()

    assert len({attached, searching, offline}) == 3, (attached, searching, offline)


# ── nothing unbounded ────────────────────────────────────────────────────────


def test_the_overlay_does_not_grow_without_limit(overlay):
    """A busy Trade channel is thousands of lines an hour, and this runs beside
    a game."""
    from app.overlay import _MAX_MESSAGES

    for n in range(_MAX_MESSAGES + 50):
        overlay.add_message(message(f"line {n}", msg_id=n))
    QApplication.processEvents()

    assert len(overlay._messages) <= _MAX_MESSAGES
