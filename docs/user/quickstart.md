# Quick Start

## Requirements

- Windows 10/11
- World of Warcraft (Retail — The War Within / Midnight)
- Nothing else. Translation works out of the box: the in-game glossary needs no
  account, and MyMemory translates full sentences without a key.

A key only buys you better quality. In order of how easy each is to get:

| Provider | What it needs | Allowance |
| --- | --- | --- |
| **GigaChat** (default) | a Sber ID; no card, no VPN | 1M tokens a year |
| MyMemory | nothing | 5,000 words a day, 50,000 with an email |
| DeepL | a card to verify identity; never charged | 500K characters a month |
| Microsoft Translator | an Azure account | 2M characters a month |

## Step 1: Download

Download `BabelChat.zip` from [GitHub Releases](https://github.com/Yumash/BabelChat/releases).

Extract anywhere (Desktop, Downloads, wherever you like).

## Step 2: Run it

Double-click `BabelChat.exe`.

> Administrator rights are **not** needed. `ReadProcessMemory` against a process
> owned by the same user works without them, and standing elevation turns an
> ordinary library-planting bug into a full compromise. If older instructions
> told you to run as administrator, they are out of date — don't.

## Step 3: Setup Wizard

On first launch, a wizard walks you through 5 steps:

### 3.1 Welcome
Choose your interface language (English, Russian, or Spanish).

### 3.2 Translation provider

You can skip this step entirely: without a single key, MyMemory already
translates and the in-game glossary always works.

For better quality, fill in whichever provider you have. If you are in Russia,
**GigaChat** is the one that works without a VPN or a card:

1. Sign in to [Studio Sber](https://developers.sber.ru/studio/workspaces) with a Sber ID
2. Create a GigaChat API project and get its **authorization key** (base64 of
   `client_id:client_secret`)
3. Paste it into the GigaChat field and press "Validate"

Leave the root-certificate field empty — it is only for the case where the TLS
handshake fails.

### 3.3 WoW Path
BabelChat tries to auto-detect your WoW installation. If it doesn't find it, click "Browse" and select your `World of Warcraft` folder.

### 3.4 Language
- **Your language** — the language you speak (messages in this language won't be translated)
- **Target language** — the language you want foreign messages translated TO
  (defaults to your interface language)

### 3.5 Install Addon
Click "Install Addon" to copy BabelChat into your WoW AddOns folder. Alternatively, copy `addon/BabelChat/` manually to:
```
World of Warcraft/_retail_/Interface/AddOns/BabelChat/
```

## Step 4: Launch WoW

1. Start World of Warcraft
2. On the character select screen, click **AddOns** and verify "BabelChat" is enabled
3. Log in to your character
4. You should see a welcome message in chat: *"Welcome to BabelChat!"*

## Step 5: Play

Join a group and chat. BabelChat will:
- Show the **original message immediately** in the overlay
- Show the **translation 0.5-2 seconds later** below it
- Common phrases (gg, ty, brb) translate **instantly** without any delay

## What You'll See

The overlay appears on top of WoW with a dark semi-transparent background:

- **Channel colors** match WoW's native chat colors (blue for Party, orange for Raid, green for Guild)
- **Original text** in gray
- **Translation** in gold, with an arrow: `→ translated text`
- **Filter tabs** at the top to show only specific channels

## Tips

- **Toggle translation** with `Ctrl+Shift+T` (customizable in Settings)
- **Reply in another language**: click the reply area, type your message, press Enter → translation is copied to clipboard → paste in WoW with `Ctrl+V`
- **Minimize overlay** by clicking the minimize button in the title bar
- **Move/resize** by dragging the title bar or bottom-right corner
- The overlay is **click-through** by default — clicks pass through to WoW
