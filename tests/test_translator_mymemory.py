"""MyMemory: the provider a new player gets before configuring anything.

Two things make it unlike the others. It reports failure in the BODY with a
200 status, putting its complaint where the translation belongs — render
that and the overlay shows "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE
TRANSLATIONS FOR TODAY" and caches it for a week. And its limit is 500
BYTES, not characters, so a Russian message is over it at half the length
an English one is.
"""

from __future__ import annotations

import logging

import pytest
import requests

from app.translators.base import FAILURE, RetryPolicy
from app.translators.mymemory_provider import (
    MAX_QUERY_BYTES,
    NO_SOURCE_LANGUAGE,
    MyMemoryBackend,
    truncate_to_bytes,
)
from tests.translator_fakes import FakeResponse, FakeSession

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
