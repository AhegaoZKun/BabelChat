"""The languages a message can be translated into.

Lives on its own because both frontends need it and the Qt dialog cannot be
imported from the GTK one. While it lived there, the GTK settings window
carried its own list of eleven bare language codes against this one's
twenty-two names — a Linux user could not pick most of the languages the
app supports, and the ones offered were shown as "PT" rather than
"Português".
"""

from __future__ import annotations

# Every language named in itself, which is what a language picker does: it
# needs no translation per interface language, and a speaker recognises their
# own language faster than a translation of its name. These were English names
# on an otherwise Russian screen — "Ваш язык: Russian".
LANGUAGES = {
    "EN": "English",
    "RU": "Русский",
    "DE": "Deutsch",
    "FR": "Français",
    "ES": "Español",
    "IT": "Italiano",
    "PT": "Português",
    "PL": "Polski",
    "NL": "Nederlands",
    "SV": "Svenska",
    "DA": "Dansk",
    "FI": "Suomi",
    "CS": "Čeština",
    "RO": "Română",
    "HU": "Magyar",
    "BG": "Български",
    "EL": "Ελληνικά",
    "TR": "Türkçe",
    "UK": "Українська",
    "JA": "日本語",
    "KO": "한국어",
    "ZH": "中文",
}
