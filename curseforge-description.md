# BabelChat

## Break the language barrier in World of Warcraft

[![Donate](https://img.shields.io/badge/Donate-USDT%20%7C%20OpenCollective-blue?style=for-the-badge&logo=tether&logoColor=white)](https://yumatech.ru/donate/)

## The Problem

You join a PUG raid. The tank explains tactics — in Spanish. The healer asks something — in German. Nobody understands each other. Sound familiar?

**BabelChat fixes this.** It translates WoW chat in real time — gaming terms instantly (built in), and full sentences via an optional free companion app.

## Quick Start

**1. Install the addon.** That's it — gaming terms (lfm, brez, gilded, M+, delves…) now translate automatically right in your chat. **No setup, no keys, no account, completely free.**

**2. Pick your language** (optional): type **`/babel config`** and choose your language from the dropdown.

**3. Want full-sentence translation too?** (optional) Download the **free** companion app from GitHub — see the *Companion App* section below. The in-game dictionary keeps working with or without it.

> New here? Just install and play — step 1 already does the most useful part for free.

## Key Features

**Standalone — addon only (100% free, no account):**
- **380+ gaming terms** translated in 14 languages — lfm, wts, dps, ez, copium, delves, gilded, M+, RIO…
- Clean annotation below the original message (no inline color spam)
- 13 categories incl. a current **Endgame & Midnight** set (delves, crests, gear tracks, Warbands)
- Hyperlink-aware — never breaks item/spell/achievement links
- Works out of the box, **no API keys needed**

**With the optional companion app (also free):**
- **Full sentence translation** of live chat
- Smart overlay on top of WoW with channel colors
- Streaming — original shows instantly, translation arrives 0.5–2s later
- Read-only memory access — never writes, injects, or automates
- Choose your translation provider: **DeepL** or **Microsoft Translator** (see below)

## "Is the app free? Why a credit card?"

**Yes — BabelChat (addon and companion) is free and always will be.** The companion just needs a translation provider's API key:

- **DeepL** — free tier (500,000 chars/month). DeepL asks for a credit card to *verify* the account; **it never charges you** on the free tier. The card prompt comes from DeepL, not from BabelChat.
- **Microsoft Translator** — free tier (2M chars/month), **no credit card required**. Pick this if you'd rather not give DeepL a card.

You choose either one in the companion app's setup.

## Dictionary

**380+ terms × 14 languages:**

| Category | Examples | Count |
|----------|----------|-------|
| Social & Slang | ty, gg, brb, ez, copium, go next, kek | 104 |
| Classes & Specs | dk, ret, bm, disc, resto, boomkin | 59 |
| Raid & Dungeon | wipe, prog, soak, kite, brez, vault | 54 |
| Combat | aggro, aoe, cc, dps, dot, cleave | 33 |
| Groups | lfm, lf1m, premade, pug | 29 |
| Endgame & Midnight | delves, M+, gilded/runed crest, warband, gear tracks | 22 |
| Stats | crit, haste, mastery, vers, ilvl | 19 |
| Professions | jc, bs, enchant, herb, alch | 17 |
| Trade | wtb, wts, bis, mats, cod | 8 |
| + Zones | 5000+ zone names via LibBabble | — |

**Languages:** English, Spanish, German, French, Italian, Portuguese, Russian, Korean, Chinese (Simplified & Traditional), Polish, Swedish, Norwegian.

## Commands

- **/babel** — Show help
- **/babel config** — Open settings
- **/babel on/off** — Toggle dictionary
- **/babel test** — Test with sample message
- **/babel companion** — Companion app status

## Companion App

For full sentence translation, download the **free** companion app:
**https://github.com/Yumash/BabelChat**

Run it alongside WoW and enable it in the addon (AddOns > BabelChat > Companion App). It reads the addon's chat buffer via ReadProcessMemory (read-only — no injection, no automation) and shows translations in a sleek overlay. Translation runs through **DeepL** or **Microsoft Translator** — your choice (see the free/credit-card note above).

## Credits

BabelChat's dictionary is based on [WoW Translator](https://www.curseforge.com/wow/addons/wow-translator) by **Pirson** (MIT License) — 314 original terms in 14 languages. We added slang plus a current Endgame/Midnight set, rewrote the translation engine for clean output, and built the companion app.

- **Pirson** — Dictionary data & in-game translation idea — [Buy Me a Coffee](https://buymeacoffee.com/franciscorb)
- **Andrey Yumashev** — BabelChat addon, companion app, DictEngine v2
- **AhegaoZKun** — Linux/Wayland support & Microsoft Translator backend

## License

MIT License — free to use, modify, and distribute.
