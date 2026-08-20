"""GigaChat and MyMemory backends, with the network faked out.

Both are new, both are the ones a Russian player will actually hit, and both
have failure modes that only show up in conditions that are awkward to
reproduce by hand: an expired token, a spent quota, an untrusted TLS chain.
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
from app.translators.mymemory_provider import (
    MAX_QUERY_BYTES,
    NO_SOURCE_LANGUAGE,
    MyMemoryBackend,
    truncate_to_bytes,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, raises_json: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raises_json = raises_json

    def json(self):
        if self._raises_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Answers requests from a scripted queue, remembering what it was asked."""

    def __init__(self) -> None:
        self.verify = True
        self.calls: list[dict] = []
        self._script: dict[str, list] = {}

    def script(self, url: str, *responses) -> None:
        self._script[url] = list(responses)

    def _answer(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        queue = self._script.get(url)
        if not queue:
            raise AssertionError(f"unscripted {method} to {url}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, **kwargs):
        return self._answer("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._answer("GET", url, **kwargs)


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
    """The popular workaround is verify=False. It must never be what happens."""
    backend, session = gigachat
    session.script(OAUTH_URL, requests.exceptions.SSLError("certificate verify failed"))

    result = backend.translate("hello", "RU")

    assert result.error == TLS_UNTRUSTED
    assert session.verify is not False


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
    backend, session = gigachat
    session.script(
        OAUTH_URL,
        requests.exceptions.ConnectionError("failed: {'Authorization': 'Basic base64key'}"),
    )

    result = backend.translate("hello", "RU")

    assert "base64key" not in (result.error or "")
    assert "base64key" not in caplog.text


def test_validate_reports_success_without_handing_back_the_token(gigachat):
    backend, session = gigachat
    session.script(OAUTH_URL, token_response(token="super-secret-token"))

    ok, detail = backend.validate()

    assert ok is True
    assert "super-secret-token" not in detail


def test_a_supplied_root_certificate_applies_to_this_session_only():
    backend = GigaChatBackend("key", ca_bundle="/etc/ssl/russian_root.pem")

    assert backend._session.verify == "/etc/ssl/russian_root.pem"
    assert requests.Session().verify is True, "the global trust store stays untouched"


# ── MyMemory ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mymemory():
    backend = MyMemoryBackend(retry=RetryPolicy(attempts=2, delay=0))
    session = FakeSession()
    backend._session = session
    return backend, session


def memory_response(text: str = "привет", status=200) -> FakeResponse:
    return FakeResponse(200, {"responseData": {"translatedText": text}, "responseStatus": status})


def test_mymemory_translates(mymemory):
    backend, session = mymemory
    session.script(backend_endpoint(), memory_response("привет"))

    result = backend.translate("hello", "RU", "EN")

    assert result.success is True
    assert result.translated == "привет"
    assert result.backend == "mymemory"


def backend_endpoint() -> str:
    from app.translators.mymemory_provider import ENDPOINT

    return ENDPOINT


def test_an_unknown_source_language_steps_aside(mymemory):
    """The endpoint has no autodetect, and a guessed source produces confident
    nonsense — better to let the next provider have it."""
    backend, _session = mymemory

    result = backend.translate("hello", "RU", None)

    assert result.success is False
    assert result.error == NO_SOURCE_LANGUAGE
    assert result.translated == "hello"


def test_the_daily_allowance_is_reported_with_a_200_status(mymemory):
    backend, session = mymemory
    session.script(backend_endpoint(), FakeResponse(200, {"responseStatus": 403, "responseData": {}}))

    assert backend.translate("hello", "RU", "EN").error == FAILURE.QUOTA


def test_an_email_is_sent_so_the_allowance_is_the_larger_one():
    backend = MyMemoryBackend(email="player@example.com")
    session = FakeSession()
    backend._session = session
    session.script(backend_endpoint(), memory_response())

    backend.translate("hello", "RU", "EN")

    assert session.calls[0]["params"]["de"] == "player@example.com"


def test_no_email_means_no_identifying_parameter(mymemory):
    backend, session = mymemory
    session.script(backend_endpoint(), memory_response())

    backend.translate("hello", "RU", "EN")

    assert "de" not in session.calls[0]["params"]


@pytest.mark.parametrize(
    "payload",
    [{}, {"responseData": {}}, {"responseData": {"translatedText": ""}}, {"responseData": None}],
)
def test_mymemory_response_shaped_unexpectedly_does_not_crash(mymemory, payload):
    backend, session = mymemory
    session.script(backend_endpoint(), FakeResponse(200, payload))

    assert backend.translate("hello", "RU", "EN").error == "bad_response"


# ── MyMemory: the 500-byte ceiling ───────────────────────────────────────────


def test_short_text_is_sent_whole():
    assert truncate_to_bytes("hello there") == "hello there"


def test_a_long_message_is_cut_at_a_word_boundary():
    text = "word " * 200

    clipped = truncate_to_bytes(text)

    assert len(clipped.encode("utf-8")) <= MAX_QUERY_BYTES
    assert not clipped.endswith("wor"), "cutting mid-word reads as a mistranslation"


def test_the_limit_counts_bytes_not_characters():
    """Cyrillic is two bytes per character, so 300 characters already overflow."""
    text = "привет " * 60

    clipped = truncate_to_bytes(text)

    assert len(clipped.encode("utf-8")) <= MAX_QUERY_BYTES
    assert len(clipped) < len(text)


def test_one_enormous_word_is_cut_rather_than_erased():
    text = "a" * 900

    clipped = truncate_to_bytes(text)

    assert 0 < len(clipped.encode("utf-8")) <= MAX_QUERY_BYTES


def test_the_truncated_text_is_what_gets_sent(mymemory):
    backend, session = mymemory
    session.script(backend_endpoint(), memory_response())
    long_text = "word " * 200

    backend.translate(long_text, "RU", "EN")

    assert len(session.calls[0]["params"]["q"].encode("utf-8")) <= MAX_QUERY_BYTES


# ── defects the second review found ──────────────────────────────────────────


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


def test_mymemory_does_not_log_the_message_or_the_email(caplog):
    """This is a GET: the message and the email are query parameters, so the
    exception text — which embeds the URL — cannot be logged as-is."""
    backend = MyMemoryBackend(email="player@example.com", retry=RetryPolicy(attempts=1, delay=0))
    session = FakeSession()
    backend._session = session
    session.script(
        backend_endpoint(),
        requests.exceptions.ConnectionError(
            "Max retries exceeded with url: /get?q=secret+whisper&de=player%40example.com"
        ),
    )

    with caplog.at_level(logging.WARNING):
        result = backend.translate("secret whisper", "RU", "EN")

    assert "secret+whisper" not in caplog.text
    assert "player" not in caplog.text
    assert "secret" not in (result.error or "")


@pytest.mark.parametrize(
    ("status", "translated"),
    [
        (429, "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"),
        ("429", "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"),
        (403, "PLEASE SELECT TWO DISTINCT LANGUAGES"),
    ],
)
def test_an_api_complaint_is_not_passed_off_as_a_translation(mymemory, status, translated):
    """The API answers HTTP 200 and puts its complaint in the translation field.
    Accepting it renders the warning in the overlay AND caches it for a week."""
    backend, session = mymemory
    session.script(
        backend_endpoint(),
        FakeResponse(200, {"responseStatus": status, "responseData": {"translatedText": translated}}),
    )

    result = backend.translate("hello", "RU", "EN")

    assert result.success is False
    assert result.error == FAILURE.QUOTA
    assert result.translated == "hello", "the original, not the API's complaint"


def test_a_warning_with_an_ok_status_is_still_not_a_translation(mymemory):
    backend, session = mymemory
    session.script(
        backend_endpoint(),
        FakeResponse(200, {"responseStatus": 200, "responseData": {"translatedText": "MYMEMORY WARNING: quota"}}),
    )

    assert backend.translate("hello", "RU", "EN").success is False


def test_a_json_array_body_does_not_crash(mymemory):
    """A captive portal or proxy can answer 200 with something that is not an object."""
    backend, session = mymemory
    session.script(backend_endpoint(), FakeResponse(200, ["not", "an", "object"]))

    assert backend.translate("hello", "RU", "EN").error == "bad_response"


def test_a_refusal_that_is_not_a_quota_reports_the_status(mymemory):
    backend, session = mymemory
    session.script(backend_endpoint(), FakeResponse(200, {"responseStatus": 500, "responseData": {}}))

    assert backend.translate("hello", "RU", "EN").error == "api_500"
