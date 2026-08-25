# Configuration

## Settings Dialog

Access via overlay toolbar button or system tray → Settings.

### General Tab

| Setting | Description |
|---------|-------------|
| Translation services | One row per provider: GigaChat, MyMemory, DeepL, Microsoft Translator. Fill in whichever you have — each validates on the spot, and the "Preferred" box picks which is tried first |
| WoW Path | Auto-detected or manual. "Install Addon" button copies addon to WoW |
| Interface Language | RU, EN, ES |
| Your Language | Messages in this language are not translated |
| Target Language | Foreign messages are translated to this language |
| Channels | Party, Raid, Guild, Say, Yell, Whisper, Instance, Trade, General, Services, LFG, custom channels, emotes |
| Skip own messages | Don't translate your own messages (auto-detected player name) |

### Overlay Tab

| Setting | Description |
|---------|-------------|
| Opacity | 20-100% background transparency |
| Font size | 8-20pt |
| Translation ON by default | Start with translation enabled |
| Debug console | Show debug window (for troubleshooting) |
| Write captured chat to a file | Off by default. The file holds the full text of every message the addon delivered, other players' whispers included — turn it on only while investigating something |

### Hotkeys Tab

| Default | Action |
|---------|--------|
| `Ctrl+Shift+T` | Toggle translation ON/OFF |
| `Ctrl+Shift+C` | Translate clipboard content |

Click any hotkey field and press your desired key combination to customize.

## WoW Addon Settings

In WoW: ESC → Interface → AddOns → BabelChat

| Section | Options |
|---------|---------|
| General | Enable/disable dictionary, translation color |
| Categories | 15 toggles for term categories |
| Channels | Which chat channels to translate |
| Language | Which language the gloss is written in. Taken from your WoW client on first run; 14 available |
| Companion | Enable/disable companion app buffer |
| Mode | By default the addon stays quiet while the companion app is running, so the same message is not answered twice in different words. "Show the gloss even when the app is running" overrides that |

## Addon Commands

| Command | Description |
|---------|-------------|
| `/babel` | Show help |
| `/babel config` | Open WoW settings panel |
| `/babel on` / `off` | Toggle dictionary translation |
| `/babel test` | Test with sample message |
| `/babel companion` | Show companion buffer status |
| `/babel poll on` / `off` | Toggle GetMessageInfo fallback |
| `/babel log on` / `off` | Toggle chat file logging |

## config.json

All companion app settings are stored in `config.json` (auto-created). You can edit it manually if needed:

```json
{
  "providers": {
    "gigachat": { "authorization_key": "base64 of client_id:client_secret" },
    "mymemory": { "email": "" }
  },
  "translator_priority": "gigachat",
  "wow_path": "D:/World of Warcraft",
  "ui_language": "RU",
  "own_language": "RU",
  "target_language": "RU",
  "overlay_opacity": 180,
  "overlay_font_size": 10,
  "hotkey_toggle_translate": "Ctrl+Shift+T",
  "channels_party": true,
  "channels_raid": true,
  "channels_guild": true,
  "channels_say": true,
  "channels_yell": false,
  "channels_whisper": true,
  "channels_instance": true,
  "channels_trade": false,
  "channels_general": false,
  "channels_services": false,
  "channels_lfg": false,
  "channels_custom": false,
  "channels_emote": false,
  "skip_own_messages": true,
  "show_debug_console": false,
  "debug_capture_trace": false
}
```

Credentials live under `providers`, keyed by provider id. Older configs used one
flat field per provider (`deepl_api_key`, `microsoft_api_key`,
`microsoft_region`); those are migrated on load, so an existing file keeps
working and does not need editing by hand.

A backup (`config.json.bak`) is created automatically before every save. Note
that a provider key sits in both files in the clear, so don't attach either to
an issue or paste it into chat.
