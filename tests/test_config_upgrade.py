"""What happens to a config written by an older version.

Every one of these is the same shape of bug: the app changes, the saved file
does not, and the user loses something without being told. Nothing raises, so
nothing gets reported — a kind of message simply stops appearing and the user
concludes the app is unreliable.
"""

from __future__ import annotations

import json

import pytest

from app.config import CHANNEL_TOGGLES, AppConfig, enabled_channels


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


def test_every_toggle_the_user_sees_changes_what_the_app_listens_to():
    """The version of this test that shipped compared CHANNEL_TOGGLES against
    AppConfig's fields — two lists of names, neither of which is where "applies"
    happens. It stayed green while the Yell checkbox did nothing at all on both
    platforms, because Say quietly enabled Yell and nothing read channels_yell.

    Driving the real builder is the difference: a toggle has to change the set
    of channels the pipeline listens to, or it is decoration.
    """
    inert = []
    for toggle in CHANNEL_TOGGLES:
        off = AppConfig(**{other.field: False for other in CHANNEL_TOGGLES})
        on = AppConfig(**{other.field: (other is toggle) for other in CHANNEL_TOGGLES})
        if enabled_channels(on) <= enabled_channels(off):
            inert.append(toggle.field)

    assert inert == [], f"these toggles change nothing the pipeline listens to: {inert}"


def test_no_two_toggles_claim_the_same_channel():
    """Two boxes for one channel is how Yell ended up with a live toggle and a
    dead one sitting next to each other, both labelled Крик."""
    seen: dict[str, str] = {}
    clashes = []
    for toggle in CHANNEL_TOGGLES:
        for name in toggle.channels:
            if name in seen:
                clashes.append((name, seen[name], toggle.field))
            seen[name] = toggle.field

    assert clashes == [], f"one channel, two toggles: {clashes}"


def test_every_channel_the_parser_can_report_belongs_to_some_toggle():
    """A channel the parser emits and no toggle covers is a message the user can
    neither switch on nor switch off."""
    from app.parser import Channel

    claimed = {name for toggle in CHANNEL_TOGGLES for name in toggle.channels}
    orphans = sorted(channel.name for channel in Channel if channel.name not in claimed)

    assert orphans == [], f"no toggle covers: {orphans}"


def test_both_entry_points_build_the_same_channel_set():
    """They each had their own hand-written copy of this mapping, and the GTK
    one never grew Custom or Emote — so the emote migration was careful to keep
    emotes flowing on upgrade and Linux dropped them anyway."""
    import app.config
    import app.main

    config = AppConfig(wow_path="", channels_custom=True, channels_emote=True)
    assert app.main.enabled_channels is app.config.enabled_channels

    from app.parser import Channel

    built = app.config.enabled_channels(config)
    assert Channel.CUSTOM in built
    assert Channel.EMOTE in built


def test_every_channel_label_is_a_key_the_string_table_answers():
    """`tr` hands back the key on a miss, so a typo here reaches the screen as
    `settings.ch.yell` beside a checkbox."""
    from app.i18n import _STRINGS

    missing = [toggle.label for toggle in CHANNEL_TOGGLES if toggle.label not in _STRINGS]

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

    assert set(dialog._channel_boxes) == {toggle.field for toggle in CHANNEL_TOGGLES}

    for box in dialog._channel_boxes.values():
        box.setChecked(True)

    # The real save path, minus the two things that leave the test: writing the
    # user's config file and closing the window.
    monkeypatch.setattr(AppConfig, "save", lambda self, *a, **k: None)
    monkeypatch.setattr(type(dialog), "accept", lambda self: None)
    dialog._save_and_accept()

    still_off = [toggle.field for toggle in CHANNEL_TOGGLES if not getattr(config, toggle.field)]
    dialog.deleteLater()

    assert still_off == [], f"ticked in the dialog, unchanged in the config: {still_off}"


def test_a_config_from_before_the_yell_toggle_keeps_translating_yells():
    """Yell was bundled into Say, and the checkbox said so — "Сказать / Крик".
    Splitting them with the new toggle off would stop yells on upgrade, in
    silence, exactly as splitting out emotes would have."""
    from app.config import _migrate_split_channels

    data = {"channels_say": True}
    _migrate_split_channels(data)

    assert data["channels_yell"] is True


def test_no_channel_label_still_claims_a_channel_that_moved_out_of_it():
    """The Say label read "Сказать / Крик" while Yell had its own box beside it
    — two boxes, one of them dead, both saying Крик."""
    from app.i18n import _STRINGS

    labels = {toggle.field: _STRINGS[toggle.label] for toggle in CHANNEL_TOGGLES}

    for language in ("RU", "EN", "ES"):
        say = labels["channels_say"][language]
        yell = labels["channels_yell"][language]
        assert yell.lower() not in say.lower(), f"the Say label still claims Yell in {language}: {say!r}"


def test_every_enabled_toggle_gets_a_filter_tab_that_can_show_it():
    """A message with no tab of its own only appears under "All", so a user who
    switches to any tab stops seeing it. Custom and Emote had exactly that: two
    new channels added to the parser, and none of the overlay's four tables
    updated."""
    pytest.importorskip("PyQt6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app.main import _enabled_filter_names
    from app.overlay import _FILTER_CHANNELS
    from app.parser import Channel

    everything = AppConfig(**{toggle.field: True for toggle in CHANNEL_TOGGLES})
    tabs = _enabled_filter_names(everything)

    for toggle in CHANNEL_TOGGLES:
        assert toggle.tab in tabs, f"{toggle.field} is on but its tab is not offered"
        shown = _FILTER_CHANNELS.get(toggle.tab, set())
        for name in toggle.channels:
            assert Channel[name] in shown, f"the {toggle.tab} tab does not show {name}"


def test_every_channel_has_a_colour_and_a_prefix_in_the_overlay():
    """Without them a channel renders in Say's white with an empty prefix —
    indistinguishable from someone talking next to you, which is the wrong thing
    for a player-made channel and for an emote alike."""
    pytest.importorskip("PyQt6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from app.overlay import CHANNEL_COLORS, CHANNEL_PREFIXES
    from app.parser import Channel

    missing_colour = sorted(c.name for c in Channel if c not in CHANNEL_COLORS)
    missing_prefix = sorted(c.name for c in Channel if c not in CHANNEL_PREFIXES)

    assert missing_colour == [], f"no overlay colour for: {missing_colour}"
    assert missing_prefix == [], f"no overlay prefix for: {missing_prefix}"
