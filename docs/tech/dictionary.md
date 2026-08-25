# Contributing Dictionary Terms

## Format

Each term is a Lua table entry in `addon/BabelChat/Data/*.lua`:

```lua
["term"] = {
    enUS = "English",
    esES = "Español (España)",
    esMX = "Español (México)",
    deDE = "Deutsch",
    frFR = "Français",
    itIT = "Italiano",
    koKR = "한국어",
    ptBR = "Português",
    ruRU = "Русский",
    zhCN = "简体中文",
    zhTW = "繁體中文",
    plPL = "Polski",
    svSE = "Svenska",
    noNO = "Norsk"
},
```

## Categories

Pick the file that matches your term:

| File | Category | Examples |
|------|----------|----------|
| `Social.lua` | Chat phrases, emotes, reactions | ty, gg, lol, wp |
| `Slang.lua` | Gaming slang, chat shortcuts, M+ callouts | ez, copium, bricked, w2w, soak |
| `Clases.lua` | Class names, specializations | warrior, dk, ret, bm |
| `Combate.lua` | Combat mechanics | aggro, aoe, cc, dot |
| `Grupos.lua` | Group/party related | lfm, lf1m, premade |
| `Mazz_Raid.lua` | Raid/dungeon mechanics | trash, ninja, boe, debuff |
| `Profesiones.lua` | Profession abbreviations | jc, bs, enchant, herb |
| `Estadisticas.lua` | Character stats | crit, haste, mastery, vers |
| `Estado.lua` | Player status | afk, oom |
| `Comercio.lua` | Trade terms | wtb, wts, cod, mats |
| `Hermandad.lua` | Guild terms | gm, officer, recruit |
| `Roles.lua` | Role names | tank, healer, dps |
| `Endgame.lua` | Midnight / endgame content | delve, catalyst, crest |

Zone and item-set names do not live here: they come from LibBabble, which ships
its own generated table per locale.

## Steps

1. Choose the correct category file
2. Copy an existing entry as template
3. Fill in all 14 languages (use the same value for esES/esMX if identical)
4. Key (the abbreviation) should be **lowercase**
5. Submit a pull request

## Rules

- **Lowercase keys** — the engine folds the message to lower case before
  matching. That fold covers ASCII and the Cyrillic block; Lua's own
  `string.lower` is ASCII-only, which is why "Спс" at the start of a sentence
  used to miss the key "спс"
- **No duplicates** — check all files before adding (a term should exist in exactly one file)
- **Multi-word phrases** are supported — use space in the key: `"go next"`
- **Short keys (1-2 chars)** may cause false positives — avoid unless very specific
- **Alternatives are not a list** — the engine shows only what comes before the
  first `/`, so `"Спасибо/спс"` renders as `Спасибо`. Put the form you actually
  want on screen first, or drop the alternative entirely
- **A word is a run of letters and digits.** Punctuation separates, so a term is
  matched inside `dps/heal`, `«спс»` and `ty,` alike, and never inside a longer
  word — `sec` does not match in `second`
- Translations should be **natural** in each language, not literal
- When in doubt about a language, use the English value as fallback (`enUS`)

## Testing

After adding terms, test in WoW:
```
/babel test
```

Or type a message containing your term in chat and check the gloss.

There is also a Python-side test suite that runs the addon's Lua under a real
Lua 5.1 interpreter, so a data change can be checked without launching the game:

```
pytest tests/test_addon_dict_engine.py tests/test_addon_dict_corpus.py
```

`test_addon_dict_corpus.py` runs the shipped dictionary against real chat lines,
which is what catches a change that breaks matching rather than one that breaks
a rule.

## What the gloss looks like

The engine appends to the message rather than adding a line under it:

```
wts bis ring 500k  wts = продаю · bis = лучшее в слоте
```

in grey, at most three pairs and then `+N`, in the order the message says them,
one entry per distinct term. Earlier versions printed a second line beginning
with an arrow; that doubled the height of every glossed message and broke
copy-chat, and the same arrow glyph meant both "annotation follows" and
"translates to".

The language is taken from the WoW client on first run, not from a shipped
default.

## How DictEngine uses the data

At load, `RebuildMasterDict()` walks every enabled category file and builds
three tables:

- `MasterDict[key]` for single words
- `PhraseIndex[first word]` for multi-word terms, longest first, so
  `raid finder` wins over `raid` at the same position
- `BabbleIndex[first word]` for zone and item-set names from LibBabble, with
  entries that translate to themselves dropped — a partially localised table
  would otherwise emit `Elwynn Forest = Elwynn Forest`

Matching is one pass over the message. Every word is a maximal run of word
characters, and all three tables are consulted at the position where that word
starts. That single pass is what makes the result boundary-safe, ordered by the
message rather than by hash traversal, and free of the duplicate entries the
old two-pass version produced.

Item links and colour codes are found first and treated as protected ranges, so
nothing inside `|Hitem:…|h` or `|cff…|r` is ever glossed.
