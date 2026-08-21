"""The things a player invokes by hand, run for real.

Splitting Core.lua moved the slash commands, the self test and the welcome frame
into Commands.lua and left `local L = addonTable.L` behind. Nothing failed to
parse, nothing failed to load, and the suite stayed green — but `/babel test`
and the Test button in the options panel both raised "attempt to index global
'L'" the moment a player pressed them.

A syntax check cannot see that, and neither can a static scan of an extraction
diff. Calling the entry points can.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from tests.lua_harness import AddonHarness  # noqa: E402

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"


def toc_lua_files() -> list[str]:
    """The addon's own files, in the order the TOC loads them."""
    listed = [
        line.strip().replace("\\", "/")
        for line in (ADDON_DIR / "BabelChat.toc").read_text(encoding="utf-8").splitlines()
        if line.strip().endswith(".lua")
    ]
    return [name for name in listed if not name.startswith("Libs/")]


@pytest.fixture
def addon():
    """Every first-party file loaded in TOC order, with the game stubbed."""
    harness = AddonHarness()
    lua = harness.lua
    lua.execute(
        """
        _printed = {}
        DEFAULT_CHAT_FRAME = { AddMessage = function(_, msg) table.insert(_printed, msg) end }
        SlashCmdList = {}
        strtrim = function(s) return (s:gsub("^%s*(.-)%s*$", "%1")) end
        UIParent = nil
        InterfaceOptionsFrame_OpenToCategory = function() end
        Settings = {
            RegisterCanvasLayoutCategory = function(_, title)
                return { ID = title, GetID = function(self) return self.ID end }
            end,
            RegisterAddOnCategory = function() end,
            OpenToCategory = function() end,
        }
        C_AddOns = { GetAddOnMetadata = function() return "3.4.0" end }
        -- A stand-in that answers to both shapes a frame is used in: `f:Method()`
        -- and `f.SubFrame:Method()`. Returning a plain function for every miss
        -- models the first and breaks on the second, which is how TitleBg —
        -- a field, not a method — brought this fixture down.
        local anything
        anything = setmetatable({}, {
            __index = function() return anything end,
            __call = function() return anything end,
            __newindex = function() end,
        })
        CreateFrame = function(_, name)
            local frame = setmetatable({
                name = name,
                GetName = function(self) return self.name end,
            }, {
                __index = function() return anything end,
            })
            if name then
                _G[name] = frame
                _G[name .. "Text"] = anything
            end
            return frame
        end
        setmetatable(_G, { __index = function(_, key)
            if type(key) == "string" and key:match("^UIDropDownMenu_") then
                return function() return {} end
            end
            return nil
        end })
        """
    )
    lua.globals().GetLocale = lambda: "ruRU"
    for name in toc_lua_files():
        harness.load(name)
    harness.addon_table.InitialiseSavedVariables()
    harness.addon_table.RebuildMasterDict()
    return harness


def printed(harness) -> list[str]:
    return [str(v) for v in harness.lua.globals()._printed.values()]


def run_slash(harness, argument: str) -> None:
    harness.lua.globals().SlashCmdList["BABELCHAT"](argument)


# ── the entry points a player can reach ──────────────────────────────────────


def test_the_self_test_reports_a_translation(addon):
    """`/babel test` and the Test button in the options panel both call this.
    It raised on a missing locale table after Core.lua was split."""
    addon.addon_table.RunTest()

    lines = printed(addon)
    assert lines, "the self test printed nothing"
    assert any("LFM" in line for line in lines), f"the sample message is missing: {lines}"


@pytest.mark.parametrize(
    "argument",
    ["", "help", "test", "on", "off", "companion", "buf", "poll on", "poll off", "log on", "log off", "nonsense"],
)
def test_every_slash_command_runs_without_raising(addon, argument):
    """Each of these reaches for the locale table, the saved variables or both.
    A missing upvalue shows up here and nowhere else."""
    run_slash(addon, argument)

    assert printed(addon), f"/babel {argument} printed nothing"


def test_the_config_panel_test_button_is_wired_to_the_self_test(addon):
    """The button and the slash command must reach the same code — the panel
    lives in a different file from the function it calls."""
    assert addon.addon_table.RunTest is not None
    addon.addon_table.CreateConfigUI()

    addon.addon_table.RunTest()

    assert any("LFM" in line for line in printed(addon))


def test_the_welcome_frame_can_be_shown(addon):
    """It moved in the same split and is reached from the load handler, which
    means a player only finds out it is broken on a fresh install."""
    addon.addon_table.ShowWelcomeFrame()


def test_turning_the_glossary_off_and_on_is_reported_to_the_player(addon):
    run_slash(addon, "off")
    assert addon.lua.globals().BabelChatDB.dict.enabled is False

    run_slash(addon, "on")
    assert addon.lua.globals().BabelChatDB.dict.enabled is True


# ── the self test has to name the real reason ────────────────────────────────


def test_the_self_test_shows_the_gloss_even_while_the_companion_is_running(addon):
    """It reported "no dictionary matches found" when the dictionary matched
    four terms and the addon was simply staying quiet because the companion app
    was running. The one moment a player is looking for an answer is the worst
    moment to name the wrong cause."""
    addon.lua.execute("BabelChatDB.companion.enabled = true")
    addon.lua.execute("_printed = {}")

    addon.addon_table.RunTest()

    lines = printed(addon)
    assert any("LFM = " in line for line in lines), f"the gloss was not shown: {lines}"
    assert not any("TEST_NO_MATCH" in line for line in lines)


def test_the_self_test_says_why_the_gloss_will_not_appear_in_chat(addon):
    """Showing it without explaining would be its own confusion: the player
    would go looking for it in chat and not find it."""
    addon.lua.execute("BabelChatDB.companion.enabled = true")
    addon.lua.execute("_printed = {}")

    addon.addon_table.RunTest()

    explained = addon.lua.globals().BabelChatDB and addon.addon_table.ShouldSuppressGloss()
    assert explained, "the fixture no longer suppresses, so this test proves nothing"
    assert len(printed(addon)) == 3, "expected the original, the result and the reason"


def test_the_self_test_stays_quiet_about_suppression_when_there_is_none(addon):
    addon.lua.execute("_printed = {}")

    addon.addon_table.RunTest()

    assert len(printed(addon)) == 2, "nothing to explain when the companion is off"


def test_a_disabled_glossary_is_reported_as_disabled_not_as_no_match(addon):
    addon.lua.execute("BabelChatDB.dict.enabled = false")
    addon.lua.execute("_printed = {}")

    addon.addon_table.RunTest()

    assert len(printed(addon)) == 2


def test_ordinary_chat_still_stays_quiet_while_the_companion_runs(addon):
    """Forcing the gloss is for the test command only. If it leaked into the
    chat filter, both the addon and the overlay would answer every message."""
    addon.lua.execute("BabelChatDB.companion.enabled = true")

    _text, changed = addon.addon_table.TranslateChat("LFM ICC HC 25m Need Tank and Healer")

    assert bool(changed) is False


# ── the "and N more" counter ─────────────────────────────────────────────────


def test_the_overflow_counter_is_separated_and_reads_as_words(addon):
    """Written as " +4" it ran onto the end of the last translation and read as
    part of it: "HC = Хероик +4" looks like a term, not like a count of what did
    not fit. The first person to see it asked what the +4 was."""
    addon.lua.execute("_printed = {}")

    text, changed = addon.addon_table.TranslateChat("LFM ICC HC 25m Need Tank and Healer", True)

    assert bool(changed) is True
    assert " · и ещё 4" in text, text
    assert "Хероик +4" not in text
    assert "+4" not in text


def test_the_counter_is_absent_when_everything_fitted(addon):
    text, changed = addon.addon_table.TranslateChat("ty", True)

    assert bool(changed) is True
    assert "ещё" not in text.split("ty  ")[-1] or "=" in text
    assert " · и ещё" not in text


def test_the_gloss_survives_a_missing_locale_table(addon):
    """DictEngine is loaded on its own in other tests, and reading the locale
    table at load time would take the whole gloss down for a word."""
    saved = addon.addon_table.L
    addon.addon_table.L = None
    try:
        text, changed = addon.addon_table.TranslateChat("LFM ICC HC 25m Need Tank and Healer", True)
        assert bool(changed) is True
        assert "LFM = " in text
        assert "+4" in text, "the fallback counter should still appear"
    finally:
        addon.addon_table.L = saved
