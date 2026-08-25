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


def _is_ok_status(status: object) -> bool:
    """True only for a 200, however the API spells it (int or string)."""
    try:
        return int(status) == 200
    except (TypeError, ValueError):
        return False


def _is_quota_status(status: object) -> bool:
    try:
        return int(status) in (403, 429)
    except (TypeError, ValueError):
        return False


# The API answers with prose in the translation field when it will not
# translate: allowance spent, both languages the same, and so on.
_WARNING_MARKERS = ("MYMEMORY WARNING", "QUERY LENGTH LIMIT", "PLEASE SELECT TWO DISTINCT")


def _looks_like_a_warning(translated: str) -> bool:
    upper = translated.upper()
    return any(marker in upper for marker in _WARNING_MARKERS)


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
                # The exception text embeds the request URL, and this is a GET:
                # the message itself and the user's email are query parameters.
                # Only the class of failure is safe to record.
                reason = type(e).__name__
                logger.warning("MyMemory request failed (attempt %d/%d): %s", attempt + 1, self._retry.attempts, reason)
                if attempt < self._retry.attempts - 1:
                    self._retry.backoff(attempt)
                    continue
                return failed(text, target_lang, source_lang, f"network: {reason}", "mymemory")

            if response.status_code == 429:
                return failed(text, target_lang, source_lang, FAILURE.QUOTA, "mymemory")
            if response.status_code != 200:
                return failed(text, target_lang, source_lang, f"http_{response.status_code}", "mymemory")

            try:
                payload = response.json()
            except ValueError:
                return failed(text, target_lang, source_lang, BAD_RESPONSE, "mymemory")
            if not isinstance(payload, dict):
                # A captive portal or proxy can answer 200 with a JSON array.
                return failed(text, target_lang, source_lang, BAD_RESPONSE, "mymemory")

            # This API reports failure in the BODY with a 200 status, and puts
            # its complaint in the translation field: "MYMEMORY WARNING: YOU
            # USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY". Treating a
            # non-200 responseStatus as success renders that in the overlay and
            # — worse — caches it as the translation for seven days.
            # A status that is present and not 200 is a refusal. A status that is
            # absent is not a refusal — it is a malformed body, and it falls
            # through to the translation check below so it reports as such.
            status = payload.get("responseStatus")
            if status is not None and not _is_ok_status(status):
                if _is_quota_status(status):
                    logger.warning("MyMemory allowance exhausted (responseStatus=%s)", status)
                    return failed(text, target_lang, source_lang, FAILURE.QUOTA, "mymemory")
                logger.warning("MyMemory refused the request (responseStatus=%s)", status)
                return failed(text, target_lang, source_lang, f"api_{status}", "mymemory")

            translated = (payload.get("responseData") or {}).get("translatedText")
            if not isinstance(translated, str) or not translated.strip():
                return failed(text, target_lang, source_lang, BAD_RESPONSE, "mymemory")
            if _looks_like_a_warning(translated):
                # Belt and braces: some responses carry the warning text with a
                # 200 responseStatus.
                logger.warning("MyMemory returned a warning instead of a translation")
                return failed(text, target_lang, source_lang, FAILURE.QUOTA, "mymemory")

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
                label="provider.mymemory.email",
                placeholder="provider.mymemory.email_hint",
                secret=False,
                required=False,
            ),
        ),
        build=_build,
        validate=_validate,
        note="provider.mymemory.note",
        keyless=True,
    )
)
