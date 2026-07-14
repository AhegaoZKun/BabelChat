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
    "BG", "CS", "DA", "DE", "EL", "EN", "ES", "ET", "FI", "FR",
    "HU", "ID", "IT", "JA", "KO", "LT", "LV", "NB", "NL", "PL",
    "PT", "RO", "RU", "SK", "SL", "SV", "TR", "UK", "ZH",
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

TranslatorBackend = Literal["deepl", "microsoft", "google", "argos", "auto"]


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
                    original=text, translated=result.text,
                    source_lang=result.detected_source_lang,
                    target_lang=target_lang, success=True, backend="deepl",
                )
            except deepl.QuotaExceededException:
                logger.error("DeepL quota exceeded")
                return TranslationResult(
                    original=text, translated=text,
                    source_lang=source_lang or "", target_lang=target_lang,
                    success=False, error="quota_exceeded", backend="deepl",
                )
            except deepl.DeepLException as e:
                logger.warning("DeepL error (attempt %d/%d): %s", attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2 ** attempt))
            except Exception as e:
                logger.error("DeepL unexpected error: %s", e)
                return TranslationResult(
                    original=text, translated=text,
                    source_lang=source_lang or "", target_lang=target_lang,
                    success=False, error=f"unexpected: {e}", backend="deepl",
                )
        return TranslationResult(
            original=text, translated=text,
            source_lang=source_lang or "", target_lang=target_lang,
            success=False, error="max_retries_exceeded", backend="deepl",
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
        self._session.headers.update({
            "Ocp-Apim-Subscription-Key": api_key,
            "Ocp-Apim-Subscription-Region": region,
            "Content-Type": "application/json",
        })

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
                        original=text, translated=text,
                        source_lang=source_lang or "", target_lang=target_lang,
                        success=False, error="auth_failed", backend="microsoft",
                    )
                if resp.status_code == 429:
                    logger.warning("Microsoft Translator rate limited")
                    if attempt < self._max_retries - 1:
                        time.sleep(self._retry_delay * (2 ** attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                translated = data[0]["translations"][0]["text"]
                detected = data[0].get("detectedLanguage", {}).get("language", source_lang or "")
                return TranslationResult(
                    original=text, translated=translated,
                    source_lang=detected.upper(), target_lang=target_lang,
                    success=True, backend="microsoft",
                )
            except requests.RequestException as e:
                logger.warning("Microsoft Translator error (attempt %d/%d): %s", attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2 ** attempt))
            except Exception as e:
                logger.error("Microsoft Translator unexpected error: %s", e)
                return TranslationResult(
                    original=text, translated=text,
                    source_lang=source_lang or "", target_lang=target_lang,
                    success=False, error=f"unexpected: {e}", backend="microsoft",
                )

        return TranslationResult(
            original=text, translated=text,
            source_lang=source_lang or "", target_lang=target_lang,
            success=False, error="max_retries_exceeded", backend="microsoft",
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


# ── Google Cloud Translation backend (v2 REST, API key) ──────────────────────

_GOOGLE_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"


class _GoogleBackend:
    def __init__(self, api_key: str, max_retries: int = 3, retry_delay: float = 1.0) -> None:
        self._api_key = api_key
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._session = requests.Session()

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        payload: dict = {
            "q": text,
            "target": target_lang.lower(),
            "format": "text",
            "key": self._api_key,
        }
        if source_lang:
            payload["source"] = source_lang.lower()

        for attempt in range(self._max_retries):
            try:
                resp = self._session.post(_GOOGLE_ENDPOINT, data=payload, timeout=10)
                if resp.status_code in (401, 403):
                    return TranslationResult(
                        original=text, translated=text,
                        source_lang=source_lang or "", target_lang=target_lang,
                        success=False, error="auth_failed", backend="google",
                    )
                if resp.status_code == 429:
                    logger.warning("Google Translate rate limited")
                    if attempt < self._max_retries - 1:
                        time.sleep(self._retry_delay * (2 ** attempt))
                    continue
                resp.raise_for_status()
                tr = resp.json()["data"]["translations"][0]
                detected = tr.get("detectedSourceLanguage", source_lang or "")
                return TranslationResult(
                    original=text, translated=tr["translatedText"],
                    source_lang=(detected or "").upper(), target_lang=target_lang,
                    success=True, backend="google",
                )
            except requests.RequestException as e:
                logger.warning("Google Translate error (attempt %d/%d): %s", attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2 ** attempt))
            except (KeyError, IndexError, ValueError) as e:
                return TranslationResult(
                    original=text, translated=text,
                    source_lang=source_lang or "", target_lang=target_lang,
                    success=False, error=f"unexpected: {e}", backend="google",
                )
        return TranslationResult(
            original=text, translated=text,
            source_lang=source_lang or "", target_lang=target_lang,
            success=False, error="max_retries_exceeded", backend="google",
        )

    def validate(self) -> bool:
        try:
            resp = self._session.post(
                _GOOGLE_ENDPOINT,
                data={"q": "test", "target": "en", "format": "text", "key": self._api_key},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


# ── Argos Translate backend (fully local, no key) ────────────────────────────

class _ArgosBackend:
    """Offline translation via argostranslate (optional dependency).

    Language-pair models (~100-300MB each) are downloaded and installed
    automatically on first use of a pair, then cached locally forever.
    """

    def __init__(self) -> None:
        # Import lazily so the app runs without argostranslate installed.
        import argostranslate.translate  # noqa: F401  (raises ImportError if absent)
        self._installed_pairs: set[tuple[str, str]] = set()
        self._failed_pairs: set[tuple[str, str]] = set()
        self._index_updated = False

    def _ensure_pair(self, src: str, dst: str) -> bool:
        import argostranslate.package
        import argostranslate.translate

        pair = (src, dst)
        if pair in self._installed_pairs:
            return True
        if pair in self._failed_pairs:
            return False
        # Already installed on disk?
        for lang in argostranslate.translate.get_installed_languages():
            if lang.code == src and any(t.to_lang.code == dst for t in getattr(lang, "translations_from", [])):
                self._installed_pairs.add(pair)
                return True
        try:
            if not self._index_updated:
                argostranslate.package.update_package_index()
                self._index_updated = True
            available = argostranslate.package.get_available_packages()
            pkg = next((p for p in available if p.from_code == src and p.to_code == dst), None)
            if pkg is None:
                self._failed_pairs.add(pair)
                return False
            logger.info("Argos: downloading model %s→%s (first use)…", src, dst)
            argostranslate.package.install_from_path(pkg.download())
            self._installed_pairs.add(pair)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Argos model install failed for %s→%s: %s", src, dst, e)
            self._failed_pairs.add(pair)
            return False

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        import argostranslate.translate

        src = (source_lang or "en").lower()[:2]
        dst = target_lang.lower()[:2]
        if src == dst:
            return TranslationResult(
                original=text, translated=text,
                source_lang=src.upper(), target_lang=target_lang,
                success=True, backend="argos",
            )
        if not self._ensure_pair(src, dst):
            return TranslationResult(
                original=text, translated=text,
                source_lang=source_lang or "", target_lang=target_lang,
                success=False, error="argos_pair_unavailable", backend="argos",
            )
        try:
            translated = argostranslate.translate.translate(text, src, dst)
            return TranslationResult(
                original=text, translated=translated,
                source_lang=src.upper(), target_lang=target_lang,
                success=True, backend="argos",
            )
        except Exception as e:  # noqa: BLE001
            return TranslationResult(
                original=text, translated=text,
                source_lang=source_lang or "", target_lang=target_lang,
                success=False, error=f"unexpected: {e}", backend="argos",
            )

    def validate(self) -> bool:
        return True  # local; presence of the module is the requirement


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
        google_api_key: str = "",
        argos_enabled: bool = False,
        priority: TranslatorBackend = "deepl",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._deepl: _DeepLBackend | None = None
        self._microsoft: _MicrosoftBackend | None = None
        self._google: _GoogleBackend | None = None
        self._argos: _ArgosBackend | None = None
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

        if google_api_key:
            try:
                self._google = _GoogleBackend(google_api_key, max_retries, retry_delay)
            except Exception as e:
                logger.warning("Failed to initialize Google Translate backend: %s", e)

        if argos_enabled:
            try:
                self._argos = _ArgosBackend()
            except ImportError:
                logger.warning("Argos enabled but argostranslate is not installed (pip install argostranslate)")
            except Exception as e:
                logger.warning("Failed to initialize Argos backend: %s", e)

        if not self.has_backend:
            logger.warning("No translation backend configured")

    @property
    def has_backend(self) -> bool:
        return any((self._deepl, self._microsoft, self._google, self._argos))

    def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        if not text.strip():
            return TranslationResult(
                original=text, translated=text,
                source_lang=source_lang or "", target_lang=target_lang,
                success=True,
            )

        chain = self._get_backends()

        if not chain:
            return TranslationResult(
                original=text, translated=text,
                source_lang=source_lang or "", target_lang=target_lang,
                success=False, error="no_backend",
            )

        result: TranslationResult | None = None
        for backend in chain:
            result = backend.translate(text, target_lang, source_lang)
            if result.success:
                return result
            logger.info("Backend %s failed (%s), trying next", result.backend, result.error)
        return result  # last failure

    def get_usage(self) -> deepl.Usage | None:
        """Get DeepL usage stats if available."""
        if self._deepl:
            return self._deepl.get_usage()
        return None

    def _get_backends(self) -> list:
        """All configured backends, priority first, Argos always last resort."""
        order = {
            "deepl": self._deepl,
            "microsoft": self._microsoft,
            "google": self._google,
            "argos": self._argos,
        }
        chain = []
        primary = order.pop(self._priority, None)
        if primary is not None:
            chain.append(primary)
        # remaining online backends, then argos as last resort
        argos = order.pop("argos", None)
        chain.extend(b for b in order.values() if b is not None)
        if argos is not None:
            chain.append(argos)
        return chain


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


def validate_google_key(api_key: str) -> tuple[bool, str]:
    """Validate a Google Cloud Translation API key."""
    if not api_key:
        return False, "no_key"
    try:
        resp = requests.post(
            _GOOGLE_ENDPOINT,
            data={"q": "test", "target": "en", "format": "text", "key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "valid"
        if resp.status_code in (401, 403):
            return False, "auth_failed"
        return False, f"http_{resp.status_code}"
    except requests.RequestException as e:
        return False, str(e)


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
