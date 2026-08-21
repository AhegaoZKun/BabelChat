"""The translation-provider section of the settings dialog, drawn from the registry.

Nothing here names a provider. Each one declares its own credential fields, and
this widget renders whatever it finds — so adding a provider is a change in one
module rather than a hunt through the settings dialog and the setup wizard.
"""

from __future__ import annotations

import logging
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.translators import ProviderSpec, all_providers

logger = logging.getLogger(__name__)

# A validate() detail like "123,456/500,000 (24%)" — every provider that reports
# a quota this way gets a usage bar, rather than DeepL getting a special case.
_USAGE_DETAIL = re.compile(r"^([\d,]+)\s*/\s*([\d,]+)\s*\((\d+)%\)$")

_STATUS_COLORS = {
    "unconfigured": "#999",
    "valid": "#40FF40",
    "invalid": "#FF4040",
    "error": "#FF7F00",
}
_STATUS_ICONS = {"unconfigured": "•", "valid": "✓", "invalid": "✗", "error": "⚠"}

_HEADER_STYLE = "color: #FFD200; font-weight: bold; font-size: 12px;"
_NOTE_STYLE = "color: #888; font-size: 11px;"
_STATUS_STYLE = "font-weight: bold; font-size: 11px;"


class _UsageBar(QWidget):
    """Quota bar, shown only when a provider reports one."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)

        header = QHBoxLayout()
        title = QLabel(tr("settings.api.usage"))
        title.setStyleSheet("color: #999; font-size: 11px;")
        header.addWidget(title)
        self._detail = QLabel("")
        self._detail.setStyleSheet("color: #999; font-size: 11px;")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(self._detail)
        layout.addLayout(header)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setFixedHeight(14)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)

        # A quota bar and its caption are not compressible: when the dialog was
        # short of vertical space the layout took the difference out of them,
        # handing this widget 26px against a 41px hint and leaving a 9px line of
        # 11px text with its bottom half cut off. Putting the tabs behind scroll
        # areas is what actually removed the pressure — this is here so a tab
        # that ever stops scrolling squeezes something else instead.
        for label in (title, self._detail):
            label.setMinimumHeight(label.fontMetrics().height())
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.hide()

    def show_detail(self, detail: str) -> bool:
        """Render `detail` if it looks like a quota; return whether it did."""
        match = _USAGE_DETAIL.match(detail.strip())
        if not match:
            self.hide()
            return False
        used, limit, percent = match.group(1), match.group(2), int(match.group(3))
        self._bar.setValue(percent)
        self._detail.setText(f"{used} / {limit} ({percent}%)")
        colour = "#FF4040" if percent >= 90 else "#FF7F00" if percent >= 70 else "#FFD200"
        self._bar.setStyleSheet(f"QProgressBar::chunk {{ background: {colour}; border-radius: 3px; }}")
        self.show()
        return True


class _ProviderRow:
    """One provider's inputs, status line and optional usage bar."""

    def __init__(self, spec: ProviderSpec, settings: dict[str, str], layout: QVBoxLayout) -> None:
        self.spec = spec
        self.inputs: dict[str, QLineEdit] = {}
        self.validate_button = QPushButton(tr("settings.api.validate"))

        header = QLabel(spec.display_name)
        header.setStyleSheet(_HEADER_STYLE)
        layout.addWidget(header)

        if spec.note:
            note = QLabel(spec.note_text())
            note.setStyleSheet(_NOTE_STYLE)
            note.setWordWrap(True)
            layout.addWidget(note)

        if spec.guide:
            guide = QLabel(
                f'<a href="{spec.guide}" style="color: #FFD200; font-size: 11px;">'
                f"{tr('provider.guide')}</a>"
            )
            guide.setOpenExternalLinks(True)
            guide.setStyleSheet("font-size: 11px;")
            layout.addWidget(guide)

        for index, field in enumerate(spec.fields):
            edit = QLineEdit(settings.get(field.key, ""))
            edit.setPlaceholderText(field.placeholder_text() or field.label_text())
            if field.secret:
                # A key rendered in the clear ends up in screenshots and support
                # threads. Visible on demand, hidden by default.
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.inputs[field.key] = edit

            row = QHBoxLayout()
            row.addWidget(edit, stretch=1)

            if field.secret:
                reveal = QPushButton("👁")
                reveal.setCheckable(True)
                reveal.setFixedWidth(30)
                reveal.setToolTip(tr("settings.api.reveal"))
                reveal.toggled.connect(
                    lambda shown, e=edit: e.setEchoMode(
                        QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
                    )
                )
                row.addWidget(reveal)

            # The validate button belongs to the provider, not to a field, so it
            # sits on the first row and checks all of them together.
            if index == 0:
                row.addWidget(self.validate_button)
                if field.help_url:
                    link = QLabel(
                        f'<a href="{field.help_url}" style="color: #FFD200; font-size: 11px;">'
                        f"{field.help_text()}</a>"
                    )
                    link.setOpenExternalLinks(True)
                    row.addWidget(link)

            layout.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet(_STATUS_STYLE)
        layout.addWidget(self.status)

        self.usage = _UsageBar()
        layout.addWidget(self.usage)

    @property
    def values(self) -> dict[str, str]:
        return {key: edit.text().strip() for key, edit in self.inputs.items()}

    def set_status(self, state: str, message: str) -> None:
        icon = _STATUS_ICONS.get(state, "")
        self.status.setText(f"{icon} {message}".strip())
        self.status.setStyleSheet(f"color: {_STATUS_COLORS.get(state, '#999')}; {_STATUS_STYLE}")


class ProviderSettingsGroup(QGroupBox):
    """Credential fields for every registered provider, plus the preference."""

    def __init__(self, config, parent: QWidget | None = None) -> None:
        super().__init__(tr("settings.api_group"), parent)
        self._config = config
        self._rows: dict[str, _ProviderRow] = {}

        layout = QVBoxLayout(self)
        saved = config.providers or {}

        for spec in all_providers():
            row = _ProviderRow(spec, saved.get(spec.id, {}), layout)
            row.validate_button.clicked.connect(lambda _checked, pid=spec.id: self._validate(pid))
            self._rows[spec.id] = row
            layout.addSpacing(8)

        layout.addLayout(self._build_priority_row())
        self._show_saved_state()

    # ── preference ───────────────────────────────────────────────────────

    def _build_priority_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(tr("settings.api.preferred"))
        label.setStyleSheet("color: #ccc;")
        row.addWidget(label)

        self._priority = QComboBox()
        for spec in all_providers():
            self._priority.addItem(spec.display_name, spec.id)
        index = self._priority.findData(self._config.translator_priority)
        if index >= 0:
            self._priority.setCurrentIndex(index)
        row.addWidget(self._priority)

        note = QLabel(tr("settings.api.preferred_note"))
        note.setStyleSheet(_NOTE_STYLE)
        row.addWidget(note)
        row.addStretch()
        return row

    # ── validation ───────────────────────────────────────────────────────

    def _validate(self, provider_id: str) -> None:
        row = self._rows[provider_id]
        values = row.values
        if not row.spec.is_configured(values):
            row.set_status("unconfigured", tr("settings.api.no_key"))
            row.usage.hide()
            return

        row.validate_button.setEnabled(False)
        row.validate_button.setText(tr("settings.api.validating"))
        QApplication.processEvents()
        try:
            ok, detail = row.spec.validate(values)
        except Exception as e:  # a provider must not be able to take the dialog down
            logger.warning("Validation of %s raised: %s", provider_id, e)
            ok, detail = False, str(e)
        finally:
            row.validate_button.setEnabled(True)
            row.validate_button.setText(tr("settings.api.validate"))

        if ok:
            row.set_status("valid", tr("settings.api.valid"))
            row.usage.show_detail(detail)
        else:
            known = {
                "auth_failed": tr("settings.api.invalid"),
                "no_key": tr("settings.api.no_key"),
            }
            row.set_status("invalid", known.get(detail, tr("settings.api.error", e=detail)))
            row.usage.hide()

    def _show_saved_state(self) -> None:
        saved = self._config.providers or {}
        for provider_id, row in self._rows.items():
            if row.spec.is_configured(saved.get(provider_id, {})):
                row.set_status("unconfigured", tr("settings.api.saved_hint"))
            else:
                row.set_status("unconfigured", tr("settings.api.not_configured"))

    # ── persistence ──────────────────────────────────────────────────────

    def values_for(self, provider_id: str) -> dict[str, str]:
        """What is currently typed for one provider, before it is saved."""
        row = self._rows.get(provider_id)
        return row.values if row else {}

    def preferred_id(self) -> str:
        return self._priority.currentData() or ""

    def apply_to(self, config) -> None:
        """Write the entered credentials and preference back onto `config`.

        Providers left blank are dropped rather than stored empty, so
        `providers` stays a record of what the user actually configured.
        """
        providers: dict[str, dict[str, str]] = {}
        for provider_id, row in self._rows.items():
            values = {k: v for k, v in row.values.items() if v}
            # A keyless provider is configured by existing, not by having values
            # filled in. Dropping it for being empty is what made MyMemory — the
            # one provider that needs no account — impossible to end up with.
            if values or row.spec.keyless:
                providers[provider_id] = values
        config.providers = providers
        config.translator_priority = self._priority.currentData() or ""
