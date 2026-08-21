# WoW Addon Internals

## Structure

```
addon/BabelChat/
├── BabelChat.toc       # Manifest: version, load order, SavedVariables
├── Core.lua            # Initialization, chat filter, slash commands, welcome frame
├── DictEngine.lua      # Dictionary translation engine (annotation-based)
├── CompanionBuffer.lua # Ring buffer for companion app (ReadProcessMemory)
├── Config.lua          # Settings UI panel (Interface > AddOns > BabelChat)
├── Locales.lua         # UI strings (EN, RU, ES)
├── Data/               # 13 dictionary files (383 terms × 14 languages)
├── Libs/               # Embedded libraries (LibStub, LibBabble, LibDBIcon, etc.)
└── img/icon.tga        # Addon icon (128x128 TGA, tools/make_addon_icon.py)
```

## Chat Filter (Core.lua)

BabelChat hooks all chat events via `ChatFrame_AddMessageEventFilter`:

```lua
ChatFrame_AddMessageEventFilter(event, ChatFilter)
```

The `ChatFilter` function:
1. Writes to the companion buffer (ALL channels, regardless of the dict filter)
2. Runs the dictionary if it has anything to say, appending the gloss to the
   same line
3. Returns the modified text for in-game display

## Ring Buffer (CompanionBuffer.lua)

- 50-message ring buffer with dedup (author+text, 2s TTL)
- Format: `SEQ|KIND|EVENT|author|text` (tab separator for DICT translated text)
- Flushed to `BabelChatDB.wctbuf` every 0.25s via `C_Timer.NewTicker`
- Pre-allocated SavedVariable keys for pointer stability
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

WoW settings panel with sections:
- General: enable/disable, translation color picker
- Categories: 12 toggles for dictionary categories
- Channels: 7 channel type toggles
- Language: 14-language dropdown
- Companion: enable/disable companion buffer
- Mode: Dictionary only / Overlay only / Both

## Slash Commands

| Command | Action |
|---------|--------|
| `/babel` | Show help |
| `/babel config` | Open settings |
| `/babel on/off` | Toggle dictionary |
| `/babel test` | Test with sample message |
| `/babel companion` | Buffer status |
| `/babel poll on/off` | Toggle GetMessageInfo fallback |
| `/babel log on/off` | Toggle chat file logging |

## SavedVariables

`BabelChatDB` stores:
- `dict.*` — dictionary settings (enabled, locale, color, category toggles, channel toggles)
- `companion.*` — companion app settings (enabled, flush interval)
- `minimap.*` — minimap icon position
- `wctbuf` — ring buffer content (read by companion app)
- `wctSeq` — sequence counter (persists across `/reload`)

## Migration

On first load, if `ChatTranslatorHelperDB` exists (old addon name), settings are automatically migrated to `BabelChatDB`.
