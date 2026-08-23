"""The first and last pages of the setup wizard.

Both are mostly copy and layout — the welcome page explains what the app
does, the ready page summarises what was chosen — so they carry a lot of
lines and very little behaviour.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.about_dialog import _create_logo_pixmap
from app.i18n import UI_LANGUAGES, tr
from app.wizard_style import GOLD_BTN_STYLE as _GOLD_BTN_STYLE


def build_welcome_page(wizard) -> QWidget:
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
    wizard._welcome_title = QLabel(tr("wizard.welcome.title"))
    wizard._welcome_title.setStyleSheet("color: #FFD200; font-size: 22px; font-weight: bold;")
    wizard._welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(wizard._welcome_title)

    layout.addSpacing(8)

    # Description
    wizard._welcome_desc = QLabel(tr("wizard.welcome.desc"))
    wizard._welcome_desc.setStyleSheet("color: #ccc; font-size: 13px;")
    wizard._welcome_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
    wizard._welcome_desc.setWordWrap(True)
    layout.addWidget(wizard._welcome_desc)

    layout.addSpacing(16)

    # UI language selector
    lang_row = QHBoxLayout()
    lang_row.addStretch()
    ui_lang_label = QLabel(tr("wizard.welcome.ui_lang"))
    ui_lang_label.setStyleSheet("color: #999; font-size: 12px;")
    lang_row.addWidget(ui_lang_label)

    wizard._ui_lang_combo = QComboBox()
    wizard._ui_lang_combo.setStyleSheet("QComboBox { min-width: 140px; padding: 6px 8px; }")
    for code, name in UI_LANGUAGES.items():
        wizard._ui_lang_combo.addItem(name, code)
    wizard._ui_lang_combo.setCurrentIndex(wizard._ui_lang_combo.findData(tr.get_language()))
    wizard._ui_lang_combo.currentIndexChanged.connect(wizard._on_ui_lang_changed)
    lang_row.addWidget(wizard._ui_lang_combo)
    lang_row.addStretch()
    layout.addLayout(lang_row)

    layout.addStretch()
    return page


def build_ready_page(wizard) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.addStretch()

    title = QLabel(tr("wizard.ready.title"))
    title.setStyleSheet("color: #FFD200; font-size: 20px; font-weight: bold;")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(title)

    layout.addSpacing(8)

    wizard._summary_label = QLabel("")
    wizard._summary_label.setStyleSheet("color: #ccc; font-size: 12px;")
    wizard._summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    wizard._summary_label.setWordWrap(True)
    layout.addWidget(wizard._summary_label)

    layout.addSpacing(12)

    # Addon install
    addon_group = QGroupBox(tr("wizard.ready.addon_group"))
    addon_layout = QVBoxLayout(addon_group)
    addon_text = QLabel(tr("wizard.ready.addon_text"))
    addon_text.setWordWrap(True)
    addon_text.setStyleSheet("color: #ccc; font-size: 12px;")
    addon_layout.addWidget(addon_text)

    addon_layout.addSpacing(4)

    wizard._install_addon_btn = QPushButton(tr("wizard.ready.install_addon"))
    wizard._install_addon_btn.setStyleSheet(_GOLD_BTN_STYLE)
    wizard._install_addon_btn.clicked.connect(wizard._install_addon)
    addon_layout.addWidget(wizard._install_addon_btn)

    wizard._addon_status_label = QLabel("")
    wizard._addon_status_label.setWordWrap(True)
    addon_layout.addWidget(wizard._addon_status_label)

    layout.addWidget(addon_group)

    layout.addSpacing(8)

    closing = QLabel(tr("wizard.ready.closing"))
    closing.setStyleSheet("color: #999; font-size: 11px;")
    closing.setAlignment(Qt.AlignmentFlag.AlignCenter)
    closing.setWordWrap(True)
    layout.addWidget(closing)

    layout.addStretch()
    return page
