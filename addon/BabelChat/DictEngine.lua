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
local string_byte, string_char = string.byte, string.char
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

-- Lua has no case folding beyond ASCII, so a sentence that opens with "Спс"
-- misses the key "спс" and the message the player wanted glossed comes back
-- untouched. For a Russian-speaking audience that is the first word of most
-- messages.
--
-- Cyrillic capitals are U+0410-U+042F, which UTF-8 writes as lead byte 208
-- followed by 144-175, with E as 208,129. Lower case is the same two bytes
-- wide, so folding by hand keeps every byte position in the message valid.
local CYR_LEAD_UPPER = string_char(208)
local CYR_LEAD_LOWER = string_char(209)
local CYR_UPPER = CYR_LEAD_UPPER .. "([" .. string_char(128) .. "-" .. string_char(175) .. "])"

local function Lower(text)
    return (string_gsub(string_lower(text), CYR_UPPER, function(trail)
        local code = string_byte(trail)
        if code == 129 then                      -- E -> e
            return CYR_LEAD_LOWER .. string_char(145)
        elseif code >= 144 and code <= 159 then  -- A-P, still under lead 208
            return CYR_LEAD_UPPER .. string_char(code + 32)
        elseif code >= 160 and code <= 175 then  -- R-YA, which crosses into 209
            return CYR_LEAD_LOWER .. string_char(code - 32)
        end
        return CYR_LEAD_UPPER .. trail
    end))
end

-- Multi-byte characters that are punctuation rather than letters, by lead byte.
-- 0xC2 opens U+0080-U+00BF: the non-breaking space, « », the section sign.
-- 0xE2 opens U+2000-U+2FFF: the em dash, curly quotes, the ellipsis, bullets.
-- Every other lead byte above 127 opens a letter as far as chat is concerned.
local NON_WORD_LEAD = { [194] = true, [226] = true }

-- The byte position where the character covering `pos` begins. Continuation
-- bytes are 0x80-0xBF, so walking back over them lands on the lead byte.
local function LeadByte(text, pos)
    local byte = string_byte(text, pos)
    while byte and byte >= 128 and byte < 192 and pos > 1 do
        pos = pos - 1
        byte = string_byte(text, pos)
    end
    return byte
end

-- Whether the character at `pos` is part of a word, and so whether a match may
-- begin or end beside it. Classifying by single byte treated every byte above
-- 127 as a letter, which made "«спс»" and "спс — 10g" unmatchable: the
-- guillemets and the em dash counted as part of the word.
local function IsWordCharAt(text, pos)
    if pos < 1 then return false end
    local lead = LeadByte(text, pos)
    if not lead then return false end
    if lead < 128 then
        return string_char(lead):match("[%w']") ~= nil
    end
    return not NON_WORD_LEAD[lead]
end

local function HasWordBoundaries(text, startPos, endPos)
    return not IsWordCharAt(text, startPos - 1) and not IsWordCharAt(text, endPos + 1)
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
                local lowerTerm = Lower(term)
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
                    local lowerEnglish = Lower(english)
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
    local lower = Lower(text)
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
        local key = Lower(term)
        if seenTerms[key] then return end
        seenTerms[key] = true
        table_insert(matches, { pos = startPos, term = term, translation = translation })
    end

    -- Longest entry in a bucket that the message actually continues with.
    local function claimLongest(bucket, atPos)
        if not bucket then return end
        for _, entry in ipairs(bucket) do
            local endPos = atPos + #entry[1] - 1
            if string_sub(lower, atPos, endPos) == entry[1] then
                claim(atPos, endPos, string_sub(text, atPos, endPos), entry[2])
                return
            end
        end
    end

    -- One pass over the message, a word at a time. A "word" is a maximal run of
    -- word characters, which is what makes "dps/heal", "gg,wp" and "brb/afk"
    -- work: splitting on whitespace and then trimming the ends only ever saw
    -- those as one token, so they went to the dictionary as one key and missed.
    -- That is the canonical shape of an LFG line, and it used to gloss.
    --
    -- Scanning by character class also means a non-breaking space or an em dash
    -- separates words for free — its lead byte is punctuation — so there is no
    -- rewriting of the message beforehand and no byte offsets to keep in step.
    local length = string_len(text)
    local pos = 1
    while pos <= length do
        if not IsWordCharAt(text, pos) then
            pos = pos + 1
        else
            local wordStart = pos
            while pos <= length and IsWordCharAt(text, pos) do
                pos = pos + 1
            end
            local wordEnd = pos - 1

            local bare = string_sub(text, wordStart, wordEnd)
            local bareLower = Lower(bare)

            -- Phrases are consulted from the word's own position, so punctuation
            -- in front of one cannot downgrade it to its first word — "«raid
            -- finder»" used to gloss "raid", a shorter answer and the wrong one.
            claimLongest(PhraseIndex[bareLower], wordStart)
            claimLongest(BabbleIndex[bareLower], wordStart)

            local single = MasterDict[bareLower]
            if single then
                claim(wordStart, wordEnd, bare, single)
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
-- How many entries the engine currently holds, across all three of the places
-- it keeps them. Exported so a test can watch a category toggle take effect
-- rather than infer it from the shape of the source.
function addonTable.MasterDictSize()
    local count = 0
    for _ in pairs(MasterDict) do count = count + 1 end
    for _, bucket in pairs(PhraseIndex) do count = count + #bucket end
    for _, bucket in pairs(BabbleIndex) do count = count + #bucket end
    return count
end

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
