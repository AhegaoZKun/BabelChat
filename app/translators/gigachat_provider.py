"""GigaChat (Sber) — the provider that works from Russia without a card.

DeepL asks for a card and Microsoft asks for an Azure account; both are behind a
barrier for the players this project mostly serves. GigaChat's Freemium tier
gives an individual a million tokens a year against a Sber ID, and it is
reachable without a VPN.

Two things about it are unusual and both are handled here rather than left to
the user:

* it is an LLM, not a translation endpoint, so the request is a chat completion
  with a system prompt that pins it to translating and nothing else;
* its TLS chain is signed by the Russian Ministry of Digital Development root,
  which is not in the public trust stores. The widely-copied answer to that is
  `verify=False`, which disables verification for every request on the session
  and hands the long-lived credential to anyone in the middle. Instead the root
  is supplied as an explicit bundle, for this session only.
"""

from __future__ import annotations

import logging
import time
import uuid

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
from app.translators.gigachat_credential import (
    CERT_UNREADABLE,
    authorization_key,
    bundled_root_certificate,
    describe_certificate,
)

logger = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

DEFAULT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_MODEL = "GigaChat"
_TIMEOUT = 15

# Tokens last 30 minutes. Renew a minute early so a request never starts with a
# token that expires while it is in flight.
_RENEW_MARGIN_SECONDS = 60
_DOCUMENTED_LIFETIME_SECONDS = 1800

# Failure reasons specific to this provider, alongside the shared ones.
TLS_UNTRUSTED = "tls_untrusted"
BAD_RESPONSE = "bad_response"
# The OAuth endpoint answers 400 for two quite different mistakes, and the bare
# status told the user neither. Both are theirs to fix, so both get a name.
CREDENTIAL_SHAPE = "credential_shape"
SCOPE_REJECTED = "scope_rejected"
CREDENTIAL_MISSING = "credential_missing"
#: The model answered, but with a refusal rather than a translation.
REFUSED = "provider_refused"

#: What GigaChat says when it will not translate something. It answers 200 with
#: a paragraph about itself, and that paragraph went into the overlay as if it
#: were the translation of a one-word message. Matching on the wording alone
#: would catch a genuine translation of a sentence about language models, so
#: length is part of the test: a refusal is a canned paragraph, and it dwarfs
#: the chat line that provoked it.
_REFUSAL_MARKERS = (
    "как любая языковая модель",
    "как и любая языковая модель",
    "языковая модель",
    "не обладает собственным мнением",
    "разговоры на некоторые темы",
    "не могу ответить",
    "не могу обсуждать",
    "давайте сменим тему",
    "as a language model",
    "i can't help with that",
    "i cannot assist",
)

#: A reply this many times longer than the message is not a translation of it.
_REFUSAL_LENGTH_RATIO = 3
_REFUSAL_MIN_EXTRA = 40


def looks_like_a_refusal(source: str, reply: str) -> bool:
    """Did the model decline instead of translating?

    Both halves are needed. The wording alone would flag a real translation of
    a message that happens to mention language models; the length alone would
    flag a terse word rendered as a long polite sentence.
    """
    lowered = reply.lower()
    if not any(marker in lowered for marker in _REFUSAL_MARKERS):
        return False
    return len(reply) > len(source) * _REFUSAL_LENGTH_RATIO + _REFUSAL_MIN_EXTRA

_SYSTEM_PROMPT = (
    "You translate World of Warcraft chat messages into {target}. "
    "Reply with the translation only — no explanations, no quotes, no notes. "
    "Keep player names, item links, numbers and abbreviations such as LFM, M+, "
    "HC and BiS unchanged. If the message is already in {target}, repeat it "
    "unchanged."
)

# Language codes the app uses, spelled the way a prompt reads best.
_LANGUAGE_NAMES = {
    "RU": "Russian",
    "EN": "English",
    "ES": "Spanish",
    "DE": "German",
    "FR": "French",
    "IT": "Italian",
    "PT": "Portuguese",
    "PL": "Polish",
    "TR": "Turkish",
    "ZH": "Chinese",
    "KO": "Korean",
    "JA": "Japanese",
    "UK": "Ukrainian",
}


def language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code.upper(), code.upper())


def _safe_error(error: Exception) -> str:
    """An exception's text, unless that text carries a credential.

    requests embeds the prepared request in some exception reprs, and that
    request holds the Authorization header. No error path may become the route
    by which a key reaches a log file.
    """
    text = str(error)
    # Case-insensitively: HTTP header names are, and libraries normalise them
    # differently — urllib3 lower-cases some of what it echoes back, so a
    # case-sensitive check let "authorization: bearer …" through untouched.
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "basic ")):
        return f"{type(error).__name__} (details withheld — they contained credentials)"
    return text or type(error).__name__


#: What the OAuth endpoint puts in `code` when it refuses the request. Both of
#: these arrive as a bare 400, and they are not remotely the same problem:
#: one is a value in the wrong field, the other a project created on the wrong
#: tariff.
_REJECTION_CODES = {
    4: CREDENTIAL_SHAPE,  # "Can't decode 'Authorization' header"
    7: SCOPE_REJECTED,  # "scope from db not fully includes consumed scope"
}


def _why_rejected(response: requests.Response) -> str:
    """Which of the two 400s this is, read from the body rather than guessed.

    Falls back to the status when the body is not the documented shape — a
    proxy's HTML error page is a 400 too, and calling that a malformed
    credential would send the reader after the wrong thing.
    """
    try:
        payload = response.json()
        code = payload.get("code")
        message = str(payload.get("message") or "")
    except ValueError:
        return "http_400"

    if code in _REJECTION_CODES:
        return _REJECTION_CODES[code]
    # The numeric codes are undocumented and have changed before; the wording
    # is what Sber's own examples show, so it is worth a second look.
    lowered = message.lower()
    if "decode" in lowered and "authorization" in lowered:
        return CREDENTIAL_SHAPE
    if "scope" in lowered:
        return SCOPE_REJECTED
    return "http_400"


def _extract_content(payload: object) -> str | None:
    """Pull the reply text out, tolerating a response shaped unexpectedly."""
    try:
        content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()


class GigaChatBackend:
    """Chat-completions translation with a cached OAuth token."""

    def __init__(
        self,
        authorization_key: str,
        scope: str = DEFAULT_SCOPE,
        ca_bundle: str = "",
        model: str = DEFAULT_MODEL,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._authorization_key = authorization_key
        self._scope = scope or DEFAULT_SCOPE
        self._model = model or DEFAULT_MODEL
        self._retry = retry or RetryPolicy(attempts=2)

        self._session = requests.Session()
        # Scoped to this session on purpose: DeepL, Microsoft and MyMemory keep
        # the stock trust store, and the process-wide REQUESTS_CA_BUNDLE is
        # never touched.
        self._session.verify = ca_bundle or bundled_root_certificate() or True

        # In memory only. The access token is short-lived and must never reach
        # config.json, where the long-lived authorization key already lives.
        self._token: str = ""
        self._token_expires_at: float = 0.0

    # ── authentication ───────────────────────────────────────────────────

    @property
    def _token_is_fresh(self) -> bool:
        return bool(self._token) and time.time() < self._token_expires_at - _RENEW_MARGIN_SECONDS

    def _fetch_token(self) -> tuple[bool, str]:
        """Exchange the authorization key for an access token.

        Returns (ok, detail). The token itself is never part of the return
        value, so no caller can accidentally log it.
        """
        try:
            response = self._session.post(
                OAUTH_URL,
                headers={
                    "Authorization": f"Basic {self._authorization_key}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"scope": self._scope},
                timeout=_TIMEOUT,
            )
        except requests.exceptions.SSLError:
            # The most likely first-run failure, and the one whose obvious
            # workaround is dangerous. Name the fix instead of applying it.
            logger.error(
                "GigaChat TLS verification failed. Supply the Russian root certificate "
                "in the provider's settings — do not disable verification."
            )
            return False, TLS_UNTRUSTED
        except requests.RequestException as e:
            logger.warning("GigaChat token request failed: %s", _safe_error(e))
            return False, f"network: {_safe_error(e)}"
        except OSError as e:
            # requests raises a bare OSError for an unreadable CA bundle — not a
            # RequestException. Unguarded, it leaves the provider and kills the
            # translation thread, which is what happens when the certificate
            # lives on a drive that is no longer plugged in.
            logger.error("GigaChat could not use the configured certificate: %s", _safe_error(e))
            return False, CERT_UNREADABLE

        if response.status_code == 401:
            return False, FAILURE.AUTH
        if response.status_code == 429:
            return False, FAILURE.QUOTA
        if response.status_code == 400:
            return False, _why_rejected(response)
        if response.status_code != 200:
            return False, f"http_{response.status_code}"

        try:
            payload = response.json()
            token = payload["access_token"]
            expires_at = float(payload.get("expires_at", 0)) / 1000.0  # milliseconds
        except (ValueError, KeyError, TypeError):
            return False, BAD_RESPONSE
        if not isinstance(token, str) or not token:
            return False, BAD_RESPONSE

        self._token = token
        # A response without a usable expiry gets the documented lifetime rather
        # than being treated as immortal.
        self._token_expires_at = expires_at if expires_at > time.time() else time.time() + _DOCUMENTED_LIFETIME_SECONDS
        return True, "valid"

    # ── translation ──────────────────────────────────────────────────────

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        if not self._token_is_fresh:
            ok, detail = self._fetch_token()
            if not ok:
                return failed(text, target_lang, source_lang, detail, "gigachat")

        for attempt in range(self._retry.attempts):
            outcome = self._request_translation(text, target_lang)
            if outcome.success:
                return TranslationResult(
                    original=text,
                    translated=outcome.translated,
                    source_lang=(source_lang or "").upper(),
                    target_lang=target_lang,
                    success=True,
                    backend="gigachat",
                )

            if outcome.error == FAILURE.AUTH and attempt == 0:
                # The token expired mid-session or was revoked. One transparent
                # re-login; if that fails, hand over to the next provider rather
                # than looping on a credential the server keeps refusing.
                self._token = ""
                ok, detail = self._fetch_token()
                if not ok:
                    return failed(text, target_lang, source_lang, detail, "gigachat")
                continue

            if outcome.error in (FAILURE.AUTH, FAILURE.QUOTA, TLS_UNTRUSTED, BAD_RESPONSE, REFUSED):
                # Retrying cannot change any of these.
                return failed(text, target_lang, source_lang, outcome.error, "gigachat")

            if attempt < self._retry.attempts - 1:
                self._retry.backoff(attempt)

        # Report what actually went wrong. "max_retries_exceeded" discards the
        # http_503 or timeout that caused it, and that is the only thing anyone
        # reading the log or the settings screen can act on.
        last_error = outcome.error or FAILURE.RETRIES
        return failed(text, target_lang, source_lang, last_error, "gigachat")

    def _request_translation(self, text: str, target_lang: str) -> TranslationResult:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT.format(target=language_name(target_lang))},
                {"role": "user", "content": text},
            ],
            # Translation is not a creative task; keep it as literal as the
            # model allows.
            "temperature": 0.1,
        }
        try:
            response = self._session.post(
                CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=body,
                timeout=_TIMEOUT,
            )
        except requests.exceptions.SSLError:
            return failed(text, target_lang, None, TLS_UNTRUSTED, "gigachat")
        except requests.RequestException as e:
            return failed(text, target_lang, None, f"network: {_safe_error(e)}", "gigachat")
        except OSError as e:
            logger.error("GigaChat could not use the configured certificate: %s", _safe_error(e))
            return failed(text, target_lang, None, CERT_UNREADABLE, "gigachat")

        if response.status_code == 401:
            return failed(text, target_lang, None, FAILURE.AUTH, "gigachat")
        if response.status_code == 429:
            logger.warning("GigaChat rate limited or out of quota")
            return failed(text, target_lang, None, FAILURE.QUOTA, "gigachat")
        if response.status_code != 200:
            return failed(text, target_lang, None, f"http_{response.status_code}", "gigachat")

        try:
            payload = response.json()
        except ValueError:
            return failed(text, target_lang, None, BAD_RESPONSE, "gigachat")

        translated = _extract_content(payload)
        if translated is None:
            return failed(text, target_lang, None, BAD_RESPONSE, "gigachat")

        if looks_like_a_refusal(text, translated):
            # Not an error as far as HTTP is concerned — a 200 with a paragraph
            # about what the model will not discuss. Passing it on put that
            # paragraph in the overlay where the translation belongs. Reporting
            # it as a failure lets the next provider have a go, and MyMemory has
            # no opinion about the word it was handed.
            logger.info("GigaChat declined to translate a message; handing it on")
            return failed(text, target_lang, None, REFUSED, "gigachat")

        return TranslationResult(
            original=text,
            translated=translated,
            source_lang="",
            target_lang=target_lang,
            success=True,
            backend="gigachat",
        )

    def validate(self) -> tuple[bool, str]:
        return self._fetch_token()


def _build(settings: dict[str, str]) -> GigaChatBackend:
    return GigaChatBackend(
        authorization_key=authorization_key(settings),
        scope=settings.get("scope", ""),
        ca_bundle=settings.get("ca_bundle", ""),
    )


def _validate(settings: dict[str, str]) -> tuple[bool, str]:
    if not authorization_key(settings):
        # One field filled and the other empty is a different mistake from an
        # untouched form, and "no key" reads as the latter. Say which field is
        # waiting rather than sending a request that can only be refused.
        filled = [bool((settings.get(key) or "").strip()) for key in ("client_id", "client_secret")]
        if any(filled):
            return False, CREDENTIAL_MISSING
        return False, FAILURE.NO_KEY
    # Before the request: a handshake failure looks the same whether the file is
    # an intermediate, the wrong format or missing, and reporting all three as
    # "tls_untrusted" leaves the user guessing at the one thing they can fix.
    problem = describe_certificate(settings.get("ca_bundle", "").strip())
    if problem:
        return False, problem
    try:
        return _build(settings).validate()
    except Exception as e:
        return False, _safe_error(e)


SPEC = register(
    ProviderSpec(
        id="gigachat",
        display_name="GigaChat",
        fields=(
            ProviderField(
                key="client_id",
                label="provider.gigachat.client_id",
                placeholder="provider.gigachat.client_id_hint",
                help_url="https://developers.sber.ru/studio/workspaces",
                help_label="provider.get_key",
            ),
            ProviderField(
                key="client_secret",
                label="provider.gigachat.client_secret",
                placeholder="provider.gigachat.client_secret_hint",
            ),
            ProviderField(
                key="ca_bundle",
                label="provider.gigachat.ca",
                placeholder="provider.gigachat.ca_hint",
                secret=False,
                required=False,
            ),
        ),
        build=_build,
        validate=_validate,
        note="provider.gigachat.note",
        guide="https://github.com/Yumash/BabelChat/blob/main/docs/user/gigachat.md",
    )
)
