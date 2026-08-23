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
from app.config import AppConfig, enabled_channels, enabled_filter_tabs, resolve_chatlog_path
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

    channels = enabled_channels(config)

    return PipelineConfig(
        chatlog_path=chatlog,
        providers=config.providers,
        translator_priority=config.translator_priority,
        target_lang=config.target_language,
        own_language=own_lang,
        enabled_channels=channels,
        skip_own_messages=config.skip_own_messages,
        translation_enabled=config.translation_enabled_default,
    )


def _enabled_filter_names(config: AppConfig) -> set[str]:
    """Overlay filter tabs, from the same declaration the checkboxes come from.

    This was a fifth hand-written copy of the channel mapping, and it was
    missing Custom and Emote — so a message from either had no tab of its own
    and appeared only under All.
    """
    return enabled_filter_tabs(config)


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


def _linux_start_time(stat_line: str) -> str:
    """`starttime` out of a line of /proc/<pid>/stat.

    Separate from the reading so it can be tested where /proc does not exist,
    which is where this project is developed. The parsing is not obvious: field
    two is the executable name in parentheses, and it may itself contain spaces
    and parentheses — `(my prog) (v2)` is a legal name — so splitting the line
    on whitespace from the left puts every later field at an offset that
    depends on what the process is called. Counting from the last ')' is the
    documented way round it. starttime is field 22, and the last ')' ends field
    two, so it is index 19 in what follows.
    """
    return stat_line.rpartition(")")[2].split()[19]


def _start_stamp(pid: int) -> str | None:
    """When the process at `pid` started, as the operating system recorded it.

    A PID on its own does not identify a process for longer than that process
    lives: Windows hands the numbers back out, and Linux wraps them. The lock
    file outlives the copy that wrote it, so by the time it is read the number
    in it may belong to something the user very much wants to keep running.

    Paired with the PID, the start time is unique — a process that took over the
    number necessarily started later. Returns None when the answer is unknown,
    which the caller must treat as "do not touch it".
    """
    try:
        if sys.platform == "win32":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return None
            try:
                created = ctypes.c_ulonglong()
                exited = ctypes.c_ulonglong()
                kernel_time = ctypes.c_ulonglong()
                user_time = ctypes.c_ulonglong()
                ok = kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                )
                return str(created.value) if ok else None
            finally:
                kernel32.CloseHandle(handle)

        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                return _linux_start_time(f.read())
    except (OSError, ValueError, IndexError):
        return None

    return None


def _terminate(pid: int) -> None:
    """Stop the previous copy. Only ever called for a verified match."""
    if sys.platform == "win32":
        PROCESS_TERMINATE = 0x0001
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
        if not handle:
            logger.info("Old PID %d is already gone", pid)
            return
        kernel32.TerminateProcess(handle, 0)
        kernel32.WaitForSingleObject(handle, 2000)
        kernel32.CloseHandle(handle)
    else:
        import time as _time

        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            logger.info("Old PID %d is already gone", pid)
            return
        _time.sleep(0.5)
    logger.info("Stopped the previous instance, PID %d", pid)


def _ensure_single_instance() -> None:
    """Stop the previous copy of BabelChat, and nothing else.

    The lock file carries the PID and the start stamp of the process that wrote
    it. Both must match a live process before anything is terminated; a lock
    with only a PID — written by a version before this check existed — matches
    nothing, so an upgrade leaves the running copy for the user to close rather
    than gambling on the number.
    """
    lock_path = os.path.abspath(_LOCK_FILE)
    if os.path.exists(lock_path):
        try:
            with open(lock_path, encoding="utf-8") as f:
                recorded = f.read().splitlines()
            old_pid = int(recorded[0].strip())
            was = recorded[1].strip() if len(recorded) > 1 else ""
            now = _start_stamp(old_pid)

            # One condition, deliberately. An earlier version spelled the three
            # ways this can fail as three branches, and each of them turned out
            # to be unreachable — the comparison below already rejects a missing
            # stamp, an unknown one and a mismatched one. Branches that cannot
            # change the outcome cannot be tested either, and they read as if
            # they were load-bearing.
            if now and now == was:
                _terminate(old_pid)
            else:
                logger.info(
                    "Leaving PID %d alone: the lock says it started at %s, the live process says %s",
                    old_pid,
                    was or "(nothing)",
                    now or "(nothing there)",
                )
        except (OSError, ValueError, IndexError) as e:
            logger.warning("Could not read the lock file: %s", e)

    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()}\n{_start_stamp(os.getpid()) or ''}\n")


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
    # Every hotkey the settings window lets you configure is registered here.
    # The clipboard one was offered, saved, and read by nobody: a combination
    # the user assigned and pressed that did nothing at all.
    actions = {
        hotkey_mgr.register(config.hotkey_toggle_translate): overlay._toggle_translation,
        hotkey_mgr.register(config.hotkey_clipboard_translate): overlay.translate_clipboard,
    }

    def on_hotkey(hk_id: int) -> None:
        action = actions.get(hk_id)
        if action is not None:
            action()

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
        # A named problem outranks both of the other answers: "attached" used to
        # mean only that a process called Wow.exe existed, so a reader Windows
        # was refusing showed the same green tick as a working one.
        problem = getattr(mw, "problem", "")
        if problem:
            return problem
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
