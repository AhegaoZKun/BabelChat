"""MyMemory — the fallback that needs no account at all.

Every other provider needs a sign-up of some kind. This one needs nothing, so
it is what a player gets on the very first launch before configuring anything.
Quality is mixed — it blends machine translation with a crowd-sourced memory —
which is why it sits below the others in the chain rather than in front of them.

Two constraints shape the code: the endpoint takes at most 500 bytes of text per
call, and it needs an explicit source language, so an undetected language is a
reason to step aside rather than to guess.
"""

from __future__ import annotations

import logging

import requests

from app.translators.base import (
    FAILURE,
    ProviderField,
    ProviderSpec,
    RetryPolicy,
    TranslationResult,
    failed,
    register,
)

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.mymemory.translated.net/get"
_TIMEOUT = 10

# The endpoint's documented per-request ceiling, counted in bytes rather than
# characters — Cyrillic costs two bytes each, so a 300-character Russian message
# is already over it.
MAX_QUERY_BYTES = 500

NO_SOURCE_LANGUAGE = "source_language_unknown"
BAD_RESPONSE = "bad_response"


def truncate_to_bytes(text: str, limit: int = MAX_QUERY_BYTES) -> str:
    """Trim to `limit` bytes of UTF-8, preferring a word boundary.

    Cutting mid-word gives a translation of a fragment that reads as a mistake
    rather than as a truncation, so the last whitespace wins unless that would
    throw away most of the message.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text

    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    spaced = clipped.rsplit(" ", 1)[0]
    # Only honour the word boundary if it keeps most of what fitted; a very long
    # single token would otherwise reduce the message to nothing.
    if len(spaced) >= len(clipped) * 0.6:
        return spaced.rstrip()
    return clipped.rstrip()


class MyMemoryBackend:
    def __init__(self, email: str = "", retry: RetryPolicy | None = None) -> None:
        self._email = email.strip()
        self._retry = retry or RetryPolicy(attempts=2)
        self._session = requests.Session()

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        if not source_lang:
            # The endpoint has no autodetect. Guessing a source language here
            # would produce confident nonsense; stepping aside lets the chain
            # try a provider that can detect.
            return failed(text, target_lang, source_lang, NO_SOURCE_LANGUAGE, "mymemory")

        query = truncate_to_bytes(text)
        params = {
            "q": query,
            "langpair": f"{source_lang.lower()}|{target_lang.lower()}",
        }
        if self._email:
            # Identifying an email raises the daily allowance tenfold.
            params["de"] = self._email

        for attempt in range(self._retry.attempts):
            try:
                response = self._session.get(ENDPOINT, params=params, timeout=_TIMEOUT)
            except requests.RequestException as e:
                logger.warning("MyMemory error (attempt %d/%d): %s", attempt + 1, self._retry.attempts, e)
                if attempt < self._retry.attempts - 1:
                    self._retry.backoff(attempt)
                    continue
                return failed(text, target_lang, source_lang, f"network: {e}", "mymemory")

            if response.status_code == 429:
                return failed(text, target_lang, source_lang, FAILURE.QUOTA, "mymemory")
            if response.status_code != 200:
                return failed(text, target_lang, source_lang, f"http_{response.status_code}", "mymemory")

            try:
                payload = response.json()
            except ValueError:
                return failed(text, target_lang, source_lang, BAD_RESPONSE, "mymemory")

            # The daily allowance is reported in the body with a 200 status.
            status = payload.get("responseStatus")
            if status in (403, "403"):
                logger.warning("MyMemory daily allowance exhausted")
                return failed(text, target_lang, source_lang, FAILURE.QUOTA, "mymemory")

            translated = (payload.get("responseData") or {}).get("translatedText")
            if not isinstance(translated, str) or not translated.strip():
                return failed(text, target_lang, source_lang, BAD_RESPONSE, "mymemory")

            return TranslationResult(
                original=text,
                translated=translated.strip(),
                source_lang=source_lang.upper(),
                target_lang=target_lang,
                success=True,
                backend="mymemory",
            )

        return failed(text, target_lang, source_lang, FAILURE.RETRIES, "mymemory")

    def validate(self) -> tuple[bool, str]:
        result = self.translate("hello", "RU", "EN")
        if result.success:
            allowance = "50,000 words a day" if self._email else "5,000 words a day"
            return True, f"valid — {allowance}"
        return False, result.error or "unknown"


def _build(settings: dict[str, str]) -> MyMemoryBackend:
    return MyMemoryBackend(email=settings.get("email", ""))


def _validate(settings: dict[str, str]) -> tuple[bool, str]:
    return _build(settings).validate()


SPEC = register(
    ProviderSpec(
        id="mymemory",
        display_name="MyMemory",
        fields=(
            ProviderField(
                key="email",
                label="Email (optional)",
                placeholder="raises the daily allowance from 5,000 to 50,000 words",
                secret=False,
                required=False,
            ),
        ),
        build=_build,
        validate=_validate,
        note="Free, no account needed. Quality is below the others, so it is used as a fallback.",
        keyless=True,
    )
)
