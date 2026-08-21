-- BabelChat Commands.lua
-- Everything a player invokes by hand: the slash commands, the self test and
-- the first-run welcome frame.
--
-- Split out of Core.lua, which exists to wire the addon together at load and
-- had grown past the point where that was still what it looked like.
local ADDON_NAME, addonTable = ...

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
            Print("Settings panel not available. Use the game's AddOn settings.")
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
        Print("Companion buffer:")
        Print("  Messages: " .. count .. "/" .. limit)
        Print("  Seq: " .. seq)
        Print("  Flush: " .. (flushing and "|cFF40FF40ON|r" or "|cFFFF4040OFF|r"))
        Print("  Poll fallback: " .. (addonTable.IsPollActive() and "|cFF40FF40ON|r" or "|cFFFF4040OFF|r"))

    elseif command == "poll on" then
        addonTable.StartPollTimer()
        Print("Poll fallback |cFF40FF40enabled|r.")

    elseif command == "poll off" then
        addonTable.StopPollTimer()
        Print("Poll fallback |cFFFF4040disabled|r.")

    elseif command == "log on" then
        if not LoggingChat() then LoggingChat(true) end
        addonTable.StartLogFlush(db.companion.flushInterval)
        Print("Chat logging |cFF40FF40enabled|r.")

    elseif command == "log off" then
        addonTable.StopLogFlush()
        if LoggingChat() then LoggingChat(false) end
        Print("Chat logging |cFFFF4040disabled|r.")

    else
        Print("|cffd597ff" .. L["HELP_HEADER"] .. "|r")
        Print("|cffffff00/babel config|r - " .. L["HELP_CONFIG_MSG"])
        Print("|cffffff00/babel on|off|r - " .. L["HELP_ONOFF_MSG"])
        Print("|cffffff00/babel test|r - " .. L["HELP_TEST_MSG"])
        Print("|cffffff00/babel companion|r - " .. L["HELP_COMPANION_MSG"])
        Print("|cffffff00/babel poll on|off|r - Toggle GetMessageInfo fallback")
        Print("|cffffff00/babel log on|off|r - Toggle chat file logging")
    end
end

-- ==========================================
-- TEST FUNCTION
-- ==========================================
function addonTable.RunTest()
    local testMsg = "LFM ICC HC 25m Need Tank and Healer"
    local translated, changed = addonTable.TranslateChat(testMsg)

    Print("|cffffff00" .. L["SLASH_TEST_ORIGINAL"] .. "|cffffffff" .. testMsg .. "|r")
    if changed then
        Print("|cffffff00" .. L["SLASH_TEST_RESULT"] .. "|cffffffff" .. translated .. "|r")
    else
        local db = BabelChatDB
        local errorStr = (not db.dict.enabled) and L["SLASH_TEST_ERROR"] or L["TEST_NO_MATCH"]
        Print("|cffff0000" .. errorStr .. "|r")
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
