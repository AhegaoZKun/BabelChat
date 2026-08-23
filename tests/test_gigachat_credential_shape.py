"""What a bare `http_400` was hiding, and the paste that produced it.

Sber's project page shows three values: a Client ID, a Client Secret, and an
"authorization key" that is base64 of the first two joined by a colon. The
authorization key is the longest and the most key-looking of the three, so it
is the one that ends up in a field labelled *secret* — and the app encoded it a
second time, producing a header the server could not decode. What it told the
user was `http_400`.

Confirmed against the live endpoint with a real credential: the two values as
documented return 200; `base64(id + ":" + authorization_key)` returns
`{"code": 4, "message": "Can't decode 'Authorization' header"}`; a project on
the corporate tariff returns `{"code": 7, ...}` — also a 400, and a completely
different thing to fix; and a well-formed pair that is simply wrong returns
401, not 400.

So the shape is now accepted wherever it was pasted, and the two 400s are told
apart before anyone sees a status code.
"""

from __future__ import annotations

import base64

import pytest

from app.translators.base import FAILURE
from app.translators.gigachat_provider import (
    CREDENTIAL_MISSING,
    CREDENTIAL_SHAPE,
    SCOPE_REJECTED,
    SPEC,
    _why_rejected,
    authorization_key,
)
from tests.translator_fakes import FakeResponse

#: Shaped like the real ones — a UUID each — without being anybody's.
CLIENT_ID = "01234567-89ab-cdef-0123-456789abcdef"
CLIENT_SECRET = "fedcba98-7654-3210-fedc-ba9876543210"
AUTH_KEY = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()


# ── the paste that produced the 400 ──────────────────────────────────────────


def test_the_two_documented_values_still_produce_the_key():
    built = authorization_key({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})

    assert built == AUTH_KEY


def test_the_authorization_key_in_the_secret_field_is_taken_as_it_is():
    """Encoding it again is what the server could not decode."""
    built = authorization_key({"client_id": CLIENT_ID, "client_secret": AUTH_KEY})

    assert built == AUTH_KEY
    assert built != base64.b64encode(f"{CLIENT_ID}:{AUTH_KEY}".encode()).decode()


def test_the_authorization_key_in_the_id_field_works_too():
    """The fields are adjacent and the labels are in English on a Russian
    screen; there is no reason to guess which one it lands in."""
    assert authorization_key({"client_id": AUTH_KEY, "client_secret": ""}) == AUTH_KEY


def test_a_real_secret_is_never_mistaken_for_an_authorization_key():
    """This is what makes the detection safe rather than clever: a Client
    Secret is a UUID, and its dashes are outside the base64 alphabet, so it
    cannot decode into an id:secret pair by accident."""
    built = authorization_key({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})

    assert built == AUTH_KEY, "a plain secret was misread as an authorization key"


@pytest.mark.parametrize(
    "pasted",
    [
        base64.b64encode(b"no-colon-in-here").decode(),
        base64.b64encode(b":secret-with-no-id").decode(),
        base64.b64encode(b"id-with-no-secret:").decode(),
        "not base64 at all",
    ],
    ids=["no_colon", "no_id", "no_secret", "not_base64"],
)
def test_something_that_only_looks_like_a_key_is_not_treated_as_one(pasted):
    """Everything that is not a decodable id:secret pair keeps the old
    behaviour, which is to encode the two fields and let the server judge."""
    built = authorization_key({"client_id": CLIENT_ID, "client_secret": pasted})

    assert built == base64.b64encode(f"{CLIENT_ID}:{pasted}".encode()).decode()


def test_a_truncated_authorization_key_is_still_treated_as_one():
    """It decodes to an id and a shortened secret, so it is recognised — and it
    should be. Encoding it again would turn a credential the server can reject
    with a plain 401 into a header it cannot read at all, which is the less
    useful of the two answers."""
    clipped = AUTH_KEY[:-8]

    assert authorization_key({"client_id": CLIENT_ID, "client_secret": clipped}) == clipped


# ── telling the two 400s apart ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"code": 4, "message": "Can't decode 'Authorization' header"}, CREDENTIAL_SHAPE),
        ({"code": 7, "message": "scope from db not fully includes consumed scope"}, SCOPE_REJECTED),
        ({"message": "Can't decode 'Authorization' header"}, CREDENTIAL_SHAPE),
        ({"message": "consumed scope is not allowed"}, SCOPE_REJECTED),
        ({"code": 99, "message": "something new"}, "http_400"),
        ({}, "http_400"),
    ],
    ids=["code_4", "code_7", "message_only_decode", "message_only_scope", "unknown_code", "empty"],
)
def test_the_body_says_which_400_this_is(payload, expected):
    assert _why_rejected(FakeResponse(400, payload)) == expected


def test_a_400_that_is_not_json_stays_a_400():
    """A proxy's HTML error page is a 400 too, and calling that a malformed
    credential sends the reader after the wrong thing entirely."""
    assert _why_rejected(FakeResponse(400, raises_json=True)) == "http_400"


# ── the half-filled form ─────────────────────────────────────────────────────


def test_one_field_filled_names_the_missing_one_without_asking_the_server():
    ok, detail = SPEC.validate({"client_id": CLIENT_ID, "client_secret": ""})

    assert ok is False
    assert detail == CREDENTIAL_MISSING


def test_an_untouched_form_is_not_reported_as_a_half_filled_one():
    """ "Fill in both fields" reads as a correction; on a form nobody has typed
    into it is just noise."""
    ok, detail = SPEC.validate({"client_id": "", "client_secret": ""})

    assert ok is False
    assert detail == FAILURE.NO_KEY


def test_the_backend_reports_the_reason_not_the_status():
    """`_why_rejected` being right is no use if the login path never asks it.
    Putting the status back is a one-line change that every test above
    survives, so this is the one that has to notice."""
    from app.translators.base import RetryPolicy
    from app.translators.gigachat_provider import OAUTH_URL, GigaChatBackend
    from tests.translator_fakes import FakeSession

    backend = GigaChatBackend(AUTH_KEY, retry=RetryPolicy(attempts=1, delay=0))
    session = FakeSession()
    backend._session = session
    session.script(OAUTH_URL, FakeResponse(400, {"code": 4, "message": "Can't decode 'Authorization' header"}))

    ok, detail = backend.validate()

    assert ok is False
    assert detail == CREDENTIAL_SHAPE, "the login path is still reporting the bare status"


def test_a_rejected_scope_reaches_the_caller_the_same_way():
    from app.translators.base import RetryPolicy
    from app.translators.gigachat_provider import OAUTH_URL, GigaChatBackend
    from tests.translator_fakes import FakeSession

    backend = GigaChatBackend(AUTH_KEY, retry=RetryPolicy(attempts=1, delay=0))
    session = FakeSession()
    backend._session = session
    body = {"code": 7, "message": "scope from db not fully includes consumed scope"}
    session.script(OAUTH_URL, FakeResponse(400, body))

    assert backend.validate() == (False, SCOPE_REJECTED)


# ── and every one of them has something to show ──────────────────────────────


@pytest.mark.parametrize("code", [CREDENTIAL_SHAPE, SCOPE_REJECTED, CREDENTIAL_MISSING])
@pytest.mark.parametrize("language", ["RU", "EN", "ES"])
def test_each_new_failure_has_copy_in_every_language(code, language):
    """Read from the language's own module rather than through `tr`, which
    falls back to English for a missing key. That fallback is right at runtime
    and useless here: deleting the Russian entry left `tr` returning perfectly
    good English, and a test that accepted it could not see the deletion.

    The Russian text is the one that matters most — the guide, the wizard and
    the players this provider exists for are all Russian-speaking."""
    from app.locales import LANGUAGE_MODULES

    strings = LANGUAGE_MODULES[language].STRINGS
    key = f"error.{code}"

    assert key in strings, f"{code} has no {language} copy of its own"
    assert len(strings[key].split()) >= 8, f"{code} in {language} is too short to explain anything"


def test_the_three_languages_do_not_share_one_text():
    """Three identical strings mean two of them were copied and never
    translated, which the presence check above cannot tell from real copy."""
    from app.locales import LANGUAGE_MODULES

    for code in (CREDENTIAL_SHAPE, SCOPE_REJECTED, CREDENTIAL_MISSING):
        texts = {lang: module.STRINGS[f"error.{code}"] for lang, module in LANGUAGE_MODULES.items()}
        assert len(set(texts.values())) == len(texts), f"{code} has the same text in more than one language"


@pytest.mark.parametrize("code", [CREDENTIAL_SHAPE, SCOPE_REJECTED, CREDENTIAL_MISSING])
def test_the_settings_screen_maps_each_one(code):
    """The provider can name a cause the screen has no entry for, and then the
    screen shows the raw code — which is where this started."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "provider_settings_qt.py"
    ).read_text(encoding="utf-8")

    assert f'"{code}": tr("error.{code}")' in source, f"the settings screen has no entry for {code}"
