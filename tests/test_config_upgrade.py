"""What happens to a config written by an older version.

Every one of these is the same shape of bug: the app changes, the saved file
does not, and the user loses something without being told. Nothing raises, so
nothing gets reported — a kind of message simply stops appearing and the user
concludes the app is unreliable.
"""

from __future__ import annotations

import json

import pytest

from app.config import CHANNEL_TOGGLES, AppConfig


def write(tmp_path, **fields) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return str(path)


# ── emotes became their own channel ──────────────────────────────────────────


def test_a_config_from_before_the_emote_toggle_keeps_translating_emotes():
    """The reader used to deliver an emote as Say, so anyone with Say on had
    emotes translated. Splitting them out with the new toggle off would have
    stopped that on upgrade, in silence."""
    from app.config import _migrate_split_channels

    data = {"channels_say": True}
    _migrate_split_channels(data)

    assert data["channels_emote"] is True


def test_a_config_that_had_say_off_does_not_gain_emotes():
    """The migration carries the answer across; it does not invent one."""
    from app.config import _migrate_split_channels

    data = {"channels_say": False}
    _migrate_split_channels(data)

    assert data["channels_emote"] is False


def test_a_config_that_already_answered_is_left_alone():
    from app.config import _migrate_split_channels

    data = {"channels_say": True, "channels_emote": False}
    _migrate_split_channels(data)

    assert data["channels_emote"] is False, "the user turned this off; leave it off"


def test_a_fresh_install_gets_the_documented_default(tmp_path):
    """No file at all: emotes stay off, because nobody has said otherwise."""
    config = AppConfig.load(str(tmp_path / "nothing.json"))

    assert config.channels_emote is False


def test_the_upgrade_survives_a_round_trip_through_the_file(tmp_path):
    """The migration is only worth anything if it runs on the real load path."""
    path = write(tmp_path, channels_say=True, channels_trade=True)

    config = AppConfig.load(path)

    assert config.channels_emote is True
    assert config.channels_trade is True


# ── the toggles both platforms must offer ────────────────────────────────────


def test_every_channel_the_app_applies_is_offered_to_the_user():
    """Three toggles had drifted out of one screen or the other: Yell had a row
    on Linux and none on Windows, Custom and Emote the reverse. A setting the
    app applies but no screen offers cannot be seen, changed, or reported.

    Both dialogs now draw from CHANNEL_TOGGLES, so this covers both.
    """
    offered = {attribute for attribute, _key in CHANNEL_TOGGLES}
    declared = {name for name in vars(AppConfig()) if name.startswith("channels_")}

    assert declared == offered, (
        f"applied but never offered: {sorted(declared - offered)}; "
        f"offered but not a setting: {sorted(offered - declared)}"
    )


def test_every_channel_label_is_a_key_the_string_table_answers():
    """`tr` hands back the key on a miss, so a typo here reaches the screen as
    `settings.ch.yell` beside a checkbox."""
    from app.i18n import _STRINGS

    missing = [key for _attribute, key in CHANNEL_TOGGLES if key not in _STRINGS]

    assert missing == [], f"no string for: {missing}"


def test_the_settings_dialog_shows_and_saves_every_one_of_them(monkeypatch):
    """The loop is only worth anything if the dialog it feeds writes the results
    back. Ticking every box must change every setting."""
    pytest.importorskip("PyQt6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from app.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    assert app is not None

    config = AppConfig(wow_path="")
    dialog = SettingsDialog(config)

    assert set(dialog._channel_boxes) == {attribute for attribute, _key in CHANNEL_TOGGLES}

    for box in dialog._channel_boxes.values():
        box.setChecked(True)

    # The real save path, minus the two things that leave the test: writing the
    # user's config file and closing the window.
    monkeypatch.setattr(AppConfig, "save", lambda self, *a, **k: None)
    monkeypatch.setattr(type(dialog), "accept", lambda self: None)
    dialog._save_and_accept()

    still_off = [attribute for attribute, _key in CHANNEL_TOGGLES if not getattr(config, attribute)]
    dialog.deleteLater()

    assert still_off == [], f"ticked in the dialog, unchanged in the config: {still_off}"
