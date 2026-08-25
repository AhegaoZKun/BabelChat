"""The About tab of the settings dialog.

Mostly static presentation — a logo, a version line, links and credits —
which is exactly the kind of content that inflates a dialog module without
adding behaviour to it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.about_dialog import VERSION
from app.i18n import tr

#: Where support goes. Declared once: the same details appear in the app's About
#: tab, in the addon's options panel and in both READMEs, and three hand-written
#: copies of a payment address is exactly the kind of thing that goes stale in
#: one place and stays wrong for a year.
DONATE_CARD_URL = "https://pay.cloudtips.ru/p/ea5537e6"

WALLETS: tuple[tuple[str, str], ...] = (
    ("USDT TRC20", "TGaUz963ZaCoHrfoDDgy1sCvSrK1wsZvcx"),
    ("BTC", "1BkYvFT8iBVG3GfTqkR2aBkABNkTrhYuja"),
    ("TON", "UQDFaHBN1pcQZ7_9-w1E_hS_JNfGf3d0flS_467w7LOQ7xbK"),
)



def build_about_tab(dialog) -> QWidget:
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setSpacing(12)

    # Title + version
    title = QLabel(f"BabelChat {VERSION}")
    title.setStyleSheet("color: #FFD200; font-size: 18px; font-weight: bold;")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(title)

    subtitle = QLabel(tr("about.subtitle"))
    subtitle.setStyleSheet("color: #ccc; font-size: 12px;")
    subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(subtitle)

    # Developer
    dev = QLabel(f"{tr('about.developer')} <b>Andrey Yumashev</b>")
    dev.setStyleSheet("color: #ccc; font-size: 12px;")
    dev.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(dev)

    # License
    lic = QLabel(tr("about.license"))
    lic.setStyleSheet("color: #999; font-size: 11px;")
    lic.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(lic)

    # GitHub
    github = QLabel(
        '<a href="https://github.com/Yumash/BabelChat" style="color: #FFD200;">GitHub: Yumash/BabelChat</a>'
    )
    github.setAlignment(Qt.AlignmentFlag.AlignCenter)
    github.setOpenExternalLinks(True)
    layout.addWidget(github)

    # ── Glossary credit ──
    sep1 = QLabel()
    sep1.setFixedHeight(1)
    sep1.setStyleSheet("background: #444;")
    layout.addWidget(sep1)

    glossary_credit = QLabel(tr("about.glossary_credit"))
    glossary_credit.setStyleSheet("color: #ccc; font-size: 11px;")
    glossary_credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
    glossary_credit.setOpenExternalLinks(True)
    glossary_credit.setWordWrap(True)
    layout.addWidget(glossary_credit)

    # ── Donate: Pirson (WoW Dictionary) ──
    sep2 = QLabel()
    sep2.setFixedHeight(1)
    sep2.setStyleSheet("background: #444;")
    layout.addWidget(sep2)

    dict_donate_title = QLabel(tr("about.donate_dictionary"))
    dict_donate_title.setStyleSheet("color: #FFD200; font-size: 12px; font-weight: bold;")
    dict_donate_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(dict_donate_title)

    dict_donate_desc = QLabel(tr("about.donate_dictionary_desc"))
    dict_donate_desc.setStyleSheet("color: #999; font-size: 11px;")
    dict_donate_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dict_donate_desc.setWordWrap(True)
    layout.addWidget(dict_donate_desc)

    pirson_link = QLabel(
        '<a href="https://buymeacoffee.com/franciscorb" '
        'style="color: #FFD200; font-size: 12px;">'
        "Buy Me a Coffee — Pirson</a>"
    )
    pirson_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pirson_link.setOpenExternalLinks(True)
    layout.addWidget(pirson_link)

    # ── Donate: Companion App ──
    sep3 = QLabel()
    sep3.setFixedHeight(1)
    sep3.setStyleSheet("background: #444;")
    layout.addWidget(sep3)

    app_donate_title = QLabel(tr("about.donate_app"))
    app_donate_title.setStyleSheet("color: #FFD200; font-size: 12px; font-weight: bold;")
    app_donate_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(app_donate_title)

    app_donate_desc = QLabel(tr("about.donate_app_desc"))
    app_donate_desc.setStyleSheet("color: #999; font-size: 11px;")
    app_donate_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
    app_donate_desc.setWordWrap(True)
    layout.addWidget(app_donate_desc)

    card_link = QLabel(
        f'<a href="{DONATE_CARD_URL}" style="color: #FFD200; font-size: 12px;">'
        f"{tr('about.donate_card')}</a>"
    )
    card_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
    card_link.setOpenExternalLinks(True)
    layout.addWidget(card_link)

    for label, addr in WALLETS:
        row = QHBoxLayout()
        crypto_label = QLabel(f"<b>{label}:</b>")
        crypto_label.setStyleSheet("color: #ccc; font-size: 11px;")
        crypto_label.setFixedWidth(90)
        row.addWidget(crypto_label)

        addr_field = QLineEdit(addr)
        addr_field.setReadOnly(True)
        addr_field.setStyleSheet(
            "color: #e0e0e0; font-size: 10px; background: #111; "
            "border: 1px solid #444; border-radius: 3px; padding: 4px;"
        )
        row.addWidget(addr_field)

        copy_btn = QPushButton(tr("overlay.reply.copy"))
        copy_btn.setFixedWidth(80)
        copy_btn.clicked.connect(lambda checked, a=addr: QApplication.clipboard().setText(a))
        row.addWidget(copy_btn)
        layout.addLayout(row)

    layout.addStretch()
    return tab

# ── Actions ──────────────────────────────────────────────────
