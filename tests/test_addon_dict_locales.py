"""Guard: every addon dictionary entry must define all 14 supported locales.

The in-game dictionary (addon/BabelChat/Data/*.lua) translates WoW terms into
14 client languages. A missing locale silently falls back to enUS in-game, so
this test fails loudly if any entry drops a locale — protecting both the legacy
terms and any newly added category (e.g. Endgame.lua).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LOCALES = (
    "esES",
    "esMX",
    "enUS",
    "deDE",
    "frFR",
    "itIT",
    "koKR",
    "ptBR",
    "ruRU",
    "zhCN",
    "zhTW",
    "plPL",
    "svSE",
    "noNO",
)

_DATA_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat" / "Data"
# ["key"] = { ... }  — locale tables contain no nested braces, so a non-greedy
# match up to the first closing brace captures the full entry body.
_ENTRY_RE = re.compile(r'\["([^"]+)"\]\s*=\s*\{(.*?)\}', re.S)
_LOCALE_KEY_RE = re.compile(r'(\w+)\s*=\s*"')


def _entries(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    return _ENTRY_RE.findall(text)


def _data_files():
    return sorted(_DATA_DIR.glob("*.lua"))


def test_data_dir_present():
    files = _data_files()
    assert files, f"no dictionary Data files found in {_DATA_DIR}"


@pytest.mark.parametrize("path", _data_files(), ids=lambda p: p.name)
def test_every_entry_has_all_locales(path: Path):
    missing: list[str] = []
    for key, body in _entries(path):
        present = set(_LOCALE_KEY_RE.findall(body))
        gaps = [loc for loc in LOCALES if loc not in present]
        if gaps:
            missing.append(f"{key}: missing {gaps}")
    assert not missing, f"{path.name} has entries missing locales:\n" + "\n".join(missing)


def test_endgame_category_present():
    """The Midnight/endgame category should exist and be non-trivial."""
    endgame = _DATA_DIR / "Endgame.lua"
    assert endgame.exists(), "Endgame.lua (Midnight terms) is missing"
    assert len(_entries(endgame)) >= 15, "Endgame.lua should define the current endgame terms"
