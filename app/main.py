"""BabelChat — entry point."""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys

from dotenv import load_dotenv
from lingua import Language
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app import debug_log
from app.about_dialog import AboutDialog
from app.config import AppConfig, resolve_chatlog_path
from app.hotkeys import GlobalHotkeyManager
from app.i18n import tr
from app.overlay import ChatOverlay
from app.parser import Channel
from app.pipeline import PipelineConfig, TranslationPipeline
from app.settings_dialog import SettingsDialog
from app.translator import TranslatorService, any_configured
from app.tray import TrayIcon

# Configure logging: file only at startup (no StreamHandler — console may not exist
# in windowed exe). Console handler added later by _setup_console() if enabled.
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Write log next to the executable when possible, fall back to user home dir
# (AppImage mounts are read-only so we can't write next to the binary there)
def _get_log_path() -> str:
    import sys as _sys
    if getattr(_sys, "frozen", False):
        # PyInstaller bundle — use home dir to avoid read-only AppImage mount
        import pathlib
        return str(pathlib.Path.home() / "babelchat.log")
    return "babelchat.log"

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FMT,
    handlers=[
        logging.FileHandler(_get_log_path(), encoding="utf-8", mode="w"),
    ],
)
logger = logging.getLogger(__name__)

# Lingua language code mapping
_LANG_CODE_TO_LINGUA: dict[str, Language] = {
    "EN": Language.ENGLISH,
    "RU": Language.RUSSIAN,
    "DE": Language.GERMAN,
    "FR": Language.FRENCH,
    "ES": Language.SPANISH,
    "IT": Language.ITALIAN,
    "PT": Language.PORTUGUESE,
    "PL": Language.POLISH,
    "NL": Language.DUTCH,
    "UK": Language.UKRAINIAN,
    "TR": Language.TURKISH,
    "ZH": Language.CHINESE,
    "JA": Language.JAPANESE,
    "KO": Language.KOREAN,
}


class PipelineThread(QThread):
    """Runs TranslationPipeline in a background thread."""

    message_ready = pyqtSignal(object)  # TranslatedMessage

    def __init__(self, config: PipelineConfig) -> None:
        super().__init__()
        self._config = config
        self._pipeline: TranslationPipeline | None = None

    def run(self) -> None:
        self._pipeline = TranslationPipeline(
            config=self._config,
            on_message=lambda msg: self.message_ready.emit(msg),
        )
        self._pipeline.start()
        self.exec()  # Event loop to keep thread alive

    def stop(self) -> None:
        if self._pipeline:
            self._pipeline.stop()
        self.quit()
        self.wait(5000)

    def update_config(self, config: PipelineConfig) -> None:
        """Forward config update to the pipeline (thread-safe)."""
        if self._pipeline:
            self._pipeline.update_config(config)

    def clear_cache(self) -> int:
        """Clear the running pipeline's translation cache.

        Goes through the pipeline that is actually running rather than opening a
        second connection to a default path: the two are not always the same
        file, and only this one owns the in-memory half of the cache.
        """
        return self._pipeline.clear_cache() if self._pipeline else 0

    @property
    def pipeline(self) -> TranslationPipeline | None:
        return self._pipeline


def _build_pipeline_config(config: AppConfig) -> PipelineConfig:
    """Convert AppConfig to PipelineConfig."""
    chatlog = resolve_chatlog_path(config)
    own_lang = _LANG_CODE_TO_LINGUA.get(config.own_language, Language.ENGLISH)

    enabled_channels: set[Channel] = set()
    if config.channels_party:
        enabled_channels |= {Channel.PARTY, Channel.PARTY_LEADER}
    if config.channels_raid:
        enabled_channels |= {Channel.RAID, Channel.RAID_LEADER, Channel.RAID_WARNING}
    if config.channels_guild:
        enabled_channels |= {Channel.GUILD, Channel.OFFICER}
    if config.channels_say:
        enabled_channels |= {Channel.SAY, Channel.YELL}
    if config.channels_whisper:
        enabled_channels |= {Channel.WHISPER_FROM, Channel.WHISPER_TO}
    if config.channels_instance:
        enabled_channels |= {Channel.INSTANCE, Channel.INSTANCE_LEADER}
    if config.channels_trade:
        enabled_channels |= {Channel.TRADE}
    if config.channels_general:
        enabled_channels |= {Channel.GENERAL}
    if config.channels_services:
        enabled_channels |= {Channel.SERVICES}
    if config.channels_lfg:
        enabled_channels |= {Channel.LOOKING_FOR_GROUP}

    return PipelineConfig(
        chatlog_path=chatlog,
        providers=config.providers,
        translator_priority=getattr(config, "translator_priority", "deepl"),
        target_lang=config.target_language,
        own_language=own_lang,
        enabled_channels=enabled_channels,
        skip_own_messages=config.skip_own_messages,
        translation_enabled=config.translation_enabled_default,
    )


def _enabled_filter_names(config: AppConfig) -> set[str]:
    """Build the set of overlay filter tab names from config channel booleans."""
    names: set[str] = set()
    if config.channels_party:
        names.add("Party")
    if config.channels_raid:
        names.add("Raid")
    if config.channels_guild:
        names.add("Guild")
    if config.channels_say:
        names.add("Say")
    if config.channels_whisper:
        names.add("Whisper")
    if config.channels_instance:
        names.add("Instance")
    if config.channels_trade:
        names.add("Trade")
    if config.channels_general:
        names.add("General")
    if config.channels_services:
        names.add("Services")
    if config.channels_lfg:
        names.add("LookingForGroup")
    return names


_console_initialized = False


def _setup_console(visible: bool) -> None:
    """Show or hide a debug console window.

    On Windows: allocates a Win32 console window and redirects stdout/stderr.
    On Linux: adds a StreamHandler to logging and switches to DEBUG level.
    Also switches all logging to DEBUG level on both platforms.
    """
    global _console_initialized
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        if visible and not _console_initialized:
            kernel32.AllocConsole()
            try:
                sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
                sys.stderr = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
            except OSError:
                return
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(logging.Formatter(_LOG_FMT))
            root = logging.getLogger()
            root.addHandler(console_handler)
            root.setLevel(logging.DEBUG)
            for h in root.handlers:
                h.setLevel(logging.DEBUG)
            _console_initialized = True
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5 if visible else 0)
    else:
        # Linux: just attach a StreamHandler (terminal is already available)
        if visible and not _console_initialized:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(logging.Formatter(_LOG_FMT))
            root = logging.getLogger()
            root.addHandler(console_handler)
            root.setLevel(logging.DEBUG)
            for h in root.handlers:
                h.setLevel(logging.DEBUG)
            _console_initialized = True


def _get_lock_file() -> str:
    if getattr(__import__("sys"), "frozen", False):
        lock_dir = os.path.join(os.path.expanduser("~"), ".config", "BabelChat")
        os.makedirs(lock_dir, exist_ok=True)
        return os.path.join(lock_dir, "babelchat.lock")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "babelchat.lock")

_LOCK_FILE = _get_lock_file()


def _ensure_single_instance() -> None:
    """Ensure only one instance is running. Kill the old one if found."""
    lock_path = os.path.abspath(_LOCK_FILE)
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                old_pid = int(f.read().strip())
            if sys.platform == "win32":
                kernel32 = ctypes.windll.kernel32
                PROCESS_TERMINATE = 0x0001
                SYNCHRONIZE = 0x00100000
                handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, old_pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.WaitForSingleObject(handle, 2000)
                    kernel32.CloseHandle(handle)
                    logger.info("Killed old instance PID %d", old_pid)
                else:
                    logger.info("Old PID %d no longer running", old_pid)
            else:
                # Linux: check /proc, send SIGTERM
                if os.path.exists(f"/proc/{old_pid}"):
                    try:
                        os.kill(old_pid, 15)  # SIGTERM
                        import time as _time
                        _time.sleep(0.5)
                        logger.info("Sent SIGTERM to old instance PID %d", old_pid)
                    except ProcessLookupError:
                        logger.info("Old PID %d already gone", old_pid)
                else:
                    logger.info("Old PID %d no longer running", old_pid)
        except Exception as e:
            logger.warning("Failed to kill old instance: %s", e)
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))


def main() -> int:
    load_dotenv()

    # Single instance guard — kill old instance if running
    _ensure_single_instance()

    # On Linux, force XCB (XWayland) backend so the overlay works correctly
    # on Wayland compositors — enables always-on-top and free window positioning.
    if sys.platform != "win32" and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        # Suppress Qt's "Ignoring icon" warning — it's a cosmetic XCB tray limitation
        os.environ.setdefault("QT_LOGGING_RULES", "*.warning=false")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Load config
    config = AppConfig.load()

    # Debug console: hidden by default, show if configured
    if config.show_debug_console:
        _setup_console(visible=True)

    # Capture trace: off unless asked for. It records every chat line in full,
    # other players' whispers included, so it is never on by default.
    debug_log.configure(config.debug_capture_trace)

    # Set UI language from config
    tr.set_language(config.ui_language)

    # First run — setup wizard if no API key
    if not any_configured(config.providers):
        from app.setup_wizard import SetupWizard

        while True:
            wizard = SetupWizard(config)
            result = wizard.exec()
            if result == 2:  # Language changed — restart wizard
                config = wizard.get_config()
                continue
            if result != SetupWizard.DialogCode.Accepted:
                return 0
            config = wizard.get_config()
            break

    # Create overlay
    print("DEBUG: creating overlay", flush=True)
    overlay = ChatOverlay(config)
    overlay.update_channel_filters(_enabled_filter_names(config))

    # Provide translator for the reply panel.
    # Reply translates outgoing messages — default to EN unless own language is EN.
    reply_translator = TranslatorService.from_config(config)
    reply_lang = "EN" if config.own_language != "EN" else config.target_language
    overlay.set_translator(reply_translator, reply_lang)

    overlay.show()
    print("DEBUG: overlay shown", flush=True)

    # Create system tray
    tray = TrayIcon()
    tray.show_overlay_requested.connect(overlay.show)
    tray.hide_overlay_requested.connect(overlay.hide)
    tray.toggle_translation_requested.connect(overlay._toggle_translation)
    tray.quit_requested.connect(app.quit)
    tray.show()

    def open_settings() -> None:
        nonlocal config
        old_console = config.show_debug_console
        dialog = SettingsDialog(config, clear_cache=pipeline_thread.clear_cache)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            config = dialog.get_config()
            overlay.update_channel_filters(_enabled_filter_names(config))
            overlay.apply_settings(config)
            # Propagate language/channel settings to the pipeline thread
            new_pipeline_config = _build_pipeline_config(config)
            pipeline_thread.update_config(new_pipeline_config)
            # Toggle debug console if setting changed
            if config.show_debug_console != old_console:
                _setup_console(config.show_debug_console)

    tray.settings_requested.connect(open_settings)
    overlay.settings_requested.connect(open_settings)
    overlay.quit_requested.connect(app.quit)

    def open_about() -> None:
        AboutDialog().exec()

    tray.about_requested.connect(open_about)

    # Global hotkeys
    hotkey_mgr = GlobalHotkeyManager()
    hk_toggle_translate = hotkey_mgr.register(config.hotkey_toggle_translate)

    def on_hotkey(hk_id: int) -> None:
        if hk_id == hk_toggle_translate:
            overlay._toggle_translation()

    hotkey_mgr.hotkey_pressed.connect(on_hotkey)
    hotkey_mgr.start()

    # Start pipeline
    pipeline_config = _build_pipeline_config(config)
    pipeline_thread = PipelineThread(pipeline_config)
    pipeline_thread.message_ready.connect(overlay.add_message)

    # Load chat history before starting real-time feed
    from app.parser import parse_line
    from app.pipeline import TranslatedMessage
    from app.watcher import ChatLogWatcher
    _history_watcher = ChatLogWatcher(pipeline_config.chatlog_path, lambda _: None)
    _history_lines = _history_watcher.read_tail(max_lines=50)
    history: list[TranslatedMessage] = []
    for _line in _history_lines:
        _msg = parse_line(_line)
        if not _msg or _msg.channel not in pipeline_config.enabled_channels:
            continue
        # Skip NPC messages (names with spaces) in Say/Yell
        if _msg.channel in (Channel.SAY, Channel.YELL) and " " in _msg.author:
            continue
        history.append(TranslatedMessage(original=_msg, translation=None))
    overlay.load_history(history)

    pipeline_thread.start()

    # WoW connection status checker for overlay
    def wow_status_checker() -> str:
        pipeline = pipeline_thread.pipeline
        if pipeline is None:
            return "searching"
        mw = pipeline._memory_watcher
        if mw is None:
            return "offline"
        if mw.is_attached:
            return "attached"
        return "searching"

    overlay.set_wow_status_checker(wow_status_checker)

    # Graceful shutdown
    def shutdown() -> None:
        logger.info("Shutting down...")
        hotkey_mgr.stop()
        pipeline_thread.stop()
        tray.hide()
        app.quit()

    signal.signal(signal.SIGINT, lambda *_: shutdown())
    app.aboutToQuit.connect(lambda: (hotkey_mgr.stop(), pipeline_thread.stop()))

    logger.info("BabelChat started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
