"""GTK entry point for BabelChat (Linux/Wayland layer-shell frontend).

Qt-free counterpart to app/main.py: builds the reusable TranslationPipeline and
TranslatorService, then runs the GTK4 layer-shell overlay. The pipeline runs in
its own background thread and pushes TranslatedMessages to the overlay, which
marshals them onto the GTK main loop.

Stage 1: overlay display + reply box. Settings/tray come later; for now the
overlay's settings/quit buttons quit the app (settings UI is still the Qt
SettingsDialog, launched separately, or to be ported).
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from lingua import Language

from app.config import CONFIG_FILE, AppConfig, resolve_chatlog_path
from app.overlay_gtk import ChatOverlayGtk
from app.parser import Channel
from app.pipeline import PipelineConfig, TranslationPipeline
from app.settings_gtk import SettingsWindowGtk
from app.translator import TranslatorService

_LANG_CODE_TO_LINGUA: dict[str, Language] = {
    "EN": Language.ENGLISH,
    "RU": Language.RUSSIAN,
    "ES": Language.SPANISH,
    "DE": Language.GERMAN,
    "FR": Language.FRENCH,
    "PT": Language.PORTUGUESE,
    "IT": Language.ITALIAN,
    "PL": Language.POLISH,
    "ZH": Language.CHINESE,
    "KO": Language.KOREAN,
    "JA": Language.JAPANESE,
}


def _build_pipeline_config(config: AppConfig) -> PipelineConfig:
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
        deepl_api_key=config.deepl_api_key,
        microsoft_api_key=getattr(config, "microsoft_api_key", ""),
        microsoft_region=getattr(config, "microsoft_region", ""),
        translator_priority=getattr(config, "translator_priority", "deepl"),
        target_lang=config.target_language,
        own_language=own_lang,
        enabled_channels=enabled_channels,
        skip_own_messages=config.skip_own_messages,
        translation_enabled=config.translation_enabled_default,
    )


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = AppConfig.load()

    # First run: no config file yet, or no translation API configured →
    # run the setup wizard (its own blocking GTK loop) before normal startup.
    if not os.path.exists(CONFIG_FILE) or (
        not config.deepl_api_key
        and not getattr(config, "microsoft_api_key", "")
    ):
        from app.setup_wizard_gtk import run_setup_wizard

        config = run_setup_wizard(config)
        if config is None:  # user closed the wizard without finishing
            return 0

    overlay = ChatOverlayGtk(config)

    # Reply translator (outgoing): default EN unless own language is EN.
    reply_translator = TranslatorService(
        api_key=config.deepl_api_key,
        microsoft_api_key=getattr(config, "microsoft_api_key", ""),
        microsoft_region=getattr(config, "microsoft_region", ""),
        priority=getattr(config, "translator_priority", "deepl"),
    )
    reply_lang = "EN" if config.own_language != "EN" else config.target_language
    overlay.set_translator(reply_translator, reply_lang)

    # Pipeline: deliver each TranslatedMessage to the overlay (thread-safe).
    pipeline = TranslationPipeline(
        config=_build_pipeline_config(config),
        on_message=overlay.deliver_message,
    )

    def _quit() -> None:
        try:
            pipeline.stop()
        finally:
            overlay._app.quit()

    overlay.on_quit = _quit

    settings_win: dict[str, SettingsWindowGtk | None] = {"ref": None}

    def _open_settings() -> None:
        # If a settings window is already open, just bring it forward instead of
        # opening another one.
        existing = settings_win["ref"]
        if existing is not None:
            existing.present()
            return

        def _on_saved(updated: AppConfig) -> None:
            # Apply live: rebuild pipeline config (channels/langs).
            pipeline.update_config(_build_pipeline_config(updated))
            # Rebuild the reply translator so API key/priority changes take
            # effect without a restart.
            new_translator = TranslatorService(
                api_key=updated.deepl_api_key,
                microsoft_api_key=getattr(updated, "microsoft_api_key", ""),
                microsoft_region=getattr(updated, "microsoft_region", ""),
                priority=getattr(updated, "translator_priority", "deepl"),
            )
            new_reply_lang = "EN" if updated.own_language != "EN" else updated.target_language
            overlay.set_translator(new_translator, new_reply_lang)
            # Restyle the overlay live (opacity/font) without a restart.
            overlay.apply_appearance()
            logging.info("settings applied live")

        win = SettingsWindowGtk(config, on_saved=_on_saved, app=overlay._app)
        settings_win["ref"] = win

        def _on_close(_w: object) -> bool:
            settings_win["ref"] = None
            return False  # allow the window to close

        win._win.connect("close-request", _on_close)
        win.present()

    overlay.on_settings = _open_settings

    def _toggle_translation(enabled: bool) -> None:
        pipeline.translation_enabled = enabled

    overlay.on_toggle_translation = _toggle_translation

    # Show recent history on launch (same as the PyQt frontend). These queue in
    # the overlay's pending list and render once the window is built.
    try:
        for hist_msg in pipeline.load_history(50):
            overlay.deliver_message(hist_msg)
    except Exception:  # noqa: BLE001
        logging.exception("history load failed (continuing without it)")

    pipeline.start()
    try:
        return overlay.run()
    except KeyboardInterrupt:
        # Ctrl-C in a terminal: exit quietly instead of dumping a traceback.
        return 0
    finally:
        pipeline.stop()


if __name__ == "__main__":
    sys.exit(main())
