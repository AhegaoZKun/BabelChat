"""Microsoft Translator — free tier with no card, but an Azure account first.

Quality sits below DeepL and the setup involves a portal, a resource and a
region. Worth keeping as the fallback that costs nothing to hold.
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

ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
_TIMEOUT = 10

# Microsoft spells a few codes differently from the rest of the app.
_LANG_CODES = {"ZH": "zh-Hans", "PT": "pt-br", "NB": "nb", "EN": "en"}


def _code(lang: str) -> str:
    return _LANG_CODES.get(lang.upper(), lang.lower())


def _session(api_key: str, region: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Ocp-Apim-Subscription-Key": api_key,
            "Ocp-Apim-Subscription-Region": region,
            "Content-Type": "application/json",
        }
    )
    return session


class MicrosoftBackend:
    def __init__(self, api_key: str, region: str = "", retry: RetryPolicy | None = None) -> None:
        self._session = _session(api_key, region)
        self._retry = retry or RetryPolicy()

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        params: dict = {"api-version": "3.0", "to": _code(target_lang)}
        if source_lang:
            params["from"] = _code(source_lang)

        for attempt in range(self._retry.attempts):
            try:
                response = self._session.post(ENDPOINT, params=params, json=[{"text": text}], timeout=_TIMEOUT)
                if response.status_code == 401:
                    return failed(text, target_lang, source_lang, FAILURE.AUTH, "microsoft")
                if response.status_code == 429:
                    logger.warning("Microsoft Translator rate limited")
                    if attempt < self._retry.attempts - 1:
                        self._retry.backoff(attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                detected = payload[0].get("detectedLanguage", {}).get("language", source_lang or "")
                return TranslationResult(
                    original=text,
                    translated=payload[0]["translations"][0]["text"],
                    source_lang=detected.upper(),
                    target_lang=target_lang,
                    success=True,
                    backend="microsoft",
                )
            except requests.RequestException as e:
                logger.warning("Microsoft Translator error (attempt %d/%d): %s", attempt + 1, self._retry.attempts, e)
                if attempt < self._retry.attempts - 1:
                    self._retry.backoff(attempt)
            except Exception as e:
                logger.error("Microsoft Translator unexpected error: %s", e)
                return failed(text, target_lang, source_lang, f"unexpected: {e}", "microsoft")
        return failed(text, target_lang, source_lang, FAILURE.RETRIES, "microsoft")

    def validate(self) -> tuple[bool, str]:
        try:
            response = self._session.post(
                ENDPOINT,
                params={"api-version": "3.0", "to": "en"},
                json=[{"text": "test"}],
                timeout=_TIMEOUT,
            )
        except Exception as e:
            return False, f"error: {e}"
        if response.status_code == 200:
            return True, "valid"
        if response.status_code == 401:
            return False, FAILURE.AUTH
        return False, f"http_{response.status_code}"


def _build(settings: dict[str, str]) -> MicrosoftBackend:
    return MicrosoftBackend(settings["api_key"], settings.get("region", ""))


def _validate(settings: dict[str, str]) -> tuple[bool, str]:
    key = settings.get("api_key", "").strip()
    if not key:
        return False, FAILURE.NO_KEY
    return MicrosoftBackend(key, settings.get("region", "")).validate()


SPEC = register(
    ProviderSpec(
        id="microsoft",
        display_name="Microsoft Translator",
        fields=(
            ProviderField(
                key="api_key",
                label="Microsoft Translator API key",
                placeholder="Microsoft Translator API key",
                help_url="https://portal.azure.com/",
                help_label="Azure portal",
            ),
            ProviderField(
                key="region",
                label="Azure region",
                placeholder="e.g. germanywestcentral, eastus, westeurope",
                secret=False,
                required=False,
            ),
        ),
        build=_build,
        validate=_validate,
        note="Free tier: 2M characters a month, no card required — but you need an Azure account.",
    )
)
