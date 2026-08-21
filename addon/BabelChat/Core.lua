-- Core.lua — BabelChat v2.2.2
-- Thin init: wires DictEngine + CompanionBuffer + ChatFilter + slash commands.
-- Dictionary translation inline in chat + memory buffer for companion overlay.

local ADDON_NAME, addonTable = ...
local L = addonTable.L

local PREFIX = "|cffffff00[|r|cffd597ffBabelChat|r|cffffff00]|r "

local function Print(msg)
    print(PREFIX .. msg)
end

-- ==========================================
-- DEFAULT DATABASE
-- ==========================================
local DEFAULTS = {
    -- Dictionary settings (from Pirson's WoWTranslator)
    dict = {
        enabled = true,
        -- Filled in from the client locale on first run; see
        -- AutoDetectLocale. Left unset here on purpose: a shipped
        -- default is a language chosen for somebody else.
        targetLocale = false,
        -- When to print the in-chat gloss: "auto" keeps out of the way
        -- when the companion app is set up, because the overlay already
        -- shows a full translation of the same line.
        mode = "auto",
        -- Grey, not the old bright green: this is now a short aside at the
        -- end of the line rather than a translation on its own row, and it
        -- should sit behind the message rather than compete with it. An
        -- existing choice is left alone — it is the player's setting.
        chatColor = "808080",
        settings = {
            showDungeons = true,
            showSocial = true,
            showClasses = true,
            showCombat = true,
            showTrade = true,
            showStats = true,
            showGroups = true,
            showGuild = true,
            showProfessions = true,
            showRoles = true,
            showStatus = true,
            showSlang = true,
            showEndgame = true,
            showZones = true,
            showSets = true,
            skipSameLanguage = true,
            channels = {}
        }
    },
    -- Companion app settings (disabled by default — addon works standalone)
    companion = {
        enabled = false,
        autoLog = false,
        verbose = false,
        flushInterval = 5,
        pollFallback = false,
    },
    -- First run flag
    firstRun = true,
    -- Minimap icon
    minimap = {},
}

-- ==========================================
-- SETTING KEY MIGRATION
-- ==========================================
-- The category toggles were named after the Spanish source this dictionary came
-- from — showMazz (mazmorras), showClases, showComercio — which meant nobody
-- reading their own SavedVariables could tell what they controlled.
--
-- Renaming them without moving the values would silently re-enable every
-- category a player had switched off: the old key stops being read, the new one
-- is absent, and ApplyDefaults fills it with `true`.
addonTable.SETTING_RENAMES = {
    showMazz        = "showDungeons",
    showClases      = "showClasses",
    showCombate     = "showCombat",
    showComercio    = "showTrade",
    showGrupos      = "showGroups",
    showHermandad   = "showGuild",
    showEstado      = "showStatus",
    showProfesiones = "showProfessions",
}

-- Returns how many values it moved, which makes the migration testable and
-- makes "it ran and found nothing" distinguishable from "it did not run".
function addonTable.MigrateSettingKeys(settings)
    if type(settings) ~= "table" then return 0 end
    local moved = 0
    for old, new in pairs(addonTable.SETTING_RENAMES) do
        if settings[old] ~= nil then
            -- A value already under the new name wins: it is what the player
            -- last chose in a version that used it. Running this twice is
            -- therefore a no-op, which matters because it runs on every load.
            if settings[new] == nil then
                settings[new] = settings[old]
                moved = moved + 1
            end
            settings[old] = nil
        end
    end
    return moved
end

-- Deep merge defaults into db (non-destructive)
local function ApplyDefaults(db, defaults)
    for k, v in pairs(defaults) do
        if type(v) == "table" then
            if db[k] == nil then db[k] = {} end
            if type(db[k]) == "table" then
                ApplyDefaults(db[k], v)
            end
        elseif db[k] == nil then
            db[k] = v
        end
    end
end

-- Which language the gloss is written in. Taken from the client on first run.
--
-- This used to compare against "enUS" while the shipped default was "esES" — a
-- leftover from the Spanish addon this dictionary came from — so the check
-- never fired and a Russian player's gloss came out in Spanish. Nobody would
-- report that as a bug in locale detection; they would report that the
-- dictionary is wrong.
local function AutoDetectLocale()
    local db = BabelChatDB
    local clientLocale = GetLocale()
    -- enGB clients read the enUS tables; nothing ships a separate enGB column.
    if clientLocale == "enGB" then clientLocale = "enUS" end

    -- Anyone who saved a config before this shipped carries "esES", because
    -- that was the default. On a Spanish client that is a real choice and is
    -- left alone; anywhere else it is the old default, and it is the reason
    -- their gloss has been coming out in a language they do not read.
    if db.dict.targetLocale == "esES" and clientLocale ~= "esES" and clientLocale ~= "esMX" then
        db.dict.targetLocale = nil
    end

    if db.dict.targetLocale then return end
    db.dict.targetLocale = clientLocale or "enUS"
end

-- A chat argument that is safe to treat as text.
--
-- Under chat messaging lockdown these arrive as secret values: they report as
-- strings and raise on string.len. The type check comes first because
-- string.len(42) succeeds — numbers coerce — so a length probe alone waves a
-- number through to a caller that then indexes it as a string.
local function IsUsableString(value)
    local ok, kind = pcall(type, value)
    if not ok or kind ~= "string" then return false end
    return (pcall(string.len, value))
end

-- ==========================================
-- CHAT EVENT FILTER (dual-path)
-- ==========================================
local CHAT_EVENTS = {
    "CHAT_MSG_SAY", "CHAT_MSG_YELL",
    "CHAT_MSG_WHISPER", "CHAT_MSG_WHISPER_INFORM",
    "CHAT_MSG_BN_WHISPER",
    "CHAT_MSG_PARTY", "CHAT_MSG_PARTY_LEADER",
    "CHAT_MSG_RAID", "CHAT_MSG_RAID_LEADER", "CHAT_MSG_RAID_WARNING",
    "CHAT_MSG_INSTANCE_CHAT", "CHAT_MSG_INSTANCE_CHAT_LEADER",
    "CHAT_MSG_GUILD", "CHAT_MSG_OFFICER",
    "CHAT_MSG_CHANNEL", "CHAT_MSG_EMOTE",
    "CHAT_MSG_BATTLEGROUND", "CHAT_MSG_BATTLEGROUND_LEADER",
}

local function ChatFilter(self, event, text, author, ...)
    local db = BabelChatDB
    if not db then return end

    -- Strip CHAT_MSG_ prefix for compact event name
    local shortEvent = event:gsub("^CHAT_MSG_", "")

    -- For public channels, send the channel's TYPE id alongside its name.
    --
    -- CHAT_MSG_CHANNEL is (text, author, lang, channelString, author2, flags,
    -- zoneChannelID, channelIndex, channelBaseName, ...). `...` starts at arg3,
    -- so channelString is select(2, ...) and zoneChannelID is select(5, ...).
    --
    -- The name alone was the bug: the companion matched it against English
    -- words, so on a Russian client "Торговля" matched nothing and every public
    -- channel — Trade included — was filed as General. zoneChannelID is the
    -- same number on every locale, and it is 0 for a player-made channel, which
    -- is exactly the distinction that was missing.
    --
    -- Encoded as "CHANNEL:<id>:<name>". An older addon sends "CHANNEL:<name>",
    -- which the companion still understands.
    if event == "CHAT_MSG_CHANNEL" then
        local channelString = select(2, ...)
        local zoneChannelID = select(5, ...)
        -- Both are chat event arguments, so under chat messaging lockdown they
        -- are secret values, and every test below — truthiness, comparison,
        -- gsub — is an operation a secret rejects. Probe before touching them.
        --
        -- The type check is not redundant with the length probe: string.len(42)
        -- SUCCEEDS in Lua 5.1, because numbers coerce. A length probe alone
        -- waves a number through and the gsub below then raises on it, which
        -- would take the chat filter down for the rest of the session.
        if IsUsableString(channelString) then
            -- Strip a leading "N. " channel-number prefix if present.
            local name = channelString:gsub("^%d+%.%s*", "")
            if name ~= "" then
                -- Zero means "a player made this channel" and the companion
                -- treats it as such, so it must never stand in for "we could
                -- not read the id". When the id is unreadable, send the older
                -- two-part form and let the companion fall back to the name.
                local channelType
                if IsUsableString(zoneChannelID) or type(zoneChannelID) == "number" then
                    channelType = tonumber(zoneChannelID)
                end
                if channelType then
                    shortEvent = "CHANNEL:" .. channelType .. ":" .. name
                else
                    shortEvent = "CHANNEL:" .. name
                end
            end
        end
    end

    -- Dictionary translation (if enabled and channel not filtered)
    local translated, wasChanged
    local dictChannelEnabled = not db.dict.settings.channels or db.dict.settings.channels[event] ~= false
    if db.dict.enabled and dictChannelEnabled then
        -- pcall for safety (secret values in instance chat)
        local ok, t, c = pcall(addonTable.TranslateChat, text)
        if ok then
            translated, wasChanged = t, c
        end
    end

    -- Buffer for companion app — ALL channels, regardless of dict filter.
    -- Wrapped in pcall for the same reason TranslateChat is: this runs inside a
    -- chat event filter, and an error escaping here does not just lose one
    -- message — it breaks the filter for every chat line that follows, for the
    -- whole encounter. BufferAddEntry probes its own arguments, so this is the
    -- second layer, not the first.
    if wasChanged then
        pcall(addonTable.BufferAddEntry, text, "DICT", shortEvent, author)
    else
        pcall(addonTable.BufferAddEntry, text, "RAW", shortEvent, author)
    end

    -- Return modified text for inline chat display
    if wasChanged then
        return false, translated, author, ...
    end
end

-- ==========================================
-- INITIALIZATION
-- ==========================================
local initFrame = CreateFrame("Frame")
initFrame:RegisterEvent("PLAYER_LOGIN")

-- Everything that has to happen to BabelChatDB before anything reads it, in
-- the one order that is correct: adopt the old addon's table if that is all
-- the player has, rename the Spanish-derived keys, and only then fill in
-- defaults. The other order sees the new keys missing, defaults them to
-- `true`, and hands every player back the categories they had switched off.
--
-- Exported rather than left inline in the event handler so it can run without
-- a game underneath it: that ordering is the part most worth a test, and it
-- was unreachable while it lived inside OnEvent.
function addonTable.InitialiseSavedVariables()
    -- Migrate from old ChatTranslatorHelper if present
    if ChatTranslatorHelperDB and not BabelChatDB then
        BabelChatDB = ChatTranslatorHelperDB
        Print("|cFF40FF40Migrated settings from ChatTranslatorHelper.|r")
    end

    -- Initialize database
    if not BabelChatDB then
        BabelChatDB = {}
    end
    -- Rename the Spanish-derived setting keys BEFORE defaults are filled in.
    -- The other order would see the new keys missing, default them to `true`,
    -- and hand every player back the categories they had switched off.
    if BabelChatDB.dict and BabelChatDB.dict.settings then
        addonTable.MigrateSettingKeys(BabelChatDB.dict.settings)
    end
    ApplyDefaults(BabelChatDB, DEFAULTS)

    -- Initialize default channel states
    local db = BabelChatDB
    for _, e in ipairs(CHAT_EVENTS) do
        if db.dict.settings.channels[e] == nil then
            db.dict.settings.channels[e] = true
        end
    end

    AutoDetectLocale()
    return db
end

-- Exported so a test can call it: the token it builds for a public channel
-- is what the companion classifies on, and getting it wrong files a real
-- Trade channel as a private one.
addonTable.ChatFilter = ChatFilter

initFrame:SetScript("OnEvent", function(self, event)
    local db = addonTable.InitialiseSavedVariables()

    -- Pre-allocate companion keys (pointer stability)
    addonTable.PreallocateCompanionKeys()

    -- Initialize LibBabble
    addonTable.InitLibBabble()

    -- Build master dictionary
    addonTable.RebuildMasterDict()

    -- Register chat event filter for all channels
    for _, e in ipairs(CHAT_EVENTS) do
        ChatFrame_AddMessageEventFilter(e, ChatFilter)
    end

    -- Start companion buffer flush
    if db.companion.enabled then
        addonTable.StartBufferFlush()
    end

    -- Start chat log flush if auto-logging enabled
    if db.companion.autoLog then
        if not LoggingChat() then LoggingChat(true) end
        addonTable.StartLogFlush(db.companion.flushInterval)
    end

    -- Start poll fallback if explicitly enabled
    if db.companion.pollFallback then
        addonTable.StartPollTimer()
    end

    -- Create config UI
    addonTable.CreateConfigUI()

    -- Minimap button
    local LDB = LibStub("LibDataBroker-1.1", true)
    local LDBIcon = LibStub("LibDBIcon-1.0", true)
    if LDB and LDBIcon then
        local dataObject = LDB:NewDataObject("BabelChat", {
            type = "launcher",
            icon = "Interface\\AddOns\\BabelChat\\img\\icon",
            OnClick = function()
                if Settings and Settings.OpenToCategory and addonTable.categoryID then
                    Settings.OpenToCategory(addonTable.categoryID)
                end
            end,
            OnTooltipShow = function(tooltip)
                tooltip:AddLine(L["QT_MINIMAP_TT"] or "Click: Open Settings")
            end,
        })
        if not LDBIcon:IsRegistered("BabelChat") then
            LDBIcon:Register("BabelChat", dataObject, db.minimap)
        end
    end

    -- First run: show welcome popup
    if db.firstRun then
        db.firstRun = false
        C_Timer.After(3, function()
            addonTable.ShowWelcomeFrame()
        end)
    else
        Print(L["CHAT_LOADED"])
    end
end)
