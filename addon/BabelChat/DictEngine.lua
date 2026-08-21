-- DictEngine.lua — Dictionary gloss for chat
-- Based on WoW Translator by Pirson (CurseForge project 1431567)
--
-- What this is for. The dictionary holds ~380 gaming terms; it cannot translate
-- a sentence and should not pretend to. Its useful job is the part a sentence
-- translator gets WRONG — brez, gilded, soak, lf1m — so the gloss is short, in
-- reading order, and appended to the line rather than doubling its height.
--
-- What it used to produce, for one message with three matches:
--
--     Foo: ty ty ty for the run
--        → ty → Спасибо/спс, ty → Спасибо/спс, ty → Спасибо/спс
--
-- Three problems in one line: the same term three times, the same arrow glyph
-- meaning both "annotation follows" and "maps to", and a newline that doubles
-- every glossed message and breaks copy-chat. Now:
--
--     Foo: ty ty ty for the run  ty = спасибо

local ADDON_NAME, addonTable = ...

-- ==========================================
-- MASTER DICTIONARY & LOOKUP TABLES
-- ==========================================
local MasterDict = {}        -- single word (lower) → translation
local PhraseIndex = {}       -- first word (lower) → { {lowerPhrase, translation}, ... }
-- LibBabble (zones / item sets) keyed the same way. The old build kept a flat
-- list of ~11,900 entries and ran string.find over every one of them for every
-- chat line, three times over when three chat frames showed the message. In a
-- busy Trade chat that is hundreds of thousands of scans a second.
local BabbleIndex = {}

local ipairs, pairs, type = ipairs, pairs, type
local string_format, string_gsub, string_lower = string.format, string.gsub, string.lower
local string_sub, string_len = string.sub, string.len
local table_insert, table_sort, table_concat = table.insert, table.sort, table.concat

-- How many pairs to show before collapsing the rest into a count. Four glossed
-- terms on one chat line is already more than anyone reads.
local MAX_PAIRS = 3

-- LibBabble references (set on init)
local BZ, BI

function addonTable.InitLibBabble()
    BZ = LibStub("LibBabble-SubZone-3.0", true) and LibStub("LibBabble-SubZone-3.0"):GetUnstrictLookupTable()
    BI = LibStub("LibBabble-ItemSet-3.0", true) and LibStub("LibBabble-ItemSet-3.0"):GetUnstrictLookupTable()
end

-- ==========================================
-- HELPERS
-- ==========================================

-- Dictionary values often list alternatives: "Спасибо/спс", "Сорян/извини".
-- That is a lexicographer's note, not a translation, and printing it verbatim
-- is a large part of why the gloss read as sloppy. Show the first.
local function FirstAlternative(value)
    local slash = value:find("/", 1, true)
    return slash and value:sub(1, slash - 1) or value
end

local function FirstWord(phrase)
    return phrase:match("^[^%s]+") or phrase
end

-- A byte is part of a word if a match may not start or end next to it. Lua
-- patterns are byte-based and %w is ASCII-only, so Cyrillic bytes (>= 128) are
-- treated as word characters explicitly — otherwise "ты" would count as a
-- boundary and every Russian word would match inside its neighbours.
local function IsWordByte(byte)
    if not byte then return false end
    if byte >= 128 then return true end
    local char = string.char(byte)
    return char:match("[%w']") ~= nil
end

local function HasWordBoundaries(text, startPos, endPos)
    return not IsWordByte(text:byte(startPos - 1)) and not IsWordByte(text:byte(endPos + 1))
end

-- ==========================================
-- BUILD MASTER DICTIONARY FROM DATA FILES
-- ==========================================
function addonTable.RebuildMasterDict()
    MasterDict = {}
    PhraseIndex = {}
    BabbleIndex = {}

    local db = BabelChatDB
    if not db or not db.dict then return end

    local map = {
        { key = "showDungeons",   dict = addonTable.MazzRaidDict },
        { key = "showSocial",     dict = addonTable.SocialDict },
        { key = "showClasses",    dict = addonTable.ClasesDict },
        { key = "showCombat",     dict = addonTable.CombateDict },
        { key = "showTrade",      dict = addonTable.ComercioDict },
        { key = "showStats",      dict = addonTable.EstadisticasDict },
        { key = "showGroups",     dict = addonTable.GruposDict },
        { key = "showGuild",      dict = addonTable.HermandadDict },
        { key = "showProfessions", dict = addonTable.ProfesionesDict },
        { key = "showRoles",      dict = addonTable.RolesDict },
        { key = "showStatus",     dict = addonTable.EstadoDict },
        { key = "showSlang",      dict = addonTable.SlangDict },
        { key = "showEndgame",    dict = addonTable.EndgameDict },
    }

    local target = db.dict.targetLocale or "enUS"

    for _, entry in ipairs(map) do
        if entry.dict and db.dict.settings[entry.key] then
            for term, byLocale in pairs(entry.dict) do
                local lowerTerm = string_lower(term)
                local translation = FirstAlternative(byLocale[target] or byLocale["enUS"] or term)

                if lowerTerm:find(" ", 1, true) then
                    local head = FirstWord(lowerTerm)
                    PhraseIndex[head] = PhraseIndex[head] or {}
                    table_insert(PhraseIndex[head], { lowerTerm, translation })
                else
                    MasterDict[lowerTerm] = translation
                end
            end
        end
    end

    -- Longest phrase first, so "raid finder" wins over "raid" at the same spot.
    for _, bucket in pairs(PhraseIndex) do
        table_sort(bucket, function(a, b) return #a[1] > #b[1] end)
    end

    local babbleSources = {
        { data = BZ, active = db.dict.settings.showZones },
        { data = BI, active = db.dict.settings.showSets },
    }
    for _, source in ipairs(babbleSources) do
        if source.data and source.active then
            for english, localised in pairs(source.data) do
                -- Skip entries that translate to themselves: a partially
                -- localised table would otherwise emit "Elwynn = Elwynn".
                if #english > 3 and localised ~= english then
                    local lowerEnglish = string_lower(english)
                    local head = FirstWord(lowerEnglish)
                    BabbleIndex[head] = BabbleIndex[head] or {}
                    table_insert(BabbleIndex[head], { lowerEnglish, localised })
                end
            end
        end
    end
    for _, bucket in pairs(BabbleIndex) do
        table_sort(bucket, function(a, b) return #a[1] > #b[1] end)
    end
end

-- ==========================================
-- HYPERLINK AND COLOUR RANGES
-- ==========================================
-- Item links and colour codes are structure, not words. A match inside one
-- would gloss part of an item name, and worse, could be reported at a position
-- that makes no sense to the reader.
local function FindProtectedRanges(text)
    local ranges = {}

    local searchStart = 1
    while true do
        local hStart = text:find("|H", searchStart, true)
        if not hStart then break end
        local firstH = text:find("|h", hStart + 2, true)
        if not firstH then break end
        local secondH = text:find("|h", firstH + 2, true)
        local hEnd = secondH and (secondH + 1) or (firstH + 1)
        table_insert(ranges, { hStart, hEnd })
        searchStart = hEnd + 1
    end

    -- Colour codes come in two shapes: |cffRRGGBB and the named |cnNAME:. The
    -- old code assumed the first and skipped a fixed ten characters looking for
    -- the closing |r, which walked straight past a short block's terminator and
    -- swallowed the rest of the line.
    searchStart = 1
    while true do
        local cStart = text:find("|c", searchStart, true)
        if not cStart then break end
        local rEnd = text:find("|r", cStart + 2, true)
        if not rEnd then break end
        table_insert(ranges, { cStart, rEnd + 1 })
        searchStart = rEnd + 2
    end

    table_sort(ranges, function(a, b) return a[1] < b[1] end)
    return ranges
end

-- Overlap, not containment: a term straddling the edge of a link is still
-- inside the link as far as the reader is concerned.
local function TouchesRange(ranges, startPos, endPos)
    for _, r in ipairs(ranges) do
        if startPos <= r[2] and endPos >= r[1] then return true end
    end
    return false
end

-- ==========================================
-- MATCH COLLECTION
-- ==========================================
local function CollectMatches(text)
    local lower = string_lower(text)
    local protected = FindProtectedRanges(text)
    local matches = {}
    local taken = {}
    local seenTerms = {}

    local function claim(startPos, endPos, term, translation)
        if TouchesRange(protected, startPos, endPos) then return end
        if TouchesRange(taken, startPos, endPos) then return end
        if not HasWordBoundaries(text, startPos, endPos) then return end
        table_insert(taken, { startPos, endPos })
        -- One entry per distinct term: "ty ty ty" is one thing worth saying,
        -- not three.
        local key = string_lower(term)
        if seenTerms[key] then return end
        seenTerms[key] = true
        table_insert(matches, { pos = startPos, term = term, translation = translation })
    end

    -- One pass over the message's words. Every source is consulted at the
    -- position where a word actually starts, which is what makes the result
    -- boundary-safe and ordered without a second sort over the dictionary.
    for startPos, word in text:gmatch("()([^%s|]+)") do
        local lowerWord = string_lower(word)

        local phrases = PhraseIndex[lowerWord]
        if phrases then
            for _, phrase in ipairs(phrases) do
                local candidate = string_sub(lower, startPos, startPos + #phrase[1] - 1)
                if candidate == phrase[1] then
                    claim(startPos, startPos + #phrase[1] - 1, string_sub(text, startPos, startPos + #phrase[1] - 1), phrase[2])
                    break
                end
            end
        end

        local babble = BabbleIndex[lowerWord]
        if babble then
            for _, entry in ipairs(babble) do
                local candidate = string_sub(lower, startPos, startPos + #entry[1] - 1)
                if candidate == entry[1] then
                    claim(startPos, startPos + #entry[1] - 1, string_sub(text, startPos, startPos + #entry[1] - 1), entry[2])
                    break
                end
            end
        end

        -- Tokens are split on whitespace, so a word carries whatever punctuation
        -- sits against it: "ty," is one token and misses the dictionary. Trim
        -- the non-word bytes off both ends and move the position with them.
        local trimStart, trimEnd = startPos, startPos + #word - 1
        while trimStart <= trimEnd and not IsWordByte(text:byte(trimStart)) do
            trimStart = trimStart + 1
        end
        while trimEnd >= trimStart and not IsWordByte(text:byte(trimEnd)) do
            trimEnd = trimEnd - 1
        end
        if trimEnd >= trimStart then
            local bare = string_sub(text, trimStart, trimEnd)
            local single = MasterDict[string_lower(bare)]
            if single then
                claim(trimStart, trimEnd, bare, single)
            end
        end
    end

    table_sort(matches, function(a, b) return a.pos < b.pos end)
    return matches
end

-- ==========================================
-- PUBLIC API
-- ==========================================

-- True when the gloss should stay out of the chat window.
--
-- The companion shows a full sentence translation of the same message, so
-- printing a term list next to it is two answers to one question. This asks
-- whether the player set the companion up — the addon has no way to see
-- whether the app is running right now, since the buffer is read-only from the
-- app's side and nothing is ever written back.
function addonTable.ShouldSuppressGloss()
    local db = BabelChatDB
    if not db or not db.dict then return false end
    -- Two states, because "never" is what `dict.enabled = false` already means.
    if db.dict.mode == "always" then return false end
    return (db.companion and db.companion.enabled) == true
end

-- Returns (displayText, changed). `changed` is what the chat filter uses to
-- decide whether to rewrite the line.
function addonTable.TranslateChat(text)
    local db = BabelChatDB
    if type(text) ~= "string" or not db or not db.dict or not db.dict.enabled then
        return text, false
    end
    if addonTable.ShouldSuppressGloss() then
        return text, false
    end

    local matches = CollectMatches(text)
    if #matches == 0 then
        return text, false
    end

    local pairsShown = {}
    for index = 1, #matches do
        if index > MAX_PAIRS then break end
        local match = matches[index]
        table_insert(pairsShown, match.term .. " = " .. match.translation)
    end

    local gloss = table_concat(pairsShown, " · ")
    local hidden = #matches - #pairsShown
    if hidden > 0 then
        gloss = gloss .. " +" .. hidden
    end

    -- Appended to the same line, not a second one. A newline here doubled the
    -- height of every glossed message and broke copy-chat, and the arrow that
    -- introduced it was the same glyph used between term and translation.
    local colour = db.dict.chatColor or "808080"
    return text .. "  " .. string_format("|cff%s%s|r", colour, gloss), true
end

-- Exposed for tests: the pieces above are where the defects lived.
addonTable._CollectMatches = CollectMatches
addonTable._FindProtectedRanges = FindProtectedRanges
addonTable._FirstAlternative = FirstAlternative
