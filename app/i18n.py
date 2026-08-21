"""Internationalization — RU/EN/ES UI translations."""

from __future__ import annotations

from typing import ClassVar

from app import locales

# All translatable strings keyed by ID
#: Key to language to text, assembled from app/locales — one module per
#: language. It used to be a thousand-line literal here, which meant every
#: translation lived in the middle of the mechanism that renders it, and a
#: translator had to find their language three lines at a time.
#:
#: The shape is unchanged, so everything that reads it still does.
_STRINGS: dict[str, dict[str, str]] = locales.STRINGS

UI_LANGUAGES = {"RU": "Русский", "EN": "English", "ES": "Español"}


class tr:
    """Simple translation helper. Call tr("key") to get localized string."""

    _lang: ClassVar[str] = "RU"

    @classmethod
    def set_language(cls, lang: str) -> None:
        cls._lang = lang if lang in ("RU", "EN", "ES") else "RU"

    @classmethod
    def get_language(cls) -> str:
        return cls._lang

    @classmethod
    def __class_getitem__(cls, key: str) -> str:
        """Allow tr["key"] syntax."""
        return cls(key)

    def __new__(cls, key: str, **kwargs: object) -> str:  # type: ignore[misc]
        entry = _STRINGS.get(key)
        if not entry:
            return key
        text = entry.get(cls._lang, entry.get("EN", key))
        if kwargs:
            text = text.format(**kwargs)
        return text
