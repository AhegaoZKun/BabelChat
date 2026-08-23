"""Which log channel a public WoW channel lands in.

The classifier matched English words against a display name the client
localises. On a Russian client "Торговля" matched nothing, so every public
channel — Trade included — was filed as General, and a player-made channel was
filed as General too. Ticking "Trade" therefore did nothing, and a private
channel was routed under a toggle that says something else.

The addon now sends the channel's type id, which is the same number on every
locale and 0 for a channel a player made.
"""

from __future__ import annotations

import pytest

from app.addon_protocol import (
    CUSTOM_CHANNEL,
    classify_public_channel,
    make_synthetic_log_line,
    parse_channel_token,
)
from app.parser import Channel, parse_line

# ── the type id decides ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("channel_type", "expected"),
    [(1, "General"), (2, "Trade"), (26, "LookingForGroup")],
)
def test_a_known_type_id_decides_regardless_of_the_name(channel_type, expected):
    """Even a name in a language nobody wrote a hint for."""
    assert classify_public_channel(channel_type, "무엇이든") == expected


@pytest.mark.parametrize(
    "localised",
    [
        "Trade - City",
        "Торговля - Оргриммар",
        "Handel - Sturmwind",
        "Commerce - Hurlevent",
        "Comercio - Ventormenta",
        "Commercio - Roccavento",
        "Comércio - Correnteza",
    ],
)
def test_trade_is_recognised_in_every_language_we_have_a_name_for(localised):
    """This is the path an addon too old to send the id still takes — and None,
    not 0, is what "no id" looks like."""
    assert classify_public_channel(None, localised) == "Trade"


@pytest.mark.parametrize(
    "localised",
    ["General - City", "Общий - Оргриммар", "Allgemein - Sturmwind", "Général - Hurlevent"],
)
def test_general_is_recognised_in_every_language_we_have_a_name_for(localised):
    assert classify_public_channel(None, localised) == "General"


# ── a channel nobody knows ───────────────────────────────────────────────────


def test_a_player_made_channel_is_not_passed_off_as_general():
    """It was, and that is why a message in one landed under the General toggle
    while the user believed General meant the game's General channel."""
    assert classify_public_channel(None, "BabelTest") == CUSTOM_CHANNEL
    assert classify_public_channel(None, "GuildRecruitTalk") == CUSTOM_CHANNEL


def test_an_unknown_type_id_falls_back_to_the_name_before_giving_up():
    assert classify_public_channel(999, "Торговля - Оргриммар") == "Trade"
    assert classify_public_channel(999, "SomethingElse") == CUSTOM_CHANNEL


# ── the token the addon sends ────────────────────────────────────────────────


def test_the_current_token_carries_the_id_and_the_name():
    assert parse_channel_token("CHANNEL:2:Торговля - Оргриммар") == (2, "Торговля - Оргриммар")


def test_a_token_from_an_older_addon_still_parses():
    """3.3.0 sent "CHANNEL:<name>" with no id. An app updated ahead of its
    addon — the normal case, since the addon is copied by hand — must cope."""
    assert parse_channel_token("CHANNEL:2. Trade - City") == (None, "2. Trade - City")


def test_a_channel_name_containing_a_colon_survives():
    assert parse_channel_token("CHANNEL:0:LFM: mythic +10") == (0, "LFM: mythic +10")
    assert parse_channel_token("CHANNEL:2:LFM: mythic +10") == (2, "LFM: mythic +10")


def test_a_malformed_token_does_not_raise():
    assert parse_channel_token("CHANNEL:") == (None, "")
    assert parse_channel_token("CHANNEL") == (None, "")


# ── end to end, through the parser ───────────────────────────────────────────


def deliver(event: str) -> Channel | None:
    line = make_synthetic_log_line(event, "Vasya", "wts crest")
    assert line is not None
    message = parse_line(line)
    return message.channel if message else None


def test_a_russian_client_trade_message_arrives_as_trade():
    """The whole point: on a ruRU client this used to arrive as General, so the
    Trade toggle controlled nothing and the General toggle controlled Trade."""
    assert deliver("CHANNEL:2:Торговля - Оргриммар") == Channel.TRADE


def test_a_russian_client_general_message_arrives_as_general():
    assert deliver("CHANNEL:1:Общий - Оргриммар") == Channel.GENERAL


def test_a_custom_channel_message_arrives_as_custom():
    assert deliver("CHANNEL:0:BabelTest") == Channel.CUSTOM


def test_an_emote_is_no_longer_indistinguishable_from_speech():
    """EMOTE was mapped onto Say, so /e and /s were the same channel."""
    assert deliver("EMOTE") == Channel.EMOTE
    assert deliver("SAY") == Channel.SAY


# ── the toggles that follow from it ──────────────────────────────────────────


def test_a_player_made_channel_is_on_and_an_emote_is_not():
    """This used to say both were off, on the grounds that a player-made
    channel is private and sending it to a translation service should be a
    decision. That argument does not survive contact with the rest of the
    defaults: whispers and guild chat are on, and they are far more private
    than a channel anyone can /join.

    What actually happens is the failure a tester hit — join a channel, type in
    it, get nothing, with the reason in a debug log nobody has switched on. You
    only end up in one of these by asking to, and it is usually the guild's alt
    channel or a group of friends.

    Emotes stay off: nobody joins those, they arrive whether you want them or
    not, and "Player looks around" is not worth an API call."""
    from app.config import AppConfig

    config = AppConfig()
    assert config.channels_custom is True
    assert config.channels_emote is False


def test_enabling_a_channel_puts_it_in_the_pipeline_set():
    from app.config import AppConfig
    from app.main import _build_pipeline_config

    config = AppConfig(channels_custom=True, channels_emote=True, wow_path="")
    enabled = _build_pipeline_config(config).enabled_channels

    assert Channel.CUSTOM in enabled
    assert Channel.EMOTE in enabled


def test_leaving_a_channel_off_keeps_it_out_of_the_pipeline_set():
    """The toggle and the set the pipeline filters against are built
    separately, so one can be right while the other is not."""
    from app.config import AppConfig
    from app.main import _build_pipeline_config

    enabled = _build_pipeline_config(AppConfig(wow_path="")).enabled_channels

    assert Channel.EMOTE not in enabled
    assert Channel.TRADE not in enabled
    assert Channel.CUSTOM in enabled, "a channel you joined on purpose is not spam"


# ── zero is an answer, not a shrug ───────────────────────────────────────────


def test_a_player_made_channel_named_after_a_real_one_stays_custom():
    """WoW reports zoneChannelID 0 for a channel a player created. Treating that
    0 as "the addon told us nothing" sent a private channel called "TradeHub"
    into Trade — a toggle plenty of users leave on — and the message the user
    thought was private was translated and shown.

    The id, when present, is the game speaking. It outranks the name.
    """
    assert classify_public_channel(0, "TradeHub") == CUSTOM_CHANNEL
    assert classify_public_channel(0, "Торговля моей гильдии") == CUSTOM_CHANNEL
    assert classify_public_channel(0, "General chat for my guild") == CUSTOM_CHANNEL


def test_no_id_and_an_id_of_zero_are_different_answers():
    """The whole point of the None: they must not classify the same way."""
    assert classify_public_channel(None, "Торговля - Оргриммар") == "Trade"
    assert classify_public_channel(0, "Торговля - Оргриммар") == CUSTOM_CHANNEL


def test_a_legacy_token_reports_no_id_rather_than_zero():
    """An addon too old to send an id must not be mistaken for one reporting a
    player-made channel."""
    assert parse_channel_token("CHANNEL:Торговля - Оргриммар")[0] is None
    assert parse_channel_token("CHANNEL:0:Торговля - Оргриммар")[0] == 0


def test_a_channel_name_containing_a_colon_is_not_read_as_an_id():
    """"CHANNEL:Trade: Orgrimmar" from a legacy addon splits into something that
    looks like a 3-part token. Reading the head as an id of 0 would classify a
    real Trade channel as Custom and stop translating it."""
    assert parse_channel_token("CHANNEL:Trade: Orgrimmar") == (None, "Trade: Orgrimmar")
    assert parse_channel_token("CHANNEL:Торговля: Оргриммар") == (None, "Торговля: Оргриммар")

    for token in ("CHANNEL:Trade: Orgrimmar", "CHANNEL:Торговля: Оргриммар"):
        channel_type, name = parse_channel_token(token)
        assert classify_public_channel(channel_type, name) == "Trade"
