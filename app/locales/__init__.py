"""Interface strings, one module per language.

`STRINGS` is assembled here into the shape the rest of the app uses — key to
language to text — so that adding a language means adding a file and a line
rather than editing two hundred entries in place.
"""

from __future__ import annotations

from app.locales import en, es, ru

#: Language code to the module holding it.
LANGUAGE_MODULES = {"EN": en, "RU": ru, "ES": es}


def build() -> dict[str, dict[str, str]]:
    """Key to language to text, in the order English declares the keys."""
    table: dict[str, dict[str, str]] = {}
    for language, module in LANGUAGE_MODULES.items():
        for key, text in module.STRINGS.items():
            table.setdefault(key, {})[language] = text
    return table


STRINGS = build()
