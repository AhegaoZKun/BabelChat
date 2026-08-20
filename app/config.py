"""Application configuration management."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

if sys.platform == "win32":
    import winreg

logger = logging.getLogger(__name__)


def _get_config_path() -> str:
    """Return config path — user home dir when frozen (AppImage/exe), else CWD."""
    import pathlib

    if getattr(sys, "frozen", False):
        config_dir = pathlib.Path.home() / ".config" / "BabelChat"
        config_dir.mkdir(parents=True, exist_ok=True)
        return str(config_dir / "config.json")
    return "config.json"


CONFIG_FILE = _get_config_path()

# Standard WoW install locations (Windows)
_WOW_PATHS_WINDOWS = [
    Path("C:/Program Files (x86)/World of Warcraft"),
    Path("C:/Program Files/World of Warcraft"),
    Path("D:/World of Warcraft"),
    Path("D:/Games/World of Warcraft"),
]

# WoW Chat Log relative path inside WoW install
_CHATLOG_RELATIVE = "_retail_/Logs/WoWChatLog.txt"


@dataclass
class AppConfig:
    """Application settings."""

    # Translation providers, keyed by provider id: {"deepl": {"api_key": "..."}}.
    # Generic on purpose — a new provider needs no new config field, and the
    # settings UI renders whatever fields the provider declares.
    providers: dict[str, dict[str, str]] = field(default_factory=dict)
    # GigaChat first: free for individuals, no card, reachable from Russia
    # without a VPN. The others take over if it fails or runs out.
    translator_priority: str = "gigachat"

    # Paths
    wow_path: str = ""
    chatlog_path: str = ""

    # Languages
    ui_language: str = "RU"
    own_language: str = "RU"
    target_language: str = "ES"

    # Overlay
    overlay_opacity: int = 180
    overlay_font_size: int = 10
    overlay_theme: str = "wow"  # preset id from overlay_theme.PRESETS, or "custom"
    overlay_bg_color: str = "#000000"
    overlay_text_color: str = "#FFFFFF"
    overlay_original_color: str = "#888888"
    overlay_translation_color: str = "#FFD200"
    overlay_timestamp_color: str = "#666666"
    overlay_tl_on_color: str = "#40FF40"
    overlay_tl_off_color: str = "#FF4040"
    overlay_close_color: str = "#FF4040"
    overlay_tool_color: str = "#CCCCCC"
    overlay_corner_radius: int = 8
    overlay_font_family: str = ""  # empty = system default
    overlay_channel_colors: dict = field(default_factory=dict)  # slot → "#RRGGBB"
    overlay_x: int = 100
    overlay_y: int = 100
    overlay_width: int = 450
    overlay_height: int = 300

    # Hotkeys
    hotkey_toggle_translate: str = "Ctrl+Shift+T"
    hotkey_toggle_interactive: str = "Ctrl+Shift+I"
    hotkey_clipboard_translate: str = "Ctrl+Shift+C"

    # Channels
    channels_party: bool = True
    channels_raid: bool = True
    channels_guild: bool = True
    channels_say: bool = True
    channels_whisper: bool = True
    channels_yell: bool = False
    channels_instance: bool = True
    channels_trade: bool = False
    channels_general: bool = False
    channels_services: bool = False
    channels_lfg: bool = False

    # Translation
    skip_own_messages: bool = True
    translation_enabled_default: bool = True

    # Debug
    show_debug_console: bool = False

    def save(self, path: str = CONFIG_FILE) -> None:
        """Save config to JSON file atomically (write to temp, then rename)."""
        target = Path(path)
        content = json.dumps(asdict(self), indent=2, ensure_ascii=False)

        # Backup existing config
        if target.exists():
            bak = target.with_suffix(".json.bak")
            try:
                bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                logger.warning("Could not create config backup")

        # Atomic write: temp file in same directory, then rename
        # Use system temp dir — target.parent may be read-only (e.g. AppImage mount)
        fd, tmp_path = tempfile.mkstemp(dir=tempfile.gettempdir(), suffix=".tmp", prefix="config_")
        closed = False
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            closed = True
            shutil.move(tmp_path, str(target))
        except OSError:
            if not closed:
                os.close(fd)
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()
            raise

    @classmethod
    def load(cls, path: str = CONFIG_FILE) -> AppConfig:
        """Load config from JSON file, using defaults for missing fields."""
        target = Path(path)
        for try_path in [target, target.with_suffix(".json.bak")]:
            try:
                data = json.loads(try_path.read_text(encoding="utf-8"))
                _migrate_provider_keys(data, try_path)
                defaults = asdict(cls())
                # Ignore unknown keys (e.g. fields removed in newer versions)
                # instead of crashing cls(**...) with a TypeError.
                defaults.update({k: v for k, v in data.items() if k in defaults})
                return cls(**defaults)
            except FileNotFoundError:
                continue
            except json.JSONDecodeError:
                logger.warning("Corrupt config file: %s, trying backup", try_path)
                continue
        logger.warning("No valid config found, using defaults")
        return cls()


# Config written before providers became generic: one flat field per provider
# per credential. Mapped onto the new shape as {provider: {field: value}}.
_LEGACY_PROVIDER_KEYS = {
    "deepl_api_key": ("deepl", "api_key"),
    "microsoft_api_key": ("microsoft", "api_key"),
    "microsoft_region": ("microsoft", "region"),
}


def _migrate_provider_keys(data: dict, source: Path) -> None:
    """Fold pre-registry API-key fields into `providers`, in place.

    Runs before unknown keys are dropped — otherwise upgrading would silently
    discard the user's API keys and present them with an unconfigured app. A
    copy of the original file is kept alongside it the first time this happens,
    because the ordinary `.json.bak` gets overwritten by the very next save.
    """
    legacy = {k: v for k, v in data.items() if k in _LEGACY_PROVIDER_KEYS and str(v).strip()}
    if not legacy:
        return

    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        logger.warning("Ignoring malformed 'providers' section while migrating API keys")
        providers = {}
        data["providers"] = providers

    migrated = []
    for key, value in legacy.items():
        provider_id, field_name = _LEGACY_PROVIDER_KEYS[key]
        settings = providers.setdefault(provider_id, {})
        # A value already present in the new shape wins: it is the one the user
        # last edited, and re-running the migration must not undo that.
        if not settings.get(field_name):
            settings[field_name] = value
            migrated.append(f"{provider_id}.{field_name}")

    if not migrated:
        return

    backup = source.with_suffix(".json.pre-providers.bak")
    if not backup.exists():
        try:
            backup.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            logger.warning("Could not write pre-migration config backup")
    logger.info("Migrated API keys to the provider registry: %s", ", ".join(migrated))


def detect_wow_path() -> str:
    """Try to find WoW installation path."""
    if sys.platform == "win32":
        # Try registry first (Battle.net launcher)
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft",
            )
            install_path, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            if Path(install_path).exists():
                return str(install_path)
        except (FileNotFoundError, OSError):
            pass

        for p in _WOW_PATHS_WINDOWS:
            if p.exists():
                return str(p)
    else:
        # On Linux, WoW can be installed anywhere (Steam library, NTFS drive, etc.)
        # Auto-detection is unreliable — return empty and let the user set it via GUI.
        return ""

    return ""


def resolve_chatlog_path(config: AppConfig) -> Path:
    """Resolve the WoW Chat Log file path from config."""
    if config.chatlog_path:
        return Path(config.chatlog_path)

    wow_path = config.wow_path or detect_wow_path()
    if wow_path:
        return Path(wow_path) / _CHATLOG_RELATIVE

    return Path("WoWChatLog.txt")
