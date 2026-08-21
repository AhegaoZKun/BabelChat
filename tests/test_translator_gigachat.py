"""GigaChat: the token dance, the failure paths, and the credential.

It is the only provider that logs in before it can translate, so most of
what can go wrong is about the token: reusing it, renewing it before it
lapses, recovering from one that expired early, and never writing it
anywhere a support thread might end up carrying it.
"""

from __future__ import annotations

import logging
import pathlib
import time

import pytest
import requests

from app.translators.base import FAILURE, RetryPolicy
from app.translators.gigachat_provider import (
    BAD_RESPONSE,
    CERT_UNREADABLE,
    CHAT_URL,
    OAUTH_URL,
    TLS_UNTRUSTED,
    GigaChatBackend,
    _safe_error,
)
from tests.translator_fakes import FakeResponse, FakeSession


def token_response(expires_in_seconds: float = 1800, token: str = "tok-123") -> FakeResponse:
    return FakeResponse(200, {"access_token": token, "expires_at": (time.time() + expires_in_seconds) * 1000})


def chat_response(text: str = "привет") -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


@pytest.fixture
def gigachat():
    backend = GigaChatBackend("base64key", retry=RetryPolicy(attempts=2, delay=0))
    session = FakeSession()
    backend._session = session
    return backend, session


# ── GigaChat: the ordinary path ──────────────────────────────────────────────


def test_a_message_is_translated(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, chat_response("привет всем"))

    result = backend.translate("hello everyone", "RU", "EN")

    assert result.success is True
    assert result.translated == "привет всем"
    assert result.backend == "gigachat"
    assert result.source_lang == "EN"


def test_the_target_language_reaches_the_prompt(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, chat_response())

    backend.translate("hi", "RU")

    chat_call = [c for c in session.calls if c["url"] == CHAT_URL][0]
    system_prompt = chat_call["json"]["messages"][0]["content"]
    assert "Russian" in system_prompt
    assert chat_call["json"]["messages"][1]["content"] == "hi"


def test_the_token_is_reused_rather_than_refetched(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, chat_response())

    backend.translate("one", "RU")
    backend.translate("two", "RU")

    assert sum(1 for c in session.calls if c["url"] == OAUTH_URL) == 1


def test_a_token_close_to_expiry_is_renewed_before_it_lapses(gigachat):
    """Renewing early stops a request starting with a token that dies in flight."""
    backend, session = gigachat
    session.script(OAUTH_URL, token_response(expires_in_seconds=30))
    session.script(CHAT_URL, chat_response())

    backend.translate("one", "RU")
    backend.translate("two", "RU")

    assert sum(1 for c in session.calls if c["url"] == OAUTH_URL) == 2


def test_a_response_without_an_expiry_still_gets_a_finite_lifetime(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, FakeResponse(200, {"access_token": "tok"}))
    session.script(CHAT_URL, chat_response())

    backend.translate("hi", "RU")

    assert backend._token_expires_at > time.time(), "a missing expiry must not mean 'never expires'"


# ── GigaChat: failure paths ──────────────────────────────────────────────────


def test_an_expired_token_triggers_exactly_one_silent_relogin(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response(), token_response(token="tok-456"))
    session.script(CHAT_URL, FakeResponse(401), chat_response("ок"))

    result = backend.translate("hello", "RU")

    assert result.success is True
    assert result.translated == "ок"
    assert sum(1 for c in session.calls if c["url"] == OAUTH_URL) == 2


def test_a_rejected_key_gives_up_instead_of_looping(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response(), FakeResponse(401))
    session.script(CHAT_URL, FakeResponse(401))

    result = backend.translate("hello", "RU")

    assert result.success is False
    assert result.error == FAILURE.AUTH
    assert result.translated == "hello", "the original text survives a failed translation"
    assert sum(1 for c in session.calls if c["url"] == OAUTH_URL) == 2


def test_a_spent_quota_is_not_retried(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, FakeResponse(429))

    result = backend.translate("hello", "RU")

    assert result.error == FAILURE.QUOTA
    assert sum(1 for c in session.calls if c["url"] == CHAT_URL) == 1


def test_an_untrusted_tls_chain_is_reported_not_worked_around(gigachat):
    """The popular workaround is to retry with verify=False, which turns a
    certificate warning into an open door. It must never be what happens —
    including on the retry, which is where it would be tempting.

    Asserting `session.verify is not False` could not fail: the fixture sets it
    to True and this path never writes it. Watching the flag across every call
    the backend makes can."""
    backend, session = gigachat
    session.script(OAUTH_URL, requests.exceptions.SSLError("certificate verify failed"))
    session.script(OAUTH_URL, requests.exceptions.SSLError("certificate verify failed"))
    result = backend.translate("hello", "RU")

    assert result.error == TLS_UNTRUSTED
    assert session.calls, "the backend never reached the network"
    for call in session.calls:
        assert call["session_verify"] is not False, f"verification was switched off: {call}"
        assert call.get("verify", True) is not False, f"verify=False passed per call: {call}"


def test_an_unreachable_network_returns_the_original_text(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, requests.exceptions.Timeout("timed out"))

    result = backend.translate("hello everyone", "RU")

    assert result.success is False
    assert result.translated == "hello everyone"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {"content": 42}}]},
    ],
)
def test_a_response_shaped_unexpectedly_does_not_crash(gigachat, payload):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, FakeResponse(200, payload))

    result = backend.translate("hello", "RU")

    assert result.error == BAD_RESPONSE
    assert result.translated == "hello"


def test_a_body_that_is_not_json_does_not_crash(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, FakeResponse(200, raises_json=True))

    assert backend.translate("hello", "RU").error == BAD_RESPONSE


# ── GigaChat: the credential must not leak ───────────────────────────────────


def test_an_error_carrying_a_header_is_scrubbed_before_it_is_logged():
    """requests puts the prepared request in some exception reprs, and that
    request holds the Authorization header."""
    leaky = requests.RequestException("connection failed while sending headers {'Authorization': 'Bearer tok-123'}")

    scrubbed = _safe_error(leaky)

    assert "tok-123" not in scrubbed
    assert "Authorization" not in scrubbed
    assert "RequestException" in scrubbed


def test_a_network_failure_message_never_carries_the_token(gigachat, caplog):
    """Both legs, not just the login one. The chat request carries
    `Authorization: Bearer <token>` — the more valuable of the two secrets — and
    it was the leg nothing covered."""
    backend, session = gigachat
    session.script(
        OAUTH_URL,
        requests.exceptions.ConnectionError("failed: {'Authorization': 'Basic base64key'}"),
    )

    with caplog.at_level(logging.DEBUG):
        result = backend.translate("hello", "RU")

    assert "base64key" not in (result.error or "")
    assert "base64key" not in caplog.text

    caplog.clear()
    session.script(OAUTH_URL, token_response(token="bearer-secret-123"))
    session.script(
        CHAT_URL,
        requests.exceptions.ConnectionError("failed: {'Authorization': 'Bearer bearer-secret-123'}"),
    )

    with caplog.at_level(logging.DEBUG):
        result = backend.translate("hello", "RU")

    assert "bearer-secret-123" not in (result.error or "")
    assert "bearer-secret-123" not in caplog.text


def test_validate_reports_success_without_handing_back_the_token(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response(token="super-secret-token"))

    ok, detail = backend.validate()

    assert ok is True
    assert "super-secret-token" not in detail


def test_a_supplied_root_certificate_applies_to_this_session_only():
    """The Russian root CA has to be trusted for this provider and nothing else.

    Asserting `requests.Session().verify is True` was a property of the requests
    library, not of BabelChat. A second BabelChat backend is the comparison that
    means something."""
    import requests

    from app.translators.gigachat_provider import bundled_root_certificate

    with_bundle = GigaChatBackend("key", ca_bundle="/etc/ssl/russian_root.pem")
    without_bundle = GigaChatBackend("key")

    assert with_bundle._session.verify == "/etc/ssl/russian_root.pem"
    # An unconfigured backend now falls back to the root shipped with the app —
    # requests does not read the Windows certificate store, so without it this
    # provider cannot connect at all.
    assert without_bundle._session.verify == bundled_root_certificate()
    assert with_bundle._session is not without_bundle._session
    # And none of it reaches anyone else's session.
    assert requests.Session().verify is True, "trust was widened process-wide"


def test_an_unreadable_certificate_path_does_not_escape_the_provider():
    """requests raises a bare OSError for a CA bundle it cannot read — not a
    RequestException. Unguarded it left the provider and killed the translation
    thread, which is what a certificate on an unplugged drive produces."""
    unreadable = str(pathlib.Path("Z:/definitely/not/here.pem"))
    backend = GigaChatBackend("key", ca_bundle=unreadable, retry=RetryPolicy(attempts=1, delay=0))

    result = backend.translate("hello", "RU")

    assert result.success is False
    assert result.error == CERT_UNREADABLE
    assert result.translated == "hello"


def test_a_repeated_server_error_reports_itself_not_max_retries(gigachat):
    """`max_retries_exceeded` discards the http_503 that caused it, and that is
    the only thing anyone reading the log can act on."""
    backend, session = gigachat
    session.script(OAUTH_URL, token_response())
    session.script(CHAT_URL, FakeResponse(503), FakeResponse(503))

    result = backend.translate("hello", "RU")

    assert result.error == "http_503"


@pytest.mark.parametrize(
    "leaky",
    [
        "connection failed while sending headers {'Authorization': 'Bearer tok-abc123'}",
        "upstream rejected: Bearer tok-abc123",
        "proxy denied Basic base64key",
        "retry with authorization: bearer tok-abc123",
    ],
    ids=["header_name", "bearer_value_only", "basic_value_only", "lower_case"],
)
def test_the_scrub_fires_on_the_credential_itself_not_only_on_the_header_name(leaky):
    """Every leak fixture in this file happened to contain the literal string
    "Authorization", so the Bearer and Basic markers were never the reason the
    scrub fired — reducing the marker list to just the header name left the file
    green. A real leak can carry the value without the header name."""
    scrubbed = _safe_error(requests.RequestException(leaky))

    assert "tok-abc123" not in scrubbed
    assert "base64key" not in scrubbed
    assert "withheld" in scrubbed
