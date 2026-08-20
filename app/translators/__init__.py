"""Translation providers.

Importing this package registers every provider that ships with the app. The
imports below look unused and are not: each module calls `register()` at import
time, and that is how the registry gets populated.
"""

from app.translators import deepl_provider as _deepl  # noqa: F401,E402  (registers "deepl")
from app.translators import microsoft_provider as _microsoft  # noqa: F401,E402  (registers "microsoft")
from app.translators.base import (
    FAILURE,
    ProviderField,
    ProviderSpec,
    RetryPolicy,
    TranslationResult,
    TranslatorBackend,
    all_providers,
    any_configured,
    configured_ids,
    get,
    known_ids,
    register,
    resolve_order,
)
from app.translators.service import TranslatorService

__all__ = [
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
]
