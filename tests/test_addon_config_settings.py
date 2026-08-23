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


# ── the panel, built ─────────────────────────────────────────────────────────


def source(*names: str) -> str:
    return chr(10).join((ADDON_DIR / name).read_text(encoding="utf-8") for name in names)


RECORDING_FRAME = """
    _frames = {}
    _registered = nil
    _scroll_child = nil

    local function NewFrame(kind, name, parent, template)
        local frame = {
            -- `false` rather than nil: a nil field falls through to the
            -- __index below and comes back as a function, which reads as
            -- "present" on the Python side and made this fixture lie once.
            kind = kind, name = name or false, parent = parent, template = template or false,
            points = {}, height = 0, width = 0, text = false, shown = true,
        }
        function frame:GetName() return self.name end
        function frame:SetPoint(anchor, a, b, c, d)
            -- Both shapes the panel uses: (anchor, x, y) and
            -- (anchor, relativeTo, relativePoint, x, y).
            if type(a) == "table" then
                -- Anchored to another frame: its x/y are in that frame's space,
                -- not the panel's, so they are recorded but not comparable.
                table.insert(self.points, { anchor = anchor, relative = a, x = c, y = d, absolute = false })
            else
                table.insert(self.points, { anchor = anchor, x = a, y = b, absolute = true })
            end
        end
        function frame:SetSize(w, h) self.width, self.height = w, h end
        function frame:SetHeight(h) self.height = h end
        function frame:SetWidth(w) self.width = w end
        function frame:SetText(value) self.text = value end
        function frame:SetScrollChild(child) _scroll_child = child end
        function frame:CreateTexture()
            local texture = NewFrame("Texture")
            table.insert(_frames, texture)
            return texture
        end
        function frame:CreateFontString()
            local fontString = NewFrame("FontString")
            table.insert(_frames, fontString)
            return fontString
        end
        return setmetatable(frame, {
            __index = function(_, key)
                if key == "GetName" then return nil end
                return function() end
            end,
        })
    end

    CreateFrame = function(kind, name, parent, template)
        local frame = NewFrame(kind, name, parent, template)
        table.insert(_frames, frame)
        if name then
            _G[name] = frame
            -- InterfaceOptionsCheckButtonTemplate creates a "<name>Text" label.
            _G[name .. "Text"] = NewFrame("FontString", name .. "Text")
        end
        return frame
    end

    UIParent = CreateFrame("Frame", "UIParent")
    C_AddOns = { GetAddOnMetadata = function() return "3.3.0" end }
    -- The dropdown API is a dozen loose globals and none of them matter to
    -- what these tests measure. Narrowly scoped so any OTHER missing global
    -- still surfaces as the nil-index error the harness intends.
    setmetatable(_G, { __index = function(_, key)
        if type(key) == "string" and key:match("^UIDropDownMenu_") then
            return function() return {} end
        end
        return nil
    end })
    ColorPickerFrame = CreateFrame("Frame", "ColorPickerFrame")
    Settings = {
        RegisterCanvasLayoutCategory = function(canvas, title)
            _registered = canvas
            return {
                ID = title,
                GetID = function(self) return self.ID end,
                SetID = function(self, value) self.ID = value end,
            }
        end,
        RegisterAddOnCategory = function() end,
    }
"""


@pytest.fixture
def panel(core):
    """Config.lua run for real, with a CreateFrame that records what it built.

    These properties used to be checked by matching the source text, which meant
    renaming a local variable reddened a test without breaking the addon, and
    shipping the bug the test named passed as long as some line still looked
    right. Building the panel measures the panel.
    """
    lua = core.lua
    lua.execute(RECORDING_FRAME)
    # The addon's own defaults, not a hand-written stand-in: a panel that reads
    # a field ApplyDefaults never writes is exactly the failure worth catching.
    lua.execute("BabelChatDB = {}")
    core.addon_table.InitialiseSavedVariables()
    core.load("Config.lua")
    core.addon_table.CreateConfigUI()
    return core


def _load_dictionary_data(harness):
    """The engine plus the tables it indexes, including a LibBabble that answers.

    A toggle can only be shown to switch something if the thing it switches is
    loaded; without the Data files every category looks equally dead and the
    test passes on nothing.
    """
    for path in sorted((ADDON_DIR / "Data").glob("*.lua")):
        harness.load(f"Data/{path.name}")
    harness.lua.execute(
        """
        _babble = { ["Elwynn Forest"] = "Элвиннский лес", ["Duskwood"] = "Сумеречный лес" }
        local library = { GetUnstrictLookupTable = function() return _babble end }
        LibStub = function() return library end
        """
    )
    harness.load("DictEngine.lua")
    harness.addon_table.InitLibBabble()


def built_frames(harness):
    return list(harness.lua.globals()._frames.values())


def checkbox_keys(harness) -> set[str]:
    """The category toggles the panel actually created, by their setting key."""
    prefix = "WCT_CB_"
    return {
        frame.name[len(prefix):]
        for frame in built_frames(harness)
        if frame.name and str(frame.name).startswith(prefix)
    }


def test_the_panel_creates_a_checkbox_for_every_category_the_engine_reads(panel):
    """The engine consults a fixed map of setting keys. One the panel never
    shows is a category the user cannot switch on, permanently off or
    permanently on depending on the default."""
    _load_dictionary_data(panel)
    engine_keys = set(re.findall(r'\{ key = "(\w+)"', source("DictEngine.lua")))

    shown = checkbox_keys(panel)

    assert engine_keys - shown == set(), f"the engine reads a toggle nobody can see: {engine_keys - shown}"


def test_every_checkbox_the_panel_shows_has_a_default(panel):
    """A toggle with no default starts as nil, which reads as off — so a
    category the user never touched is silently disabled."""
    panel.lua.execute("BabelChatDB.dict.settings = {}")
    panel.addon_table.InitialiseSavedVariables()
    defaults = panel.lua.globals().BabelChatDB.dict.settings

    missing = [key for key in checkbox_keys(panel) if defaults[key] is None]

    assert missing == [], f"no default for: {missing}"


def test_the_scrolling_canvas_is_registered_and_is_not_the_scroll_child(panel):
    """Registering the scroll child instead of the canvas gives the options
    window a frame that scrolls inside itself and clips at the wrong edge."""
    registered = panel.lua.globals()._registered
    child = panel.lua.globals()._scroll_child

    assert registered is not None, "nothing was registered with the options window"
    assert child is not None, "the scroll frame was never given its content"
    assert registered.name != child.name


def test_no_section_heading_overlaps_the_row_beneath_it(panel):
    """Two sections used a 5px gap and two used 25px; the difference was not
    intentional, and 5px under a 14px heading put the checkbox inside the text.

    Only headings are checked. The category grid runs on a deliberate 25px
    pitch, which is tighter than the template's 26px hit area and correct: that
    was never the complaint, and asserting on it would fail the addon for
    looking the way it is meant to look.
    """
    headings = [
        (frame, point)
        for frame in built_frames(panel)
        if frame.kind == "FontString" and not frame.name and frame.text
        for point in frame.points.values()
        if point.absolute and point.y is not None and point.x is not None
    ]
    rows = sorted(
        (
            (point.y, point.x, frame)
            for frame in built_frames(panel)
            for point in frame.points.values()
            if point.absolute and point.y is not None and point.x is not None
        ),
        key=lambda row: -row[0],
    )

    too_close = []
    for heading, point in headings:
        below = [y for y, x, frame in rows if y < point.y and frame is not heading]
        if not below:
            continue
        gap = point.y - max(below)
        if gap < HEADING_HEIGHT:
            too_close.append((heading.text, gap))

    assert too_close == [], f"a {HEADING_HEIGHT}px heading with less than that beneath it: {too_close}"


# ── the keys the rest of the addon uses ──────────────────────────────────────
# ── the keys the rest of the addon uses ──────────────────────────────────────


def test_every_toggle_the_panel_shows_actually_switches_a_category(panel):
    """The Spanish-derived key names were renamed across three files. A leftover
    would read a key nothing writes, and the category would appear enabled in
    the panel while translating nothing — silent, and impossible for a user to
    report as anything but "it does not work".

    This used to be a regex over the source of those three files, which cut the
    text to 7% of itself before searching and never looked at Config.lua at all.
    Switching each toggle and watching the dictionary respond measures the thing
    the names were only a proxy for.
    """
    _load_dictionary_data(panel)
    settings = panel.lua.globals().BabelChatDB.dict.settings

    dead = []
    for key in sorted(checkbox_keys(panel)):
        for other in checkbox_keys(panel):
            settings[other] = False
        panel.addon_table.RebuildMasterDict()
        without = panel.addon_table.MasterDictSize()

        settings[key] = True
        panel.addon_table.RebuildMasterDict()
        with_it = panel.addon_table.MasterDictSize()

        if with_it <= without:
            dead.append(key)

    assert dead == [], f"these toggles switch nothing: {dead}"


def test_the_old_spanish_key_names_are_gone_from_the_panel(panel):
    """The rename table is the one place they are still allowed to appear."""
    shown = checkbox_keys(panel)
    survivors = sorted(shown & set(RENAMED))

    assert survivors == [], f"the panel still shows: {survivors}"


# ── layout arithmetic ────────────────────────────────────────────────────────
#
# The panel IS built here now, so these are the two heights the recorded frames
# are measured against: a heading's text and a checkbox's hit area. The bug was
# a five-pixel gap under a fourteen-pixel heading, with a twenty-six-pixel
# control beneath it.

HEADING_HEIGHT = 14
CHECKBOX_HEIGHT = 26


def config_constant(name: str) -> int:
    match = re.search(rf"^local {name} = (\d+)$", source("Config.lua"), re.M)
    assert match, f"{name} is not a plain constant any more"
    return int(match.group(1))


def test_every_section_uses_the_shared_gap():
    """Two sections used 5 and two used 25; the difference was not intentional."""
    text = source("Config.lua")
    literal_gaps = re.findall(r"yOffset = yOffset - (\d+)\n(\s+)for i, info in ipairs", text)
    assert literal_gaps == [], f"a section still hardcodes its gap: {literal_gaps}"




# ── which language the gloss speaks ──────────────────────────────────────────


def locale_harness(client_locale: str, saved: str | None = None):
    """Core.lua initialised as it would be on a client with this locale."""
    harness = AddonHarness()
    lua = harness.lua
    lua.execute(
        "CreateFrame = function()  return setmetatable({}, {__index = function() return function() end end})end"
    )
    lua.execute("SlashCmdList = {}; strtrim = function(s) return (s:gsub('^%s*(.-)%s*$', '%1')) end")
    lua.globals().GetLocale = lambda: client_locale
    harness.addon_table.L = lua.eval("setmetatable({}, {__index = function(_, k) return k end})")
    harness.load("Core.lua")
    if saved is not None:
        lua.globals().BabelChatDB = lua.eval(f'{{ dict = {{ targetLocale = "{saved}" }} }}')
    harness.addon_table.InitialiseSavedVariables()
    return harness


@pytest.mark.parametrize(
    ("client", "expected"),
    [("ruRU", "ruRU"), ("deDE", "deDE"), ("frFR", "frFR"), ("enUS", "enUS"), ("enGB", "enUS")],
)
def test_a_fresh_install_glosses_in_the_language_of_the_client(client, expected):
    """The shipped default was "esES", inherited from the Spanish addon this
    dictionary came from, and the detection compared against "enUS" — so it
    never fired and a Russian player's gloss came out in Spanish. Nobody reports
    that as a locale bug; they report that the dictionary is wrong."""
    h = locale_harness(client)

    assert h.lua.globals().BabelChatDB.dict.targetLocale == expected


def test_an_existing_config_carrying_the_old_spanish_default_is_corrected():
    """It is indistinguishable from a deliberate choice except by the client:
    somebody playing in Spanish meant it, and nobody else did."""
    h = locale_harness("ruRU", saved="esES")

    assert h.lua.globals().BabelChatDB.dict.targetLocale == "ruRU"


@pytest.mark.parametrize("client", ["esES", "esMX"])
def test_a_spanish_client_keeps_spanish(client):
    h = locale_harness(client, saved="esES")

    assert h.lua.globals().BabelChatDB.dict.targetLocale == "esES"


def test_a_language_the_player_chose_is_never_overwritten():
    """Only the old default is corrected. Anything else is a decision."""
    h = locale_harness("ruRU", saved="deDE")

    assert h.lua.globals().BabelChatDB.dict.targetLocale == "deDE"


def test_the_rename_runs_before_the_defaults_are_filled_in():
    """The one ordering the initialiser was extracted from the event handler to
    make testable — and then wasn't tested, so swapping the two lines left the
    whole suite green.

    It is load-bearing: defaults first would see the new key missing, default it
    to `true`, and hand every upgrading player back the categories they had
    switched off.
    """
    harness = AddonHarness()
    lua = harness.lua
    lua.execute(
        "CreateFrame = function()  return setmetatable({}, {__index = function() return function() end end})end"
    )
    lua.execute("SlashCmdList = {}; strtrim = function(s) return (s:gsub('^%s*(.-)%s*$', '%1')) end")
    lua.globals().GetLocale = lambda: "ruRU"
    harness.addon_table.L = lua.eval("setmetatable({}, {__index = function(_, k) return k end})")
    harness.load("Core.lua")
    lua.globals().BabelChatDB = lua.eval(
        '{ dict = { settings = { showMazz = false, showComercio = false, showSocial = true } } }'
    )

    harness.addon_table.InitialiseSavedVariables()
    settings = lua.globals().BabelChatDB.dict.settings

    assert settings.showDungeons is False, "a category the player switched off came back on"
    assert settings.showTrade is False
    assert settings.showSocial is True
    assert settings.showMazz is None, "the old key should be gone, not kept alongside"


# ── labels have to fit the column they sit in ────────────────────────────────

#: The category grid is three columns 190px apart (Config.lua), a checkbox eats
#: about 26px of that, and the label is drawn in GameFontHighlightSmall. What is
#: left holds roughly 26 characters. This is a budget, not a measurement — the
#: harness has no font metrics — but it is calibrated against the label that
#: actually overflowed: "Эндгейм: М+, вылазки, экипировка (22)" at 37, which ran
#: straight through the checkbox in the next column.
CATEGORY_LABEL_BUDGET = 26


def category_labels(locale: str) -> dict[str, str]:
    """Every category label as the options panel will render it, with its count."""
    harness = AddonHarness()
    lua = harness.lua
    lua.globals().GetLocale = lambda: locale
    harness.load("Locales.lua")
    table = harness.addon_table.L
    return {
        str(key): str(table[key])
        for key in ("CAT_DUNGEONS", "CAT_SOCIAL", "CAT_CLASSES", "CAT_ROLES", "CAT_STATS",
                    "CAT_PROFESSIONS", "CAT_COMBAT", "CAT_TRADE", "CAT_GROUPS", "CAT_GUILD",
                    "CAT_STATUS", "CAT_SLANG", "CAT_ENDGAME", "CAT_ZONES", "CAT_SETS")
        if table[key] is not None
    }


@pytest.mark.parametrize("locale", ["enUS", "ruRU", "esES"])
def test_no_category_label_runs_into_the_next_column(locale):
    """The Endgame label read "Эндгейм: М+, вылазки, экипировка (22)" and
    overlapped the checkbox beside it. A label is a name; what the category
    covers belongs in the tooltip, which already existed and was duplicating it.
    """
    # Four more characters for the " (NN)" the panel appends.
    too_long = {
        key: (text, len(text) + 5)
        for key, text in category_labels(locale).items()
        if len(text) + 5 > CATEGORY_LABEL_BUDGET
    }

    assert too_long == {}, f"{locale}: these will overlap the next column: {too_long}"


@pytest.mark.parametrize("locale", ["enUS", "ruRU", "esES"])
def test_every_category_has_a_tooltip_that_says_more_than_its_label(locale):
    """Shortening the labels is only safe if the explanation is somewhere. It is
    the tooltip's whole job, and a tooltip that merely repeats the label is not
    doing it."""
    harness = AddonHarness()
    harness.lua.globals().GetLocale = lambda: locale
    harness.load("Locales.lua")
    table = harness.addon_table.L

    lazy = []
    for key in category_labels(locale):
        tooltip = table["TT_" + key]
        if tooltip is None or len(str(tooltip)) <= len(str(table[key])):
            lazy.append(key)

    assert lazy == [], f"{locale}: no useful tooltip for {lazy}"


# ── the About section ────────────────────────────────────────────────────────


def link_boxes(harness) -> dict[str, str]:
    """The addresses the panel offers, by the EditBox that holds each."""
    return {
        frame.name: str(frame.text)
        for frame in built_frames(harness)
        if frame.name and str(frame.name).startswith("WCT_Link")
    }


def test_the_panel_offers_the_three_places_the_addon_lives(panel):
    """A player who wants to report something, rate it, or check for an update
    has nowhere to look otherwise — a WoW addon cannot open a browser, so the
    address has to be in front of them to copy."""
    addresses = set(link_boxes(panel).values())

    assert any("github.com/Yumash/BabelChat" in url for url in addresses), addresses
    assert any("curseforge.com" in url for url in addresses), addresses
    assert any("wago.io" in url for url in addresses), addresses


def test_every_box_holds_something_that_can_be_pasted_as_it_stands(panel):
    """A truncated or relative address is worse than none: it looks copyable and
    goes nowhere. Web addresses must be complete URLs; a wallet address is not a
    URL but must still be whole and free of stray whitespace, because it is
    pasted into a payment form where a typo loses the money."""
    bad = {}
    for name, value in link_boxes(panel).items():
        if value != value.strip() or " " in value:
            bad[name] = f"whitespace in {value!r}"
        elif "." in value.split("/")[0] and not value.startswith("https://"):
            bad[name] = f"looks like a URL but is not https: {value!r}"
        elif not value.startswith("https://") and len(value) < 26:
            bad[name] = f"too short to be a wallet address: {value!r}"

    assert bad == {}, f"not usable as it stands: {bad}"


def test_the_panel_offers_a_way_to_support_the_work(panel):
    """Asked for explicitly, and the card link is the one most people can
    actually use — the wallets are for those who prefer them."""
    values = set(link_boxes(panel).values())

    assert any("pay.cloudtips.ru" in value for value in values), values
    assert sum(1 for value in values if not value.startswith("https://")) >= 3, "the wallets are missing"


@pytest.mark.parametrize("locale", ["enUS", "ruRU", "esES"])
def test_the_about_section_credits_both_the_authors_and_the_glossary(locale):
    """The glossary is Pirson's under MIT. Crediting it only in a file nobody
    opens is not crediting it."""
    harness = AddonHarness()
    harness.lua.globals().GetLocale = lambda: locale
    harness.load("Locales.lua")
    table = harness.addon_table.L

    authors = str(table["ABOUT_AUTHORS"])
    credit = str(table["ABOUT_DICT_CREDIT"])

    assert "AhegaoZKun" in authors, authors
    assert "Pirson" in credit, credit
    assert "MIT" in credit, credit
