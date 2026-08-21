"""Getting a GigaChat credential in, without knowing what base64 is.

Sber's portal shows a Client ID and a Client Secret, and separately an
"authorization key" that is just base64 of `id:secret`. The settings screen used
to ask for the base64 form, which is the field people got stuck on — so it now
asks for the two obvious values and does the encoding itself.

The awkward part is not the encoding. It is that a config saved before this
still holds the encoded form, and losing somebody's working credential to a
cosmetic change would be a far worse outcome than the awkward field.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.config import AppConfig, _migrate_gigachat_credential
from app.translators.gigachat_provider import SPEC, authorization_key, split_authorization_key


def encoded(client_id: str, client_secret: str) -> str:
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")


# ── what the settings screen asks for ────────────────────────────────────────


def test_the_two_fields_are_named_the_way_the_portal_names_them():
    """A field called "Base64 of client_id:client_secret" is a field an ordinary
    player cannot fill in."""
    keys = [field.key for field in SPEC.fields]

    assert "client_id" in keys
    assert "client_secret" in keys
    assert "authorization_key" not in keys, "the base64 field is gone from the screen"


def test_the_app_does_the_encoding():
    settings = {"client_id": "abc-123", "client_secret": "s3cret"}

    assert authorization_key(settings) == encoded("abc-123", "s3cret")


def test_surrounding_whitespace_from_a_paste_is_ignored():
    """Copying from a web page brings spaces and newlines with it."""
    settings = {"client_id": "  abc-123\n", "client_secret": "\ts3cret  "}

    assert authorization_key(settings) == encoded("abc-123", "s3cret")


def test_half_a_credential_is_not_treated_as_configured():
    assert SPEC.is_configured({"client_id": "abc-123"}) is False
    assert SPEC.is_configured({"client_secret": "s3cret"}) is False
    assert SPEC.is_configured({"client_id": "abc-123", "client_secret": "s3cret"}) is True


# ── and what happens to a credential saved before that ───────────────────────


def test_a_key_saved_before_the_split_still_authenticates():
    """The backend falls back to it, so nothing breaks even if the migration
    below never recognises it."""
    settings = {"authorization_key": encoded("abc-123", "s3cret")}

    assert authorization_key(settings) == encoded("abc-123", "s3cret")


def test_a_saved_key_is_decoded_back_into_the_two_fields():
    data = {"providers": {"gigachat": {"authorization_key": encoded("abc-123", "s3cret")}}}

    _migrate_gigachat_credential(data)

    assert data["providers"]["gigachat"] == {"client_id": "abc-123", "client_secret": "s3cret"}


@pytest.mark.parametrize(
    "stored",
    ["not-base64!!", "", "YWJj", base64.b64encode(b"no-colon-here").decode(), "   "],
    ids=["junk", "empty", "no_colon_decoded", "no_colon", "blank"],
)
def test_a_key_that_is_not_a_pair_is_left_alone_rather_than_thrown_away(stored):
    """It might still be a working credential this function simply does not
    recognise. Keeping it costs nothing; discarding it costs the user their
    setup."""
    data = {"providers": {"gigachat": {"authorization_key": stored}}}

    _migrate_gigachat_credential(data)

    assert data["providers"]["gigachat"]["authorization_key"] == stored
    assert "client_id" not in data["providers"]["gigachat"]


def test_a_config_already_holding_two_fields_is_not_rewritten():
    data = {
        "providers": {
            "gigachat": {"client_id": "mine", "client_secret": "also-mine", "authorization_key": encoded("x", "y")}
        }
    }

    _migrate_gigachat_credential(data)

    assert data["providers"]["gigachat"]["client_id"] == "mine"


def test_the_migration_survives_a_config_shaped_like_nothing():
    for data in ({}, {"providers": None}, {"providers": {"gigachat": None}}, {"providers": {"gigachat": "nonsense"}}):
        _migrate_gigachat_credential(data)


def test_an_old_config_round_trips_through_a_real_load(tmp_path):
    """The migration is only worth anything if it runs on the load path."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"providers": {"gigachat": {"authorization_key": encoded("abc-123", "s3cret")}}}),
        encoding="utf-8",
    )

    config = AppConfig.load(str(path))

    assert config.providers["gigachat"]["client_id"] == "abc-123"
    assert config.providers["gigachat"]["client_secret"] == "s3cret"


def test_split_and_encode_are_inverses():
    assert split_authorization_key(encoded("abc-123", "s3cret")) == ("abc-123", "s3cret")
    assert split_authorization_key(encoded("id", "secret:with:colons")) == ("id", "secret:with:colons")


# ── the guide the field cannot fit ───────────────────────────────────────────


def test_the_provider_points_at_step_by_step_instructions():
    """Two well-named fields still leave "which page of the portal, and what do
    I click" unanswered, and that is where people gave up."""
    assert SPEC.guide.startswith("https://")
    assert "gigachat" in SPEC.guide.lower()
