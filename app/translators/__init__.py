"""Translation providers.

Importing this package registers every provider that ships with the app. The
imports below look unused and are not: each module calls `register()` at import
time, and that is how the registry gets populated.
"""

from app.translators import deepl_provider as _deepl  # noqa: F401,E402  (registers "deepl")
from app.translators import gigachat_provider as _gigachat  # noqa: F401,E402  (registers "gigachat")
from app.translators import microsoft_provider as _microsoft  # noqa: F401,E402  (registers "microsoft")
from app.translators import mymemory_provider as _mymemory  # noqa: F401,E402  (registers "mymemory")
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
    set_listing_order,
)
from app.translators.service import TranslatorService

# The order a new player meets these in, and the order the chain falls through.
# GigaChat leads because it is the only one someone in Russia can sign up for
# without a foreign card; MyMemory follows because it needs no account at all.
# Stated here rather than left to import order, which the import sorter rewrites.
set_listing_order(("gigachat", "mymemory", "deepl", "microsoft"))

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
    "set_listing_order",
]
