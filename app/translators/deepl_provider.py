"""DeepL — best quality of the three, and the hardest to sign up for.

The free tier asks for a card to verify identity and never charges, which is a
real barrier for players in regions where foreign cards do not work. That
belongs in the note the settings screen shows, not in a support thread.
"""

from __future__ import annotations

import logging

import deepl

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

# DeepL wants a regional variant for these two; a bare code is rejected.
_TARGET_VARIANTS = {"EN": "EN-US", "PT": "PT-BR"}

SUPPORTED_LANGUAGES = frozenset(
    {
        "BG",
        "CS",
        "DA",
        "DE",
        "EL",
        "EN",
        "ES",
        "ET",
        "FI",
        "FR",
        "HU",
        "ID",
        "IT",
        "JA",
        "KO",
        "LT",
        "LV",
        "NB",
        "NL",
        "PL",
        "PT",
        "RO",
        "RU",
        "SK",
        "SL",
        "SV",
        "TR",
        "UK",
        "ZH",
    }
)


def normalize_target(lang: str) -> str:
    upper = lang.upper()
    return _TARGET_VARIANTS.get(upper, upper)


class DeepLBackend:
    def __init__(self, api_key: str, retry: RetryPolicy | None = None) -> None:
        self._client = deepl.Translator(api_key)
        self._retry = retry or RetryPolicy()

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        target = normalize_target(target_lang)
        for attempt in range(self._retry.attempts):
            try:
                result = self._client.translate_text(
                    text,
                    target_lang=target,
                    source_lang=source_lang,
                    context="World of Warcraft chat",
                )
                return TranslationResult(
                    original=text,
                    translated=result.text,
                    source_lang=result.detected_source_lang,
                    target_lang=target_lang,
                    success=True,
                    backend="deepl",
                )
            except deepl.QuotaExceededException:
                # Retrying a spent quota only burns time — the answer will not
                # change until the month rolls over.
                logger.error("DeepL quota exceeded")
                return failed(text, target_lang, source_lang, FAILURE.QUOTA, "deepl")
            except deepl.AuthorizationException:
                logger.error("DeepL rejected the API key")
                return failed(text, target_lang, source_lang, FAILURE.AUTH, "deepl")
            except deepl.DeepLException as e:
                logger.warning("DeepL error (attempt %d/%d): %s", attempt + 1, self._retry.attempts, e)
                if attempt < self._retry.attempts - 1:
                    self._retry.backoff(attempt)
            except Exception as e:
                logger.error("DeepL unexpected error: %s", e)
                return failed(text, target_lang, source_lang, f"unexpected: {e}", "deepl")
        return failed(text, target_lang, source_lang, FAILURE.RETRIES, "deepl")

    def get_usage(self):
        return self._client.get_usage()

    def validate(self) -> tuple[bool, str]:
        try:
            usage = self._client.get_usage()
        except deepl.AuthorizationException:
            return False, FAILURE.AUTH
        except Exception as e:
            return False, f"error: {e}"
        if usage.character and usage.character.valid:
            count, limit = usage.character.count, usage.character.limit
            percent = int((count / limit) * 100) if limit else 0
            return True, f"{count:,}/{limit:,} ({percent}%)"
        return True, "valid"


def _build(settings: dict[str, str]) -> DeepLBackend:
    return DeepLBackend(settings["api_key"])


def _validate(settings: dict[str, str]) -> tuple[bool, str]:
    key = settings.get("api_key", "").strip()
    if not key:
        return False, FAILURE.NO_KEY
    try:
        return DeepLBackend(key).validate()
    except Exception as e:
        return False, f"error: {e}"


SPEC = register(
    ProviderSpec(
        id="deepl",
        display_name="DeepL",
        fields=(
            ProviderField(
                key="api_key",
                label="DeepL API key",
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx",
                help_url="https://www.deepl.com/your-account/keys",
                help_label="Get key",
            ),
        ),
        build=_build,
        validate=_validate,
        note="Free tier: 500K characters a month. Sign-up asks for a card to verify identity and never charges it.",
    )
)
