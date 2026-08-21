"""Settings dialog for BabelChat — WoW-themed dark UI."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import debug_log
from app.about_tab_qt import build_about_tab
from app.config import CHANNEL_TOGGLES, AppConfig, detect_wow_path
from app.i18n import UI_LANGUAGES, tr
from app.provider_settings_qt import ProviderSettingsGroup

# DeepL supported target languages
LANGUAGES = {
    "EN": "English",
    "RU": "Russian",
    "DE": "German",
    "FR": "French",
    "ES": "Spanish",
    "IT": "Italian",
    "PT": "Portuguese",
    "PL": "Polish",
    "NL": "Dutch",
    "SV": "Swedish",
    "DA": "Danish",
    "FI": "Finnish",
    "CS": "Czech",
    "RO": "Romanian",
    "HU": "Hungarian",
    "BG": "Bulgarian",
    "EL": "Greek",
    "TR": "Turkish",
    "UK": "Ukrainian",
    "JA": "Japanese",
    "KO": "Korean",
    "ZH": "Chinese",
}

# WoW-inspired dark theme stylesheet
from app.hotkey_edit import HotkeyEdit  # noqa: E402  (re-export)


def _create_dialog_icon() -> QIcon:
    """Load icon from .ico file, or generate programmatically as fallback."""
    candidates = [
        *(
            [
                Path(getattr(sys, "_MEIPASS", "")) / "assets" / "icon.ico",
                Path(__file__).parent.parent / "assets" / "icon.ico",
                Path(getattr(sys, "_MEIPASS", "")) / "assets" / "icon.png",
                Path(__file__).parent.parent / "assets" / "icon.png",
            ]
            if sys.platform == "win32"
            else [
                # Linux: only PNG — .ico causes "Ignoring icon" warning
                Path(getattr(sys, "_MEIPASS", "")) / "assets" / "icon.png",
                Path(__file__).parent.parent / "assets" / "icon.png",
            ]
        ),
    ]
    for path in candidates:
        if path.is_file():
            return QIcon(str(path))

    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(30, 30, 30, 220))
    painter.setPen(QColor(255, 210, 0))
    painter.drawRoundedRect(1, 1, 30, 30, 4, 4)
    painter.setFont(QFont("Arial", 18, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), 0x0084, "W")  # AlignCenter
    painter.end()
    return QIcon(pixmap)


_SETTINGS_DIALOG_POS_FILE = "settings_dialog_pos.json"
from app.qt_theme import WOW_THEME_STYLESHEET  # noqa: E402  (re-export for the wizard)


class SettingsDialog(QDialog):
    """Settings window with WoW-themed dark UI."""

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        clear_cache: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        # Supplied by whoever owns the running pipeline, so 'clear the
        # cache' clears the one actually in use.
        self._clear_cache_callback = clear_cache
        self.setWindowTitle(tr("settings.title"))
        self.setWindowIcon(_create_dialog_icon())
        self.setMinimumSize(500, 520)
        self.setStyleSheet(WOW_THEME_STYLESHEET)
        self._restore_position()

        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()
        tabs.addTab(self._create_general_tab(), tr("settings.tab.general"))
        tabs.addTab(self._create_overlay_tab(), tr("settings.tab.overlay"))
        tabs.addTab(self._create_hotkeys_tab(), tr("settings.tab.hotkeys"))
        tabs.addTab(self._create_about_tab(), tr("settings.tab.about"))
        layout.addWidget(tabs)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)

        # Gold-styled Save button
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText(tr("settings.save"))
        ok_btn.setStyleSheet(
            "QPushButton { background: #3a3000; color: #FFD200; "
            "border: 1px solid #FFD200; border-radius: 3px; padding: 8px 20px; }"
            "QPushButton:hover { background: #4a4000; }"
            "QPushButton:pressed { background: #555; }"
        )

        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setText(tr("wizard.cancel"))

        layout.addWidget(buttons)

    # ── General Tab ──────────────────────────────────────────────

    def _create_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # API Key
        layout.addWidget(self._create_api_group())

        # WoW Path
        path_group = QGroupBox(tr("settings.wow_group"))
        path_layout = QFormLayout(path_group)

        wow_row = QHBoxLayout()
        self._wow_path_input = QLineEdit(self._config.wow_path)
        self._wow_path_input.setPlaceholderText("C:/Program Files/World of Warcraft")
        wow_row.addWidget(self._wow_path_input)
        browse_btn = QPushButton(tr("settings.wow.browse"))
        browse_btn.clicked.connect(self._browse_wow_path)
        wow_row.addWidget(browse_btn)
        detect_btn = QPushButton(tr("settings.wow.auto"))
        detect_btn.clicked.connect(self._auto_detect_wow)
        wow_row.addWidget(detect_btn)
        path_layout.addRow(tr("settings.wow.path"), wow_row)

        addon_row = QHBoxLayout()
        self._install_addon_btn = QPushButton(tr("settings.wow.install_addon"))
        self._install_addon_btn.setStyleSheet(
            "QPushButton { background: #3a3000; color: #FFD200; "
            "border: 1px solid #FFD200; border-radius: 3px; padding: 6px 14px; }"
            "QPushButton:hover { background: #4a4000; }"
        )
        self._install_addon_btn.clicked.connect(self._install_addon)
        addon_row.addWidget(self._install_addon_btn)
        self._addon_status = QLabel("")
        self._addon_status.setWordWrap(True)
        addon_row.addWidget(self._addon_status, stretch=1)
        path_layout.addRow("", addon_row)

        layout.addWidget(path_group)

        # Language
        lang_group = QGroupBox(tr("settings.lang_group"))
        lang_layout = QFormLayout(lang_group)

        self._ui_lang = QComboBox()
        for code, name in UI_LANGUAGES.items():
            self._ui_lang.addItem(name, code)
        self._ui_lang.setCurrentIndex(self._ui_lang.findData(self._config.ui_language))
        lang_layout.addRow(tr("settings.lang.ui"), self._ui_lang)

        self._own_lang = QComboBox()
        self._target_lang = QComboBox()
        for code, name in LANGUAGES.items():
            self._own_lang.addItem(f"{name} ({code})", code)
            self._target_lang.addItem(f"{name} ({code})", code)

        self._own_lang.setCurrentIndex(self._own_lang.findData(self._config.own_language))
        self._target_lang.setCurrentIndex(self._target_lang.findData(self._config.target_language))
        lang_layout.addRow(tr("settings.lang.own"), self._own_lang)
        lang_layout.addRow(tr("settings.lang.target"), self._target_lang)
        layout.addWidget(lang_group)

        # Channels — 3-column grid, drawn from the shared declaration so it
        # cannot drift from the Linux dialog the way it did.
        ch_group = QGroupBox(tr("settings.channels_group"))
        ch_grid = QGridLayout(ch_group)
        self._channel_boxes: dict[str, QCheckBox] = {}
        for index, (attribute, label_key) in enumerate(CHANNEL_TOGGLES):
            box = QCheckBox(tr(label_key))
            box.setChecked(getattr(self._config, attribute))
            ch_grid.addWidget(box, index // 3, index % 3)
            self._channel_boxes[attribute] = box
        layout.addWidget(ch_group)

        layout.addStretch()
        return tab

    # ── Translation providers ────────────────────────────────────

    def _create_api_group(self) -> QGroupBox:
        """Credential fields for every registered provider.

        The dialog no longer knows which providers exist: ProviderSettingsGroup
        renders whatever the registry holds, so a new provider needs no change
        here at all.
        """
        self._provider_group = ProviderSettingsGroup(self._config, self)
        return self._provider_group

    # ── Overlay Tab ──────────────────────────────────────────────

    def _create_overlay_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Appearance
        appear_group = QGroupBox(tr("settings.appearance_group"))
        appear_layout = QFormLayout(appear_group)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(50, 255)
        self._opacity_slider.setValue(self._config.overlay_opacity)
        self._opacity_label = QLabel(f"{int(self._config.overlay_opacity / 255 * 100)}%")
        self._opacity_label.setFixedWidth(40)
        self._opacity_slider.valueChanged.connect(lambda v: self._opacity_label.setText(f"{int(v / 255 * 100)}%"))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self._opacity_slider)
        opacity_row.addWidget(self._opacity_label)
        appear_layout.addRow(tr("settings.overlay.opacity"), opacity_row)

        self._font_size = QSpinBox()
        self._font_size.setRange(8, 20)
        self._font_size.setValue(self._config.overlay_font_size)
        appear_layout.addRow(tr("settings.overlay.font_size"), self._font_size)

        layout.addWidget(appear_group)

        # Behavior
        behavior_group = QGroupBox(tr("settings.behavior_group"))
        behavior_layout = QVBoxLayout(behavior_group)

        self._translate_default = QCheckBox(tr("settings.overlay.translate_default"))
        self._translate_default.setChecked(self._config.translation_enabled_default)
        behavior_layout.addWidget(self._translate_default)

        self._skip_own_messages = QCheckBox(tr("settings.overlay.skip_own_messages"))
        self._skip_own_messages.setChecked(self._config.skip_own_messages)
        behavior_layout.addWidget(self._skip_own_messages)

        self._show_console = QCheckBox(tr("settings.overlay.show_console"))
        self._show_console.setChecked(self._config.show_debug_console)
        behavior_layout.addWidget(self._show_console)

        self._capture_trace = QCheckBox(tr("settings.privacy.trace"))
        self._capture_trace.setChecked(self._config.debug_capture_trace)
        behavior_layout.addWidget(self._capture_trace)

        trace_hint = QLabel(tr("settings.privacy.trace_hint"))
        trace_hint.setStyleSheet("color: #888; font-size: 11px;")
        trace_hint.setWordWrap(True)
        behavior_layout.addWidget(trace_hint)

        clear_row = QHBoxLayout()
        self._clear_cache_btn = QPushButton(tr("settings.privacy.clear_cache"))
        self._clear_cache_btn.clicked.connect(self._clear_translation_cache)
        clear_row.addWidget(self._clear_cache_btn)
        self._clear_cache_status = QLabel(tr("settings.privacy.clear_cache_hint"))
        self._clear_cache_status.setStyleSheet("color: #888; font-size: 11px;")
        self._clear_cache_status.setWordWrap(True)
        clear_row.addWidget(self._clear_cache_status, stretch=1)
        behavior_layout.addLayout(clear_row)

        layout.addWidget(behavior_group)
        layout.addStretch()
        return tab

    def _clear_translation_cache(self) -> None:
        """Delete every cached translation, including the source text it kept."""
        if self._clear_cache_callback is None:
            self._clear_cache_status.setText(tr("settings.privacy.clear_cache_unavailable"))
            self._clear_cache_status.setStyleSheet("color: #FF7F00; font-size: 11px;")
            return
        try:
            removed = self._clear_cache_callback()
        except Exception as e:
            self._clear_cache_status.setText(tr("settings.api.error", e=str(e)))
            self._clear_cache_status.setStyleSheet("color: #FF4040; font-size: 11px;")
            return
        self._clear_cache_status.setText(tr("settings.privacy.cleared", n=removed))
        self._clear_cache_status.setStyleSheet("color: #40FF40; font-size: 11px;")

    # ── Hotkeys Tab ──────────────────────────────────────────────

    def _create_hotkeys_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        hk_group = QGroupBox(tr("settings.hk_group"))
        hk_layout = QFormLayout(hk_group)

        self._hk_toggle = HotkeyEdit(self._config.hotkey_toggle_translate)
        hk_layout.addRow(tr("settings.hk.toggle_translate"), self._hk_toggle)
        toggle_hint = QLabel(tr("settings.hk.toggle_translate_hint"))
        toggle_hint.setStyleSheet("color: #666; font-size: 10px;")
        hk_layout.addRow("", toggle_hint)

        self._hk_clipboard = HotkeyEdit(self._config.hotkey_clipboard_translate)
        hk_layout.addRow(tr("settings.hk.clipboard"), self._hk_clipboard)
        clipboard_hint = QLabel(tr("settings.hk.clipboard_hint"))
        clipboard_hint.setStyleSheet("color: #666; font-size: 10px;")
        hk_layout.addRow("", clipboard_hint)

        layout.addWidget(hk_group)
        layout.addStretch()
        return tab

    # ── About Tab ────────────────────────────────────────────────

    def _create_about_tab(self) -> QWidget:
        return build_about_tab(self)

    def _browse_wow_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("settings.wow.browse_title"))
        if path:
            self._wow_path_input.setText(path)

    def _auto_detect_wow(self) -> None:
        detected = detect_wow_path()
        if detected:
            self._wow_path_input.setText(detected)

    def _install_addon(self) -> None:
        wow = self._wow_path_input.text().strip()
        if not wow:
            self._addon_status.setText(tr("settings.wow.addon_no_path"))
            self._addon_status.setStyleSheet("color: #FF4040; font-weight: bold;")
            return

        addons_dir = Path(wow) / "_retail_" / "Interface" / "AddOns"
        if not addons_dir.parent.exists():
            self._addon_status.setText(tr("settings.wow.addon_not_found", path=addons_dir.parent))
            self._addon_status.setStyleSheet("color: #FF4040; font-weight: bold;")
            return

        if getattr(sys, "frozen", False):
            src = Path(getattr(sys, "_MEIPASS", "")) / "addon" / "BabelChat"
        else:
            src = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"

        if not src.exists():
            self._addon_status.setText(tr("settings.wow.addon_files_missing"))
            self._addon_status.setStyleSheet("color: #FF4040; font-weight: bold;")
            return

        dest = addons_dir / "BabelChat"
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            self._addon_status.setText(tr("settings.wow.addon_installed"))
            self._addon_status.setStyleSheet("color: #40FF40; font-weight: bold;")
            self._install_addon_btn.setText(tr("settings.wow.reinstall_addon"))
        except OSError as e:
            self._addon_status.setText(tr("addon.install_failed", detail=e))
            self._addon_status.setStyleSheet("color: #FF4040; font-weight: bold;")

    def _save_and_accept(self) -> None:
        # apply_to writes both the credentials and the preferred provider.
        self._provider_group.apply_to(self._config)
        self._config.wow_path = self._wow_path_input.text().strip()
        self._config.ui_language = self._ui_lang.currentData()
        self._config.own_language = self._own_lang.currentData()
        self._config.target_language = self._target_lang.currentData()
        for attribute, box in self._channel_boxes.items():
            setattr(self._config, attribute, box.isChecked())
        self._config.overlay_opacity = self._opacity_slider.value()
        self._config.overlay_font_size = self._font_size.value()
        self._config.translation_enabled_default = self._translate_default.isChecked()
        self._config.skip_own_messages = self._skip_own_messages.isChecked()
        self._config.show_debug_console = self._show_console.isChecked()
        self._config.debug_capture_trace = self._capture_trace.isChecked()
        debug_log.configure(self._config.debug_capture_trace)
        self._config.hotkey_toggle_translate = self._hk_toggle.text()
        self._config.hotkey_clipboard_translate = self._hk_clipboard.text()
        # Apply UI language change
        new_lang = self._ui_lang.currentData()
        if new_lang != tr.get_language():
            tr.set_language(new_lang)
        self._config.save()
        self.accept()

    def _restore_position(self) -> None:
        import json

        try:
            data = json.loads(Path(_SETTINGS_DIALOG_POS_FILE).read_text(encoding="utf-8"))
            self.move(data.get("x", 200), data.get("y", 200))
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    def _save_position(self) -> None:
        import contextlib
        import json

        with contextlib.suppress(OSError):
            data = {"x": self.x(), "y": self.y()}
            Path(_SETTINGS_DIALOG_POS_FILE).write_text(json.dumps(data), encoding="utf-8")

    def closeEvent(self, event: object) -> None:
        self._save_position()
        super().closeEvent(event)  # type: ignore[arg-type]

    def get_config(self) -> AppConfig:
        return self._config
