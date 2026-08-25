"""Translation service — kept as the import path the rest of the app uses.

The implementation moved into `app.translators`, where each provider declares
itself once instead of being wired through translator.py, config.py, two
settings dialogs, two wizards and two entry points.
"""

from __future__ import annotations

from app.translators import (
    FAILURE,
    ProviderField,
    ProviderSpec,
    RetryPolicy,
    TranslationResult,
    TranslatorBackend,
    TranslatorService,
    all_providers,
    any_configured,
    configured_ids,
    get,
    known_ids,
    register,
    resolve_order,
)
from app.translators.deepl_provider import SUPPORTED_LANGUAGES as DEEPL_LANGUAGES

__all__ = [
    "DEEPL_LANGUAGES",
    "FAILURE",
    "ProviderField",
    "ProviderSpec",
    "RetryPolicy",
    "TranslationResult",
    "TranslatorBackend",
    "TranslatorService",
    "all_providers",
    "any_configured",
    "configured_ids",
    "get",
    "known_ids",
    "register",
    "resolve_order",
    "validate_provider",
]


def validate_provider(provider_id: str, settings: dict[str, str]) -> tuple[bool, str]:
    """Check one provider's credentials. Returns (usable, human-readable detail).

    An unknown provider id is a configuration problem, not a crash: the UI shows
    the message the same way it shows a rejected key.
    """
    spec = get(provider_id)
    if spec is None:
        return False, f"unknown provider: {provider_id}"
    return spec.validate(settings)
