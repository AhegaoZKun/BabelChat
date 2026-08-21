-- BabelChat Commands.lua
-- Everything a player invokes by hand: the slash commands, the self test and
-- the first-run welcome frame.
--
-- Split out of Core.lua, which exists to wire the addon together at load and
-- had grown past the point where that was still what it looked like.
local ADDON_NAME, addonTable = ...
local L = addonTable.L

-- The two states every status line reports, coloured once here rather than
-- spelled out in English at each call site.
local ON = "|cFF40FF40" .. L["CMD_ON"] .. "|r"
local OFF = "|cFFFF4040" .. L["CMD_OFF"] .. "|r"

local function Print(msg)
    DEFAULT_CHAT_FRAME:AddMessage("|cff9482c9BabelChat|r: " .. msg)
end

-- ==========================================
-- SLASH COMMANDS: /babel
-- ==========================================
SLASH_BABELCHAT1 = "/babel"
SlashCmdList["BABELCHAT"] = function(msg)
    local command = strtrim(msg):lower()
    local db = BabelChatDB

    if command == "config" or command == "settings" then
        if Settings and Settings.OpenToCategory and addonTable.categoryID then
            Settings.OpenToCategory(addonTable.categoryID)
        else
            Print(L["CMD_NO_PANEL"])
        end

    elseif command == "on" then
        db.dict.enabled = true
        Print(L["SLASH_ON"])

    elseif command == "off" then
        db.dict.enabled = false
        Print(L["SLASH_OFF"])

    elseif command == "test" then
        addonTable.RunTest()

    elseif command == "companion" or command == "buf" then
        local count, seq, limit, flushing = addonTable.GetBufferStatus()
        Print(L["CMD_BUFFER"])
        Print("  " .. L["CMD_MESSAGES"] .. ": " .. count .. "/" .. limit)
        Print("  " .. L["CMD_SEQ"] .. ": " .. seq)
        Print("  " .. L["CMD_FLUSH"] .. ": " .. (flushing and ON or OFF))
        Print("  " .. L["CMD_POLL"] .. ": " .. (addonTable.IsPollActive() and ON or OFF))

    elseif command == "poll on" then
        addonTable.StartPollTimer()
        Print("|cFF40FF40" .. L["CMD_POLL_ON"] .. "|r")

    elseif command == "poll off" then
        addonTable.StopPollTimer()
        Print("|cFFFF4040" .. L["CMD_POLL_OFF"] .. "|r")

    elseif command == "log on" then
        if not LoggingChat() then LoggingChat(true) end
        addonTable.StartLogFlush(db.companion.flushInterval)
        Print("|cFF40FF40" .. L["CMD_LOG_ON"] .. "|r")

    elseif command == "log off" then
        addonTable.StopLogFlush()
        if LoggingChat() then LoggingChat(false) end
        Print("|cFFFF4040" .. L["CMD_LOG_OFF"] .. "|r")

    else
        Print("|cffd597ff" .. L["HELP_HEADER"] .. "|r")
        Print("|cffffff00/babel config|r - " .. L["HELP_CONFIG_MSG"])
        Print("|cffffff00/babel on|off|r - " .. L["HELP_ONOFF_MSG"])
        Print("|cffffff00/babel test|r - " .. L["HELP_TEST_MSG"])
        Print("|cffffff00/babel companion|r - " .. L["HELP_COMPANION_MSG"])
        Print("|cffffff00/babel poll on|off|r - " .. L["HELP_POLL_MSG"])
        Print("|cffffff00/babel log on|off|r - " .. L["HELP_LOG_MSG"])
    end
end

-- ==========================================
-- TEST FUNCTION
-- ==========================================
function addonTable.RunTest()
    local testMsg = "LFM ICC HC 25m Need Tank and Healer"
    -- Forced: the point of the test is to show what the dictionary knows, and
    -- reporting "no match" because the companion happens to be running names
    -- the wrong cause. If the gloss is suppressed, say so separately.
    local translated, changed = addonTable.TranslateChat(testMsg, true)

    Print("|cffffff00" .. L["SLASH_TEST_ORIGINAL"] .. "|cffffffff" .. testMsg .. "|r")

    local db = BabelChatDB
    if not db.dict.enabled then
        Print("|cffff0000" .. L["SLASH_TEST_ERROR"] .. "|r")
        return
    end
    if not changed then
        Print("|cffff0000" .. L["TEST_NO_MATCH"] .. "|r")
        return
    end

    Print("|cffffff00" .. L["SLASH_TEST_RESULT"] .. "|cffffffff" .. translated .. "|r")
    if addonTable.ShouldSuppressGloss() then
        Print("|cffaaaaaa" .. L["TEST_SUPPRESSED"] .. "|r")
    end
end

-- ==========================================
-- WELCOME FRAME (first run popup)
-- ==========================================
function addonTable.ShowWelcomeFrame()
    if addonTable.welcomeFrame then
        addonTable.welcomeFrame:Show()
        return
    end

    local frame = CreateFrame("Frame", "BabelChatWelcomeFrame", UIParent, "BasicFrameTemplateWithInset")
    frame:SetSize(440, 380)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:SetFrameStrata("DIALOG")
    frame.TitleBg:SetHeight(30)

    -- Title
    frame.title = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightLarge")
    frame.title:SetPoint("TOP", frame.TitleBg, "TOP", 0, -3)
    frame.title:SetText("BabelChat")

    -- Body text
    local body = frame.InsetBg or frame.Inset
    local text = frame:CreateFontString(nil, "ARTWORK", "GameFontNormal")
    text:SetPoint("TOPLEFT", frame, "TOPLEFT", 18, -60)
    text:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -18, -60)
    text:SetJustifyH("LEFT")
    text:SetSpacing(4)

    -- Strip color codes for clean display, re-apply manually
    local lines = {
        L["WELCOME_1"],
        "",
        L["WELCOME_2"],
        "",
        L["WELCOME_3"],
        "",
        L["WELCOME_4"],
        L["WELCOME_5"],
        L["WELCOME_6"],
    }
    text:SetText(table.concat(lines, "\n"))

    -- Settings button
    local settingsBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    settingsBtn:SetSize(120, 26)
    settingsBtn:SetPoint("BOTTOMRIGHT", frame, "BOTTOM", -4, 14)
    settingsBtn:SetText(L["WELCOME_SETTINGS"])
    settingsBtn:SetScript("OnClick", function()
        frame:Hide()
        if Settings and Settings.OpenToCategory and addonTable.categoryID then
            Settings.OpenToCategory(addonTable.categoryID)
        end
    end)

    -- OK button
    local okBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    okBtn:SetSize(120, 26)
    okBtn:SetPoint("BOTTOMLEFT", frame, "BOTTOM", 4, 14)
    okBtn:SetText(L["WELCOME_OK"])
    okBtn:SetScript("OnClick", function()
        frame:Hide()
    end)

    addonTable.welcomeFrame = frame
    frame:Show()
end
