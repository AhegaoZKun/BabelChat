"""What a translation provider is, and how the app finds out about one.

Adding a provider used to mean editing translator.py, config.py, both settings
dialogs, both setup wizards and both entry points — twenty-odd places, and
missing one of them produced a backend that worked but could not be configured.

A provider now declares itself once, as a `ProviderSpec`: how to build the
backend, how to check a credential, and what the credential fields are. The
settings UI renders those fields; nothing downstream needs to know the
provider's name.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """Result of a translation attempt.

    On failure `translated` holds the original text: every caller renders this
    field, so a failed translation degrades to showing the message untranslated
    rather than showing nothing.
    """

    original: str
    translated: str
    source_lang: str
    target_lang: str
    success: bool
    error: str | None = None
    backend: str = ""


@runtime_checkable
class TranslatorBackend(Protocol):
    """A live, configured connection to one translation service."""

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult: ...

    def validate(self) -> tuple[bool, str]:
        """(usable, human-readable detail) — quota, 'valid', or a failure reason."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderField:
    """One credential input, and enough about it to draw the row."""

    key: str
    label: str
    placeholder: str = ""
    secret: bool = True
    required: bool = True
    help_url: str = ""
    help_label: str = ""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Everything the app needs to know about a provider it has never met."""

    id: str
    display_name: str
    fields: tuple[ProviderField, ...]
    build: Callable[[dict[str, str]], TranslatorBackend]
    validate: Callable[[dict[str, str]], tuple[bool, str]]
    #: Shown where a provider needs explaining — cost, sign-up friction, region.
    note: str = ""
    #: Free of charge and needs no account at all.
    keyless: bool = False

    def is_configured(self, settings: dict[str, str]) -> bool:
        if self.keyless:
            return True
        return all(settings.get(f.key, "").strip() for f in self.fields if f.required)


_REGISTRY: dict[str, ProviderSpec] = {}
_ORDER: list[str] = []


def register(spec: ProviderSpec) -> ProviderSpec:
    """Add a provider. Import order decides the listing order until it is set."""
    if spec.id in _REGISTRY:
        raise ValueError(f"provider {spec.id!r} is already registered")
    _REGISTRY[spec.id] = spec
    _ORDER.append(spec.id)
    return spec


def set_listing_order(order: Iterable[str]) -> None:
    """Fix the order providers are listed and tried in.

    Leaving this to import order was a trap: the import sorter rearranged the
    provider imports and silently reordered the fallback chain with them. The
    order is a product decision — which provider a new player should reach for
    first — so it is stated once, explicitly, and anything unlisted keeps its
    registration position at the end.
    """
    wanted = [pid for pid in order if pid in _REGISTRY]
    rest = [pid for pid in _ORDER if pid not in wanted]
    _ORDER[:] = wanted + rest


def get(provider_id: str) -> ProviderSpec | None:
    return _REGISTRY.get(provider_id)


def all_providers() -> tuple[ProviderSpec, ...]:
    return tuple(_REGISTRY[pid] for pid in _ORDER)


def known_ids() -> tuple[str, ...]:
    return tuple(_ORDER)


def resolve_order(priority: str, configured: Iterable[str]) -> list[str]:
    """Provider ids to try, preferred one first, then the rest in listing order.

    An unknown or unconfigured `priority` is not an error: it just means no
    preference, and the registration order stands. A config naming a provider
    this build does not have must not stop the ones it does have from working.
    """
    wanted = set(configured)
    available = [pid for pid in _ORDER if pid in wanted]
    unknown = wanted - set(_ORDER)
    if unknown:
        logger.warning("Config names providers this build does not have: %s", ", ".join(sorted(unknown)))
    if priority in available:
        available.remove(priority)
        return [priority, *available]
    if priority and priority not in _REGISTRY:
        logger.warning("Unknown translator priority %r — falling back to listing order", priority)
    return available


def configured_ids(providers: dict[str, dict[str, str]] | None) -> list[str]:
    """Provider ids this build knows about AND has complete credentials for."""
    result = []
    for provider_id, settings in (providers or {}).items():
        spec = _REGISTRY.get(provider_id)
        if spec is not None and spec.is_configured(settings or {}):
            result.append(provider_id)
    return result


def any_configured(providers: dict[str, dict[str, str]] | None) -> bool:
    """True when at least one provider could translate right now.

    This is what "has the user finished setting the app up?" means — asking it
    of a specific provider is how the first-run check ended up hardcoding two
    of them and blocking on a key the user may not need.
    """
    return bool(configured_ids(providers))


@dataclass
class RetryPolicy:
    """Shared retry shape, so every backend backs off the same way.

    `delay=0` makes retries instant, which is what tests want — no backend
    should need its own sleep-patching.
    """

    attempts: int = 3
    delay: float = 1.0

    def backoff(self, attempt: int) -> None:
        if self.delay:
            time.sleep(self.delay * (2**attempt))


class FAILURE:
    """Reasons a backend gives up, in one place so callers can match on them."""

    NO_BACKEND = "no_backend"
    AUTH = "auth_failed"
    QUOTA = "quota_exceeded"
    RETRIES = "max_retries_exceeded"
    NO_KEY = "no_key"


def failed(
    text: str,
    target_lang: str,
    source_lang: str | None,
    error: str,
    backend: str = "",
) -> TranslationResult:
    """A failure result carrying the original text through untouched."""
    return TranslationResult(
        original=text,
        translated=text,
        source_lang=source_lang or "",
        target_lang=target_lang,
        success=False,
        error=error,
        backend=backend,
    )


def unchanged(text: str, target_lang: str, source_lang: str | None) -> TranslationResult:
    """A success result for input that needs no translation (blank, whitespace)."""
    return TranslationResult(
        original=text,
        translated=text,
        source_lang=source_lang or "",
        target_lang=target_lang,
        success=True,
    )
