"""Translation service — supports DeepL and Microsoft Translator."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import deepl
import requests

logger = logging.getLogger(__name__)

# ── Language maps ─────────────────────────────────────────────────────────────

DEEPL_LANGUAGES = {
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

# Microsoft uses slightly different codes for some languages
_MS_LANG_MAP: dict[str, str] = {
    "ZH": "zh-Hans",
    "PT": "pt-br",
    "NB": "nb",
    "EN": "en",
}

_EN_TARGET_DEFAULT = "EN-US"
_PT_TARGET_DEFAULT = "PT-BR"

TranslatorBackend = Literal["deepl", "microsoft", "auto"]


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """Result of a translation attempt."""

    original: str
    translated: str
    source_lang: str
    target_lang: str
    success: bool
    error: str | None = None
    backend: str = ""


# ── DeepL backend ─────────────────────────────────────────────────────────────


class _DeepLBackend:
    def __init__(self, api_key: str, max_retries: int = 3, retry_delay: float = 1.0) -> None:
        self._client = deepl.Translator(api_key)
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        effective_target = _normalize_deepl_target(target_lang)
        for attempt in range(self._max_retries):
            try:
                result = self._client.translate_text(
                    text,
                    target_lang=effective_target,
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
                logger.error("DeepL quota exceeded")
                return TranslationResult(
                    original=text,
                    translated=text,
                    source_lang=source_lang or "",
                    target_lang=target_lang,
                    success=False,
                    error="quota_exceeded",
                    backend="deepl",
                )
            except deepl.DeepLException as e:
                logger.warning("DeepL error (attempt %d/%d): %s", attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2**attempt))
            except Exception as e:
                logger.error("DeepL unexpected error: %s", e)
                return TranslationResult(
                    original=text,
                    translated=text,
                    source_lang=source_lang or "",
                    target_lang=target_lang,
                    success=False,
                    error=f"unexpected: {e}",
                    backend="deepl",
                )
        return TranslationResult(
            original=text,
            translated=text,
            source_lang=source_lang or "",
            target_lang=target_lang,
            success=False,
            error="max_retries_exceeded",
            backend="deepl",
        )

    def get_usage(self) -> deepl.Usage:
        return self._client.get_usage()

    def validate(self) -> bool:
        try:
            self._client.get_usage()
            return True
        except Exception:
            return False


# ── Microsoft Translator backend ──────────────────────────────────────────────

_MS_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
_MS_DETECT_ENDPOINT = "https://api.cognitive.microsofttranslator.com/detect"


class _MicrosoftBackend:
    def __init__(self, api_key: str, region: str = "", max_retries: int = 3, retry_delay: float = 1.0) -> None:
        self._api_key = api_key
        self._region = region
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Ocp-Apim-Subscription-Key": api_key,
                "Ocp-Apim-Subscription-Region": region,
                "Content-Type": "application/json",
            }
        )

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        ms_target = _MS_LANG_MAP.get(target_lang.upper(), target_lang.lower())
        params: dict = {"api-version": "3.0", "to": ms_target}
        if source_lang:
            ms_source = _MS_LANG_MAP.get(source_lang.upper(), source_lang.lower())
            params["from"] = ms_source

        body = [{"text": text}]

        for attempt in range(self._max_retries):
            try:
                resp = self._session.post(_MS_ENDPOINT, params=params, json=body, timeout=10)
                if resp.status_code == 401:
                    return TranslationResult(
                        original=text,
                        translated=text,
                        source_lang=source_lang or "",
                        target_lang=target_lang,
                        success=False,
                        error="auth_failed",
                        backend="microsoft",
                    )
                if resp.status_code == 429:
                    logger.warning("Microsoft Translator rate limited")
                    if attempt < self._max_retries - 1:
                        time.sleep(self._retry_delay * (2**attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                translated = data[0]["translations"][0]["text"]
                detected = data[0].get("detectedLanguage", {}).get("language", source_lang or "")
                return TranslationResult(
                    original=text,
                    translated=translated,
                    source_lang=detected.upper(),
                    target_lang=target_lang,
                    success=True,
                    backend="microsoft",
                )
            except requests.RequestException as e:
                logger.warning("Microsoft Translator error (attempt %d/%d): %s", attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2**attempt))
            except Exception as e:
                logger.error("Microsoft Translator unexpected error: %s", e)
                return TranslationResult(
                    original=text,
                    translated=text,
                    source_lang=source_lang or "",
                    target_lang=target_lang,
                    success=False,
                    error=f"unexpected: {e}",
                    backend="microsoft",
                )

        return TranslationResult(
            original=text,
            translated=text,
            source_lang=source_lang or "",
            target_lang=target_lang,
            success=False,
            error="max_retries_exceeded",
            backend="microsoft",
        )

    def validate(self) -> bool:
        try:
            resp = self._session.post(
                _MS_ENDPOINT,
                params={"api-version": "3.0", "to": "en"},
                json=[{"text": "test"}],
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ── Unified TranslatorService ─────────────────────────────────────────────────


class TranslatorService:
    """Unified translation service supporting DeepL and Microsoft Translator.

    If both keys are configured, uses `priority` to choose the primary backend.
    Falls back to the other backend if the primary returns an error.
    If only one key is configured, uses that backend exclusively.
    """

    def __init__(
        self,
        api_key: str = "",
        microsoft_api_key: str = "",
        microsoft_region: str = "",
        priority: TranslatorBackend = "deepl",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._deepl: _DeepLBackend | None = None
        self._microsoft: _MicrosoftBackend | None = None
        self._priority = priority

        if api_key:
            try:
                self._deepl = _DeepLBackend(api_key, max_retries, retry_delay)
            except Exception as e:
                logger.warning("Failed to initialize DeepL backend: %s", e)

        if microsoft_api_key:
            try:
                self._microsoft = _MicrosoftBackend(microsoft_api_key, microsoft_region, max_retries, retry_delay)
            except Exception as e:
                logger.warning("Failed to initialize Microsoft Translator backend: %s", e)

        if not self._deepl and not self._microsoft:
            logger.warning("No translation backend configured")

    @property
    def has_backend(self) -> bool:
        return self._deepl is not None or self._microsoft is not None

    def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        if not text.strip():
            return TranslationResult(
                original=text,
                translated=text,
                source_lang=source_lang or "",
                target_lang=target_lang,
                success=True,
            )

        primary, fallback = self._get_backends()

        if primary is None:
            return TranslationResult(
                original=text,
                translated=text,
                source_lang=source_lang or "",
                target_lang=target_lang,
                success=False,
                error="no_backend",
            )

        result = primary.translate(text, target_lang, source_lang)

        # Fall back to secondary if primary failed and secondary is available
        if not result.success and fallback is not None:
            logger.info("Primary backend failed (%s), trying fallback", result.error)
            result = fallback.translate(text, target_lang, source_lang)

        return result

    def get_usage(self) -> deepl.Usage | None:
        """Get DeepL usage stats if available."""
        if self._deepl:
            return self._deepl.get_usage()
        return None

    def _get_backends(
        self,
    ) -> tuple[_DeepLBackend | _MicrosoftBackend | None, _DeepLBackend | _MicrosoftBackend | None]:
        """Return (primary, fallback) backends based on priority and availability."""
        if self._deepl and self._microsoft:
            if self._priority == "microsoft":
                return self._microsoft, self._deepl
            return self._deepl, self._microsoft
        if self._deepl:
            return self._deepl, None
        if self._microsoft:
            return self._microsoft, None
        return None, None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_deepl_target(lang: str) -> str:
    upper = lang.upper()
    if upper == "EN":
        return _EN_TARGET_DEFAULT
    if upper == "PT":
        return _PT_TARGET_DEFAULT
    return upper


def validate_deepl_key(api_key: str) -> tuple[bool, str]:
    """Validate a DeepL API key. Returns (valid, message)."""
    if not api_key:
        return False, "no_key"
    try:
        client = deepl.Translator(api_key)
        usage = client.get_usage()
        if usage.character and usage.character.valid:
            count = usage.character.count
            limit = usage.character.limit
            pct = int((count / limit) * 100) if limit else 0
            return True, f"{count:,}/{limit:,} ({pct}%)"
        return True, "valid"
    except deepl.AuthorizationException:
        return False, "auth_failed"
    except Exception as e:
        return False, f"error: {e}"


def validate_microsoft_key(api_key: str, region: str = "") -> tuple[bool, str]:
    """Validate a Microsoft Translator API key. Returns (valid, message)."""
    if not api_key:
        return False, "no_key"
    try:
        resp = requests.post(
            _MS_ENDPOINT,
            params={"api-version": "3.0", "to": "en"},
            json=[{"text": "test"}],
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "Ocp-Apim-Subscription-Region": region,
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "valid"
        if resp.status_code == 401:
            return False, "auth_failed"
        return False, f"http_{resp.status_code}"
    except Exception as e:
        return False, f"error: {e}"
