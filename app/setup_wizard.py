"""First-run setup wizard for BabelChat — WoW-themed."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.about_dialog import _create_logo_pixmap
from app.config import AppConfig, detect_wow_path
from app.i18n import UI_LANGUAGES, tr
from app.settings_dialog import (
    LANGUAGES,
    WOW_THEME_STYLESHEET,
    _create_dialog_icon,
)
from app.translator import validate_deepl_key, validate_microsoft_key

PAGE_WELCOME = 0
PAGE_API_KEY = 1
PAGE_WOW_PATH = 2
PAGE_LANGUAGE = 3
PAGE_READY = 4
TOTAL_PAGES = 5

# Gold-styled primary action button
_GOLD_BTN_STYLE = (
    "QPushButton { background: #3a3000; color: #FFD200; "
    "border: 1px solid #FFD200; border-radius: 3px; padding: 8px 20px; }"
    "QPushButton:hover { background: #4a4000; }"
    "QPushButton:pressed { background: #555; }"
    "QPushButton:disabled { background: #222; color: #666; "
    "border-color: #444; }"
)


class SetupWizard(QDialog):
    """First-run setup wizard with WoW-themed dark UI."""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._deepl_validated = False
        self._ms_validated = False
        self._key_validated = False  # at least one validated
        self.setWindowTitle(tr("wizard.title"))
        self.setWindowIcon(_create_dialog_icon())
        self.setMinimumSize(550, 480)
        self.setStyleSheet(WOW_THEME_STYLESHEET)

        main_layout = QVBoxLayout(self)

        # Step indicator
        main_layout.addWidget(self._create_step_indicator())
        main_layout.addWidget(self._separator())

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.addWidget(self._create_welcome_page())
        self._stack.addWidget(self._create_api_key_page())
        self._stack.addWidget(self._create_wow_path_page())
        self._stack.addWidget(self._create_language_page())
        self._stack.addWidget(self._create_ready_page())
        main_layout.addWidget(self._stack, stretch=1)

        # Navigation
        main_layout.addWidget(self._separator())
        nav = QHBoxLayout()

        self._cancel_btn = QPushButton(tr("wizard.cancel"))
        self._cancel_btn.clicked.connect(self.reject)
        nav.addWidget(self._cancel_btn)
        nav.addStretch()

        self._back_btn = QPushButton(tr("wizard.back"))
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)

        self._next_btn = QPushButton(tr("wizard.next"))
        self._next_btn.setStyleSheet(_GOLD_BTN_STYLE)
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)

        main_layout.addLayout(nav)
        self._update_navigation()

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _separator() -> QLabel:
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #444;")
        return sep

    # ── Step indicator ────────────────────────────────────────────

    def _create_step_indicator(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 8)

        self._step_dots: list[QLabel] = []
        for _ in range(TOTAL_PAGES):
            dot = QLabel()
            dot.setFixedSize(12, 12)
            layout.addWidget(dot)
            self._step_dots.append(dot)

        layout.addSpacing(8)
        self._step_text = QLabel("")
        self._step_text.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(self._step_text)
        layout.addStretch()
        return widget

    def _update_step_indicator(self) -> None:
        current = self._stack.currentIndex()
        for i, dot in enumerate(self._step_dots):
            if i < current:
                dot.setStyleSheet("background: #997D00; border-radius: 6px;")
            elif i == current:
                dot.setStyleSheet("background: #FFD200; border-radius: 6px;")
            else:
                dot.setStyleSheet("background: #555; border-radius: 6px;")

        step_names = tr("wizard.steps").split("|")
        name = step_names[current] if current < len(step_names) else ""
        self._step_text.setText(tr("wizard.step_of", current=current + 1, total=TOTAL_PAGES, name=name))

    # ── Page 1: Welcome ──────────────────────────────────────────

    def _create_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()

        # Logo
        logo = QLabel()
        logo.setPixmap(_create_logo_pixmap())
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)

        layout.addSpacing(12)

        # Title
        self._welcome_title = QLabel(tr("wizard.welcome.title"))
        self._welcome_title.setStyleSheet("color: #FFD200; font-size: 22px; font-weight: bold;")
        self._welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._welcome_title)

        layout.addSpacing(8)

        # Description
        self._welcome_desc = QLabel(tr("wizard.welcome.desc"))
        self._welcome_desc.setStyleSheet("color: #ccc; font-size: 13px;")
        self._welcome_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._welcome_desc.setWordWrap(True)
        layout.addWidget(self._welcome_desc)

        layout.addSpacing(16)

        # UI language selector
        lang_row = QHBoxLayout()
        lang_row.addStretch()
        ui_lang_label = QLabel(tr("wizard.welcome.ui_lang"))
        ui_lang_label.setStyleSheet("color: #999; font-size: 12px;")
        lang_row.addWidget(ui_lang_label)

        self._ui_lang_combo = QComboBox()
        self._ui_lang_combo.setStyleSheet("QComboBox { min-width: 140px; padding: 6px 8px; }")
        for code, name in UI_LANGUAGES.items():
            self._ui_lang_combo.addItem(name, code)
        self._ui_lang_combo.setCurrentIndex(self._ui_lang_combo.findData(tr.get_language()))
        self._ui_lang_combo.currentIndexChanged.connect(self._on_ui_lang_changed)
        lang_row.addWidget(self._ui_lang_combo)
        lang_row.addStretch()
        layout.addLayout(lang_row)

        layout.addStretch()
        return page

    def _on_ui_lang_changed(self) -> None:
        lang = self._ui_lang_combo.currentData()
        if lang and lang != tr.get_language():
            tr.set_language(lang)
            self._config.ui_language = lang
            # Signal main to restart wizard with new language
            self._restart_requested = True
            self.done(2)  # Custom result code: restart

    # ── Page 2: DeepL API Key ────────────────────────────────────

    def _create_api_key_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel(tr("wizard.api.title"))
        title.setStyleSheet("color: #FFD200; font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        layout.addSpacing(4)

        explain = QLabel(
            "These keys are only for full-sentence translation — the in-game dictionary already works for free "
            "without any of them. Configure at least one provider if you want sentence translation; if both are "
            "set, you can choose which takes priority."
        )
        explain.setStyleSheet("color: #ccc; font-size: 12px;")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        layout.addSpacing(10)

        # ── DeepL ──────────────────────────────────────────────────
        deepl_group = QGroupBox("DeepL")
        deepl_group.setStyleSheet("QGroupBox { color: #FFD200; font-weight: bold; }")
        deepl_layout = QVBoxLayout(deepl_group)

        deepl_link_row = QHBoxLayout()
        signup = QLabel(
            '<a href="https://www.deepl.com/pro-api" '
            'style="color: #FFD200; font-size: 11px;">'
            "Get free API key (1M chars, one-time)</a>"
        )
        signup.setOpenExternalLinks(True)
        deepl_link_row.addWidget(signup)
        deepl_link_row.addStretch()
        deepl_layout.addLayout(deepl_link_row)

        deepl_hint = QLabel(
            "Note: DeepL's free tier asks for a credit card to verify your account — it never charges you."
        )
        deepl_hint.setStyleSheet("color: #999; font-size: 10px;")
        deepl_hint.setWordWrap(True)
        deepl_layout.addWidget(deepl_hint)

        self._api_key_input = QLineEdit(self._config.deepl_api_key)
        self._api_key_input.setPlaceholderText("Translator API key (ends with :fx for free tier)")
        self._api_key_input.textChanged.connect(self._on_api_key_changed)
        deepl_layout.addWidget(self._api_key_input)

        deepl_action = QHBoxLayout()
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.clicked.connect(self._validate_deepl_key)
        deepl_action.addWidget(self._validate_btn)
        self._api_status_label = QLabel("")
        self._api_status_label.setWordWrap(True)
        deepl_action.addWidget(self._api_status_label, stretch=1)
        deepl_layout.addLayout(deepl_action)
        layout.addWidget(deepl_group)

        layout.addSpacing(8)

        # ── Microsoft Translator ───────────────────────────────────
        ms_group = QGroupBox("Microsoft Translator")
        ms_group.setStyleSheet("QGroupBox { color: #FFD200; font-weight: bold; }")
        ms_layout = QVBoxLayout(ms_group)

        ms_link_row = QHBoxLayout()
        ms_link = QLabel(
            '<a href="https://portal.azure.com/" '
            'style="color: #FFD200; font-size: 11px;">'
            "Get free key (2M chars/month, Azure — more setup required)</a>"
        )
        ms_link.setOpenExternalLinks(True)
        ms_link_row.addWidget(ms_link)
        ms_link_row.addStretch()
        ms_layout.addLayout(ms_link_row)

        ms_hint = QLabel("Free — no credit card required.")
        ms_hint.setStyleSheet("color: #40FF40; font-size: 10px;")
        ms_layout.addWidget(ms_hint)

        self._ms_key_input = QLineEdit(self._config.microsoft_api_key)
        self._ms_key_input.setPlaceholderText("Microsoft Translator API key")
        self._ms_key_input.textChanged.connect(self._on_ms_key_changed)
        ms_layout.addWidget(self._ms_key_input)

        self._ms_region_input = QLineEdit(getattr(self._config, "microsoft_region", ""))
        self._ms_region_input.setPlaceholderText("Azure region (e.g. germanywestcentral, eastus, westeurope)")
        ms_layout.addWidget(self._ms_region_input)

        ms_action = QHBoxLayout()
        self._ms_validate_btn = QPushButton("Validate")
        self._ms_validate_btn.clicked.connect(self._validate_ms_key)
        ms_action.addWidget(self._ms_validate_btn)
        self._ms_status_label = QLabel("")
        self._ms_status_label.setWordWrap(True)
        ms_action.addWidget(self._ms_status_label, stretch=1)
        ms_layout.addLayout(ms_action)
        layout.addWidget(ms_group)

        layout.addSpacing(8)

        # ── Priority (shown only when both validated) ──────────────
        self._priority_widget = QWidget()
        priority_layout = QHBoxLayout(self._priority_widget)
        priority_layout.setContentsMargins(0, 0, 0, 0)
        priority_label = QLabel("Priority:")
        priority_label.setStyleSheet("color: #ccc;")
        priority_layout.addWidget(priority_label)
        self._priority_combo = QComboBox()
        self._priority_combo.addItem("DeepL first", "deepl")
        self._priority_combo.addItem("Microsoft first", "microsoft")
        idx = self._priority_combo.findData(getattr(self._config, "translator_priority", "deepl"))
        if idx >= 0:
            self._priority_combo.setCurrentIndex(idx)
        priority_layout.addWidget(self._priority_combo)
        priority_note = QLabel("— the other acts as fallback")
        priority_note.setStyleSheet("color: #888; font-size: 11px;")
        priority_layout.addWidget(priority_note)
        priority_layout.addStretch()
        self._priority_widget.hide()
        layout.addWidget(self._priority_widget)

        layout.addStretch()
        return page

    def _on_api_key_changed(self, text: str) -> None:
        self._deepl_validated = False
        self._key_validated = self._ms_validated
        self._api_status_label.setText("")
        if self._stack.currentIndex() == PAGE_API_KEY:
            self._next_btn.setEnabled(self._key_validated)

    def _on_ms_key_changed(self, text: str) -> None:
        self._ms_validated = False
        self._key_validated = self._deepl_validated
        self._ms_status_label.setText("")
        if self._stack.currentIndex() == PAGE_API_KEY:
            self._next_btn.setEnabled(self._key_validated)

    def _validate_deepl_key(self) -> None:
        key = self._api_key_input.text().strip()
        if not key:
            self._set_api_status("unconfigured", "Enter a DeepL API key first")
            return
        self._validate_btn.setEnabled(False)
        self._validate_btn.setText("Validating...")
        QApplication.processEvents()
        valid, msg = validate_deepl_key(key)
        self._validate_btn.setEnabled(True)
        self._validate_btn.setText("Validate")
        if valid:
            self._deepl_validated = True
            detail = f" — {msg}" if msg not in ("valid",) else ""
            self._set_api_status("valid", f"✓ Valid{detail}")
        else:
            self._deepl_validated = False
            msgs = {"auth_failed": "Invalid key", "no_key": "No key entered"}
            self._set_api_status("invalid", f"✗ {msgs.get(msg, msg)}")
        self._update_api_page_state()

    def _validate_ms_key(self) -> None:
        key = self._ms_key_input.text().strip()
        if not key:
            self._set_ms_status("unconfigured", "Enter a Microsoft Translator key first")
            return
        self._ms_validate_btn.setEnabled(False)
        self._ms_validate_btn.setText("Validating...")
        QApplication.processEvents()
        region = self._ms_region_input.text().strip()
        valid, msg = validate_microsoft_key(key, region)
        self._ms_validate_btn.setEnabled(True)
        self._ms_validate_btn.setText("Validate")
        if valid:
            self._ms_validated = True
            self._set_ms_status("valid", "✓ Valid — 2M chars/month free")
        else:
            self._ms_validated = False
            msgs = {"auth_failed": "Invalid key", "no_key": "No key entered"}
            self._set_ms_status("invalid", f"✗ {msgs.get(msg, msg)}")
        self._update_api_page_state()

    def _update_api_page_state(self) -> None:
        self._key_validated = self._deepl_validated or self._ms_validated
        self._next_btn.setEnabled(self._key_validated)
        # Show priority selector only when both are validated
        if self._deepl_validated and self._ms_validated:
            self._priority_widget.show()
        else:
            self._priority_widget.hide()

    def _validate_api_key(self) -> None:
        self._validate_deepl_key()

    def _set_api_status(self, state: str, message: str) -> None:
        color = {
            "unconfigured": "#999",
            "valid": "#40FF40",
            "invalid": "#FF4040",
            "error": "#FF7F00",
        }.get(state, "#999")
        self._api_status_label.setText(message)
        self._api_status_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _set_ms_status(self, state: str, message: str) -> None:
        color = {
            "unconfigured": "#999",
            "valid": "#40FF40",
            "invalid": "#FF4040",
            "error": "#FF7F00",
        }.get(state, "#999")
        self._ms_status_label.setText(message)
        self._ms_status_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    # ── Page 3: WoW Path ─────────────────────────────────────────

    def _create_wow_path_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel(tr("wizard.wow.title"))
        title.setStyleSheet("color: #FFD200; font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        layout.addSpacing(4)

        explain = QLabel(tr("wizard.wow.explain"))
        explain.setStyleSheet("color: #ccc; font-size: 12px;")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        layout.addSpacing(12)

        # Path input + Browse
        path_row = QHBoxLayout()
        self._wow_path_input = QLineEdit(self._config.wow_path)
        self._wow_path_input.setPlaceholderText("C:/Program Files/World of Warcraft")
        path_row.addWidget(self._wow_path_input, stretch=1)

        browse_btn = QPushButton(tr("wizard.wow.browse"))
        browse_btn.clicked.connect(self._browse_wow_path)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        # Status
        self._wow_status_label = QLabel("")
        self._wow_status_label.setWordWrap(True)
        layout.addWidget(self._wow_status_label)

        layout.addSpacing(8)

        hint = QLabel(tr("wizard.wow.skip_hint"))
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _auto_detect_wow(self) -> None:
        import sys

        if sys.platform != "win32":
            # On Linux, auto-detection is unreliable — open the file browser directly
            self._browse_wow_path()
            return
        detected = detect_wow_path()
        if detected:
            self._wow_path_input.setText(detected)
            self._wow_status_label.setText(tr("wizard.wow.found"))
            self._wow_status_label.setStyleSheet("color: #40FF40; font-weight: bold;")
        else:
            self._wow_status_label.setText(tr("wizard.wow.not_found"))
            self._wow_status_label.setStyleSheet("color: #FF7F00; font-weight: bold;")

    def _browse_wow_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("wizard.wow.browse_title"))
        if path:
            self._wow_path_input.setText(path)
            self._wow_status_label.setText(tr("wizard.wow.path_set"))
            self._wow_status_label.setStyleSheet("color: #40FF40; font-weight: bold;")

    # ── Page 4: Language ──────────────────────────────────────────

    def _create_language_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel(tr("wizard.lang.title"))
        title.setStyleSheet("color: #FFD200; font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        layout.addSpacing(8)

        own_label = QLabel(tr("wizard.lang.own"))
        own_label.setStyleSheet("color: #ccc; font-size: 13px;")
        layout.addWidget(own_label)

        self._own_lang = QComboBox()
        for code, name in LANGUAGES.items():
            self._own_lang.addItem(f"{name} ({code})", code)
        self._own_lang.setCurrentIndex(self._own_lang.findData(self._config.own_language))
        layout.addWidget(self._own_lang)

        layout.addSpacing(12)

        target_label = QLabel(tr("wizard.lang.target"))
        target_label.setStyleSheet("color: #ccc; font-size: 13px;")
        layout.addWidget(target_label)

        self._target_lang = QComboBox()
        for code, name in LANGUAGES.items():
            self._target_lang.addItem(f"{name} ({code})", code)
        self._target_lang.setCurrentIndex(self._target_lang.findData(self._config.target_language))
        layout.addWidget(self._target_lang)

        layout.addSpacing(12)

        hint = QLabel(tr("wizard.lang.hint"))
        hint.setStyleSheet("color: #999; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    # ── Page 5: Ready ─────────────────────────────────────────────

    def _create_ready_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()

        title = QLabel(tr("wizard.ready.title"))
        title.setStyleSheet("color: #FFD200; font-size: 20px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(8)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #ccc; font-size: 12px;")
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        layout.addSpacing(12)

        # Addon install
        addon_group = QGroupBox(tr("wizard.ready.addon_group"))
        addon_layout = QVBoxLayout(addon_group)
        addon_text = QLabel(tr("wizard.ready.addon_text"))
        addon_text.setWordWrap(True)
        addon_text.setStyleSheet("color: #ccc; font-size: 12px;")
        addon_layout.addWidget(addon_text)

        addon_layout.addSpacing(4)

        self._install_addon_btn = QPushButton(tr("wizard.ready.install_addon"))
        self._install_addon_btn.setStyleSheet(_GOLD_BTN_STYLE)
        self._install_addon_btn.clicked.connect(self._install_addon)
        addon_layout.addWidget(self._install_addon_btn)

        self._addon_status_label = QLabel("")
        self._addon_status_label.setWordWrap(True)
        addon_layout.addWidget(self._addon_status_label)

        layout.addWidget(addon_group)

        layout.addSpacing(8)

        closing = QLabel(tr("wizard.ready.closing"))
        closing.setStyleSheet("color: #999; font-size: 11px;")
        closing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        closing.setWordWrap(True)
        layout.addWidget(closing)

        layout.addStretch()
        return page

    @staticmethod
    def _addon_source_path() -> Path:
        """Return path to bundled BabelChat addon folder."""
        if getattr(sys, "frozen", False):
            # PyInstaller onefile: data extracted to _MEIPASS temp dir
            base = Path(getattr(sys, "_MEIPASS", ""))
        else:
            base = Path(__file__).resolve().parent.parent
        return base / "addon" / "BabelChat"

    def _install_addon(self) -> None:
        wow = self._wow_path_input.text().strip()
        if not wow:
            self._addon_status_label.setText(tr("wizard.ready.addon_no_path"))
            self._addon_status_label.setStyleSheet("color: #FF4040; font-weight: bold;")
            return

        addons_dir = Path(wow) / "_retail_" / "Interface" / "AddOns"
        if not addons_dir.parent.exists():
            self._addon_status_label.setText(tr("wizard.ready.addon_path_not_found", path=addons_dir.parent))
            self._addon_status_label.setStyleSheet("color: #FF4040; font-weight: bold;")
            return

        src = self._addon_source_path()
        if not src.exists():
            self._addon_status_label.setText(tr("wizard.ready.addon_files_missing"))
            self._addon_status_label.setStyleSheet("color: #FF4040; font-weight: bold;")
            return

        dest = addons_dir / "BabelChat"
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            self._addon_status_label.setText(tr("wizard.ready.addon_installed", dest=dest))
            self._addon_status_label.setStyleSheet("color: #40FF40; font-weight: bold;")
            self._install_addon_btn.setText(tr("wizard.ready.reinstall_addon"))
        except OSError as e:
            self._addon_status_label.setText(f"\u2717 {e}")
            self._addon_status_label.setStyleSheet("color: #FF4040; font-weight: bold;")

    def _update_summary(self) -> None:
        deepl_key = self._api_key_input.text().strip()
        ms_key = self._ms_key_input.text().strip()
        own = LANGUAGES.get(self._own_lang.currentData(), "?")
        target = LANGUAGES.get(self._target_lang.currentData(), "?")
        wow = self._wow_path_input.text() or tr("wizard.ready.not_configured")

        backends = []
        if deepl_key:
            masked = f"****{deepl_key[-4:]}" if len(deepl_key) >= 4 else "****"
            backends.append(f"DeepL ({masked})")
        if ms_key:
            masked = f"****{ms_key[-4:]}" if len(ms_key) >= 4 else "****"
            backends.append(f"Microsoft ({masked})")
        backend_str = ", ".join(backends) if backends else "None"

        priority_str = ""
        if deepl_key and ms_key:
            p = self._priority_combo.currentData()
            priority_str = f"<br><b>Priority:</b> {'DeepL' if p == 'deepl' else 'Microsoft'} (other as fallback)"

        self._summary_label.setText(
            f"<b>Translation:</b> {backend_str}{priority_str}<br>"
            f"<b>{tr('wizard.ready.wow_path')}</b> {wow}<br>"
            f"<b>{tr('wizard.ready.own_lang')}</b> {own}<br>"
            f"<b>{tr('wizard.ready.target_lang')}</b> {target}"
        )

    # ── Navigation ────────────────────────────────────────────────

    def _go_next(self) -> None:
        current = self._stack.currentIndex()
        if current == TOTAL_PAGES - 1:
            self._finish()
            return
        self._stack.setCurrentIndex(current + 1)
        self._on_page_entered(current + 1)
        self._update_navigation()

    def _go_back(self) -> None:
        current = self._stack.currentIndex()
        if current > 0:
            self._stack.setCurrentIndex(current - 1)
            self._on_page_entered(current - 1)
            self._update_navigation()

    def _on_page_entered(self, index: int) -> None:
        if index == PAGE_WOW_PATH:
            if not self._wow_path_input.text().strip():
                self._auto_detect_wow()
        elif index == PAGE_LANGUAGE:
            self._apply_language_defaults()
        elif index == PAGE_READY:
            self._update_summary()

    def _apply_language_defaults(self) -> None:
        """Set smart defaults based on UI language selection.

        Russian UI → own=EN, translate to=RU (русский переводит НА русский)
        English UI → own=RU, translate to=EN (англичанин переводит НА английский)
        """
        ui_lang = tr.get_language()
        if ui_lang == "RU":
            self._own_lang.setCurrentIndex(self._own_lang.findData("EN"))
            self._target_lang.setCurrentIndex(self._target_lang.findData("RU"))
        else:
            self._own_lang.setCurrentIndex(self._own_lang.findData("RU"))
            self._target_lang.setCurrentIndex(self._target_lang.findData("EN"))

    def _update_navigation(self) -> None:
        current = self._stack.currentIndex()

        self._back_btn.setVisible(current > 0)

        if current == TOTAL_PAGES - 1:
            self._next_btn.setText(tr("wizard.start"))
        else:
            self._next_btn.setText(tr("wizard.next"))

        if current == PAGE_API_KEY:
            self._next_btn.setEnabled(self._key_validated)
        else:
            self._next_btn.setEnabled(True)
        # Update summary on ready page if navigating back
        if current == PAGE_READY:
            self._update_summary()

        self._update_step_indicator()

    # ── Finalization ──────────────────────────────────────────────

    def _finish(self) -> None:
        self._config.deepl_api_key = self._api_key_input.text().strip()
        self._config.microsoft_api_key = self._ms_key_input.text().strip()
        self._config.microsoft_region = self._ms_region_input.text().strip()
        self._config.translator_priority = self._priority_combo.currentData()
        self._config.wow_path = self._wow_path_input.text().strip()
        self._config.own_language = self._own_lang.currentData()
        self._config.target_language = self._target_lang.currentData()
        self._config.ui_language = tr.get_language()
        self._config.save()
        self.accept()

    def get_config(self) -> AppConfig:
        return self._config
