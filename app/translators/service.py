"""Picks a provider, and falls through to the next one when it fails."""

from __future__ import annotations

import logging

from app.translators import base
from app.translators.base import (
    FAILURE,
    TranslationResult,
    TranslatorBackend,
    failed,
    unchanged,
)

logger = logging.getLogger(__name__)


class TranslatorService:
    """Translates through the configured providers, preferred one first.

    Construction never raises on a bad provider: one unusable entry is logged
    and skipped so the rest keep working. A config written by a newer build —
    naming a provider this one has never heard of — must not take the app down.
    """

    def __init__(
        self,
        providers: dict[str, dict[str, str]] | None = None,
        priority: str = "",
    ) -> None:
        self._backends: dict[str, TranslatorBackend] = {}
        self._priority = priority

        for provider_id, settings in (providers or {}).items():
            spec = base.get(provider_id)
            if spec is None:
                logger.warning("Skipping unknown translation provider %r", provider_id)
                continue
            # A hand-edited or newer-build config can carry `"deepl": null`.
            # The class promises never to raise on a bad provider, and that
            # promise has to hold for the shape of the entry too, not just its
            # contents.
            if not isinstance(settings, dict):
                logger.warning("Ignoring malformed settings for provider %r", provider_id)
                continue
            if not spec.is_configured(settings):
                continue
            try:
                self._backends[provider_id] = spec.build(settings)
            except Exception as e:
                logger.warning("Could not initialise %s: %s", spec.display_name, e)

        if not self.has_backend:
            logger.warning("No translation backend configured")

    @classmethod
    def from_config(cls, config) -> TranslatorService:
        """Build from an AppConfig — the one place that knows the config shape."""
        return cls(providers=config.providers, priority=config.translator_priority)

    @property
    def has_backend(self) -> bool:
        return bool(self._backends)

    @property
    def active_ids(self) -> tuple[str, ...]:
        """Provider ids in the order they will be tried."""
        return tuple(base.resolve_order(self._priority, self._backends))

    def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        if not text.strip():
            return unchanged(text, target_lang, source_lang)

        chain = self.active_ids
        if not chain:
            return failed(text, target_lang, source_lang, FAILURE.NO_BACKEND)

        result: TranslationResult | None = None
        for provider_id in chain:
            result = self._backends[provider_id].translate(text, target_lang, source_lang)
            if result.success:
                return result
            # The provider id, not the error text: a provider can put the
            # request URL in there, and for a GET-based one that URL carries
            # the message and any identifying parameter.
            logger.info("Backend %s failed, trying next", provider_id)
            logger.debug("Backend %s error: %s", provider_id, result.error)
        # Every backend failed; the last failure is the most informative one to
        # surface, and it still carries the original text.
        return result  # type: ignore[return-value]

    def get_usage(self):
        """DeepL usage stats, when DeepL is one of the configured providers."""
        backend = self._backends.get("deepl")
        return backend.get_usage() if backend is not None else None
