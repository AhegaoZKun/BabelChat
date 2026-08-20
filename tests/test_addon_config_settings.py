"""Setting keys, their migration, and the layout arithmetic behind the panel.

The category toggles were named after the Spanish addon this dictionary came
from — `showMazz`, `showClases`, `showComercio` — which nobody reading their own
SavedVariables could decode. Renaming them is only safe if the saved values move
with them: otherwise every category a player had switched off comes back on,
silently, on the next login.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import AddonHarness  # noqa: E402

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"

# Renamed in this change; the values must follow.
RENAMED = {
    "showMazz": "showDungeons",
    "showClases": "showClasses",
    "showCombate": "showCombat",
    "showComercio": "showTrade",
    "showGrupos": "showGroups",
    "showHermandad": "showGuild",
    "showEstado": "showStatus",
    "showProfesiones": "showProfessions",
}


@pytest.fixture
def core():
    """Core.lua loaded far enough to expose the migration helper.

    Core.lua registers an event frame at the bottom; the harness's stubs cover
    what it touches at load time, and the handler never fires here.
    """
    harness = AddonHarness()
    lua = harness.lua
    # A frame that accepts any method call and returns another such frame, which
    # is enough for Core.lua to finish loading without a game underneath it.
    lua.execute(
        "CreateFrame = function()  return setmetatable({}, {__index = function() return function() end end})end"
    )
    lua.execute("SlashCmdList = {}; strtrim = function(s) return (s:gsub('^%s*(.-)%s*$', '%1')) end")
    lua.globals().GetLocale = lambda: "enUS"
    harness.addon_table.L = lua.eval("setmetatable({}, {__index = function(_, k) return k end})")
    harness.load("Core.lua")
    return harness


# ── the migration ────────────────────────────────────────────────────────────


def test_a_disabled_category_stays_disabled_through_the_rename(core):
    settings = core.lua.eval("{ showMazz = false, showClases = false, showSocial = true }")

    moved = core.addon_table.MigrateSettingKeys(settings)

    assert moved == 2
    assert settings["showDungeons"] is False
    assert settings["showClasses"] is False
    assert settings["showSocial"] is True


def test_the_old_keys_are_removed(core):
    settings = core.lua.eval("{ showMazz = false }")

    core.addon_table.MigrateSettingKeys(settings)

    assert settings["showMazz"] is None


def test_running_the_migration_twice_changes_nothing(core):
    settings = core.lua.eval("{ showComercio = false, showGrupos = true }")

    first = core.addon_table.MigrateSettingKeys(settings)
    second = core.addon_table.MigrateSettingKeys(settings)

    assert (first, second) == (2, 0)
    assert settings["showTrade"] is False
    assert settings["showGroups"] is True


def test_a_config_already_on_the_new_keys_is_untouched(core):
    settings = core.lua.eval("{ showDungeons = false, showClasses = true }")

    moved = core.addon_table.MigrateSettingKeys(settings)

    assert moved == 0
    assert settings["showDungeons"] is False
    assert settings["showClasses"] is True


def test_when_both_names_are_present_the_new_one_wins(core):
    """It is what the player last chose in a version that used it."""
    settings = core.lua.eval("{ showMazz = true, showDungeons = false }")

    core.addon_table.MigrateSettingKeys(settings)

    assert settings["showDungeons"] is False
    assert settings["showMazz"] is None


def test_a_missing_settings_table_is_survivable(core):
    assert core.addon_table.MigrateSettingKeys(None) == 0
    assert core.addon_table.MigrateSettingKeys("not a table") == 0


def test_every_renamed_key_is_covered(core):
    renames = dict(core.addon_table.SETTING_RENAMES)
    assert renames == RENAMED


# ── the keys the rest of the addon uses ──────────────────────────────────────


def source(*names: str) -> str:
    return "\n".join((ADDON_DIR / name).read_text(encoding="utf-8") for name in names)


def test_no_spanish_derived_key_survives_anywhere():
    """A leftover in DictEngine or Config would read a key nothing writes, and
    the category would appear enabled while translating nothing."""
    text = source("Core.lua", "Config.lua", "DictEngine.lua")
    # The rename table itself is the one legitimate place they still appear.
    text = text.split("addonTable.SETTING_RENAMES")[0] + text.split("}")[-1]
    leftovers = [old for old in RENAMED if re.search(rf'"{old}"|\b{old}\s*=', text)]
    assert leftovers == [], f"still referenced: {leftovers}"


def test_the_dictionary_engine_and_the_panel_agree_on_every_key():
    engine = re.findall(r'\{ key = "(\w+)"', source("DictEngine.lua"))
    panel = re.findall(r'key = "(\w+)"', source("Config.lua"))
    assert set(engine) - set(panel) == set(), "engine reads a toggle the panel never shows"


def test_the_defaults_cover_every_key_the_panel_shows():
    defaults = set(re.findall(r"^\s+(show\w+) = ", source("Core.lua"), re.M))
    panel = set(re.findall(r'key = "(show\w+)"', source("Config.lua")))
    assert panel - defaults == set(), "a toggle with no default starts as nil"


# ── layout arithmetic ────────────────────────────────────────────────────────
#
# The panel cannot be rendered here, so the geometry is checked as the numbers
# it is built from. That is exactly where the bug was: a five-pixel gap under a
# fourteen-pixel heading, with a twenty-six-pixel control beneath it.

HEADING_HEIGHT = 14
CHECKBOX_HEIGHT = 26


def config_constant(name: str) -> int:
    match = re.search(rf"^local {name} = (\d+)$", source("Config.lua"), re.M)
    assert match, f"{name} is not a plain constant any more"
    return int(match.group(1))


def test_a_heading_never_overlaps_the_row_beneath_it():
    gap = config_constant("SECTION_GAP")
    assert gap >= HEADING_HEIGHT, f"a {gap}px gap puts the checkbox {HEADING_HEIGHT - gap}px inside the heading"


def test_every_section_uses_the_shared_gap():
    """Two sections used 5 and two used 25; the difference was not intentional."""
    text = source("Config.lua")
    literal_gaps = re.findall(r"yOffset = yOffset - (\d+)\n(\s+)for i, info in ipairs", text)
    assert literal_gaps == [], f"a section still hardcodes its gap: {literal_gaps}"


def test_the_scroll_content_is_taller_than_the_content_it_holds():
    """Cheap upper bound: title, two 3-column grids and the trailing sections."""
    text = source("Config.lua")
    height = config_constant("CONTENT_HEIGHT")
    categories = len(re.findall(r'key = "show\w+"', text))
    rows = -(-categories // 3)
    # 130px above the grid, 25px per row, and ~250px of channels plus companion.
    minimum = 130 + rows * 25 + 250
    assert height >= minimum, f"{height}px cannot hold about {minimum}px of content"


def test_the_panel_is_registered_as_a_scrolling_canvas():
    text = source("Config.lua")
    assert "UIPanelScrollFrameTemplate" in text
    assert "SetScrollChild" in text
    assert "RegisterCanvasLayoutCategory(canvas" in text, "the scroll child must not be the canvas"
