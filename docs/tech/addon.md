# WoW Addon Internals

## Structure

```
addon/BabelChat/
├── BabelChat.toc       # Manifest: version, load order, SavedVariables
├── Core.lua            # Initialization, defaults, migration, chat filter
├── Commands.lua        # Slash commands, self test, first-run welcome frame
├── DictEngine.lua      # Dictionary translation engine (same-line gloss)
├── CompanionBuffer.lua # Ring buffer for companion app (ReadProcessMemory)
├── Config.lua          # Settings UI panel (Interface > AddOns > BabelChat)
├── Locales.lua         # UI strings (EN, RU, ES)
├── Data/               # 13 dictionary files (436 terms × 14 languages)
├── Libs/               # Embedded libraries (LibStub, LibBabble, LibDBIcon, etc.)
└── img/icon.tga        # Addon icon (128x128 TGA, tools/make_addon_icon.py)
```

`Commands.lua` was split out of `Core.lua`, which exists to wire the addon
together at load and had stopped looking like it. The TOC load order matters in
one place beyond the usual: every `Data/*.lua` file must be listed before
`DictEngine.lua`, because `RebuildMasterDict` indexes them at load time and a
data file listed after the engine contributes nothing.

## Chat Filter (Core.lua)

BabelChat hooks all chat events via `ChatFrame_AddMessageEventFilter`:

```lua
ChatFrame_AddMessageEventFilter(event, ChatFilter)
```

The `ChatFilter` function:
1. Shortens the event name (`CHAT_MSG_GUILD` → `GUILD`). For `CHAT_MSG_CHANNEL`
   it sends `CHANNEL:<zoneChannelID>:<name>` instead: the name alone is
   localised, so matching it against English words filed Trade on a Russian
   client as General. The id is the same number on every locale, and 0 means a
   player-made channel. If the id cannot be read, the older `CHANNEL:<name>`
   form goes out and the companion falls back to matching the name
2. Runs the dictionary, if it is enabled and the channel is not filtered out,
   appending the gloss to the same line
3. Writes to the companion buffer — ALL channels, regardless of the dictionary's
   channel filter — tagged `DICT` if the dictionary changed the line, `RAW`
   otherwise
4. Returns the modified text for in-game display

Both the dictionary call and the buffer write are wrapped in `pcall`. An error
escaping a chat event filter does not lose one message; it breaks the filter for
every line that follows, for the rest of the session.

## Ring Buffer (CompanionBuffer.lua)

- 50-message ring buffer with dedup (author+text, 2s TTL)
- Format: `SEQ|KIND|EVENT|author|text` (tab separator for DICT translated text)
- Flushed to `BabelChatDB.wctbuf` every 0.25s via `C_Timer.NewTicker`
- Pre-allocated SavedVariable keys for pointer stability — this is load-bearing,
  not tidiness: the companion holds the address of one slot in this table, and
  adding a key afterwards would rehash it and move every slot
- `pcall` wrapping for secret-tainted instance chat

## Dictionary Engine (DictEngine.lua)

Based on Pirson's WoW Translator (MIT License), rewritten for v2:

- **Appended, on the same line**: `wts bis ring 500k  wts = продаю · bis = лучшее в слоте`,
  in grey. Not a second line: that doubled the height of every glossed message
  and broke copy-chat, which is most of why a busy Trade channel was unreadable.
- **One entry per term**, in the order the message says them — "ty ty ty" is one
  thing worth saying, not three, and a gloss that runs in a different order from
  the sentence above it is harder to read than no gloss at all.
- **Capped at three pairs** plus a `+N` count.
- **First alternative only**: the data says "Спасибо/спс", which is a
  lexicographer's note, not a translation.
- **Quiet when the companion is running** (`mode = "auto"`): otherwise both
  answer the same message, in different words.
- **Hyperlink-aware**: skips `|H...|h` and `|cff...|r` blocks, including the
  named colour form.
- **UTF-8 aware boundaries**: guillemets, the em dash and the non-breaking space
  are punctuation, not letters, and the Cyrillic block is case-folded by hand
  since Lua's `string.lower` is ASCII-only.
- **Overlap guard**: matched ranges tracked, no double translations.
- **Multi-word priority**: longest phrases matched first, looked up from the
  trimmed word so punctuation cannot downgrade a phrase to its first word.
- **13 categories**: Social, Classes, Combat, Raid, Groups, Stats, Professions,
  Trade, Status, Guild, Roles, Slang, Endgame — plus zone and item-set names
  from LibBabble.

## Config UI (Config.lua)

WoW settings panel, six sections in this order:

1. **General** — enable/disable, an "always gloss" checkbox (it sets
   `dict.mode` to `always` instead of `auto`, so the gloss keeps printing while
   the companion app is running), and the gloss colour picker
2. **Categories** — 15 checkboxes: the 13 dictionary categories, each labelled
   with its own entry count, plus zone names and item sets, which come from
   LibBabble and have no data file of their own
3. **Channels** — 7 checkboxes, each covering a group of `CHAT_MSG_*` events
   (Say also covers Yell; Party also covers instance chat; Whisper also covers
   Battle.net whispers)
4. **Language** — 14-language dropdown, plus the Test button
5. **Companion App** — enable/disable the companion buffer. Off by default: the
   addon works standalone, and nothing is written to memory until this is on
6. **About** — credits and donation details

## Slash Commands (Commands.lua)

| Command | Action |
|---------|--------|
| `/babel` | Show help (anything unrecognised does the same) |
| `/babel config` (or `settings`) | Open the settings panel |
| `/babel on` / `off` | Toggle the dictionary |
| `/babel test` | Gloss a sample LFG line, forced even when the companion is running |
| `/babel companion` (or `buf`) | Buffer status: messages, seq, flush ticker, poll fallback |
| `/babel poll on/off` | Toggle the GetMessageInfo fallback |
| `/babel log on/off` | Toggle WoW's chat file logging plus the periodic log flush |

Every reply goes through the locale table, so the commands report in the client's
language rather than in English.

## SavedVariables

`BabelChatDB` — declared by the TOC's `## SavedVariables` line, which the whole
companion protocol rests on: without it WoW never persists the table, so
`wctbuf` never exists and the app looks broken with no error anywhere.

- `dict.*` — dictionary settings: `enabled`, `targetLocale` (unset until
  detected from the client), `mode` (`auto` / `always`), `chatColor`,
  `settings.*` category toggles, `settings.channels` event toggles
- `companion.*` — `enabled`, `autoLog`, `verbose`, `pollFallback`, and
  `flushInterval`, which is the interval for the *chat-log* flush started by
  `/babel log on`. The memory buffer's own flush is the `FLUSH_INTERVAL`
  constant in `CompanionBuffer.lua` (0.25s) and is not configurable
- `minimap.*` — minimap icon position
- `wctbuf` — ring buffer content (read by companion app)
- `wctSeq` — sequence counter (persists across `/reload`)
- `wctFlush` — the pulse: a rebuild counter that ticks on every flush, including
  the idle ones every two seconds. It is what lets the companion tell a live
  buffer from the bytes an earlier one left behind, which are otherwise
  identical — same markers, same last message. Persists across `/reload`, or the
  live buffer would look older than the corpse of the previous session
- `wctAnchor` — a constant, written once and never changed. The companion finds
  it and then reads the buffer through the table slot beside it, instead of
  searching memory for the string, which moves on every rebuild. It has to be
  set inside `PreallocateCompanionKeys` with the others: adding a key later
  rehashes the table, and a rehash moves every slot including the one the
  companion is holding. See [memory-reader.md](memory-reader.md)
- `firstRun` — drives the welcome frame

## Migration

On first load, and in this order: adopt `ChatTranslatorHelperDB` if that is all
the player has (the old addon name), rename the Spanish-derived setting keys
(`showMazz` → `showDungeons`, `showClases` → `showClasses`, and six more), and
only then fill in defaults. Any other order sees the new keys missing, defaults
them to `true`, and hands the player back every category they had switched off.
