"""The "where does chat come from" control for the Qt front end.

The counterpart of `capture_source_gtk`, shared by the setup wizard and the
settings dialog for the same reason: choosing the screen source means picking
the chat area, testing that it reads, and knowing which capture method this
machine will use, and two places must not disagree about that.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.ocr_capture import OcrRegion, describe_backend
from app.ocr_engine import SCRIPTS
from app.ocr_engine import is_available as ocr_engine_available
from app.ocr_reader import OcrSettings, read_region_report
from app.ocr_report import format_report


def _note(text: str, indent: int = 0, error: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    colour = "#FF6B6B" if error else "#999"
    label.setStyleSheet(f"color: {colour}; font-size: 11px; margin-left: {indent}px;")
    return label


class CaptureSourceWidget(QWidget):
    source_changed = pyqtSignal(str)
    region_changed = pyqtSignal(object)
    _test_finished = pyqtSignal(str)  # from the worker thread; queued onto the GUI thread

    def __init__(
        self,
        source: str,
        region: OcrRegion | None,
        portal_token: str,
        script_for_test,
        interval: float | None = None,
        script: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """`interval` / `script` of None hide those rows (the wizard keeps it simple)."""
        super().__init__(parent)
        self._region = region
        self._portal_token = portal_token
        self._script_for_test = script_for_test
        self._picker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._ocr_radio = QRadioButton(tr("wizard.source.ocr"))
        self._memory_radio = QRadioButton(tr("wizard.source.memory"))
        self._ocr_radio.setChecked(source != "memory")
        self._memory_radio.setChecked(source == "memory")

        layout.addWidget(self._ocr_radio)
        layout.addWidget(_note(tr("wizard.source.ocr.desc"), indent=22))

        self._panel = QWidget()
        panel = QVBoxLayout(self._panel)
        panel.setContentsMargins(22, 0, 0, 0)

        buttons = QHBoxLayout()
        self._pick_btn = QPushButton(tr("wizard.source.repick" if region else "wizard.source.pick"))
        self._test_btn = QPushButton(tr("wizard.source.test"))
        self._test_btn.setEnabled(region is not None)
        buttons.addWidget(self._pick_btn)
        buttons.addWidget(self._test_btn)
        buttons.addStretch()
        panel.addLayout(buttons)

        self._area_label = QLabel(tr("wizard.source.area_set" if region else "wizard.source.area_none"))
        self._area_label.setWordWrap(True)
        self._result_label = QLabel("")
        self._result_label.setWordWrap(True)
        panel.addWidget(self._area_label)
        panel.addWidget(self._result_label)
        panel.addWidget(_note(tr("wizard.source.method", method=describe_backend())))
        panel.addWidget(_note(tr("wizard.source.tips")))

        self._script_combo: QComboBox | None = None
        if script is not None:
            row = QHBoxLayout()
            row.addWidget(QLabel(tr("settings.capture.script")))
            self._script_combo = QComboBox()
            for code in ("auto", *SCRIPTS):
                self._script_combo.addItem(tr(f"ocr.script.{code}"), code)
            self._script_combo.setCurrentIndex(max(0, self._script_combo.findData(script)))
            row.addWidget(self._script_combo)
            row.addStretch()
            panel.addLayout(row)

        self._interval: QDoubleSpinBox | None = None
        if interval is not None:
            row = QHBoxLayout()
            row.addWidget(QLabel(tr("settings.capture.interval")))
            self._interval = QDoubleSpinBox()
            self._interval.setRange(0.25, 5.0)
            self._interval.setSingleStep(0.25)
            self._interval.setDecimals(2)
            self._interval.setValue(interval)
            row.addWidget(self._interval)
            row.addStretch()
            panel.addLayout(row)

        if not ocr_engine_available():
            panel.addWidget(_note(tr("wizard.source.no_engine"), error=True))

        layout.addWidget(self._panel)
        layout.addWidget(self._memory_radio)
        layout.addWidget(_note(tr("wizard.source.memory.desc"), indent=22))

        self._panel.setVisible(source != "memory")
        self._ocr_radio.toggled.connect(self._on_toggled)
        self._pick_btn.clicked.connect(self._on_pick)
        self._test_btn.clicked.connect(self._on_test)
        self._test_finished.connect(self._show_test_result)

    # -- values the owner reads ---------------------------------------------------

    @property
    def source(self) -> str:
        return "ocr" if self._ocr_radio.isChecked() else "memory"

    @property
    def region(self) -> OcrRegion | None:
        return self._region

    @property
    def portal_token(self) -> str:
        return self._portal_token

    @property
    def interval(self) -> float:
        return self._interval.value() if self._interval is not None else 1.0

    @property
    def script(self) -> str:
        return self._script_combo.currentData() if self._script_combo is not None else "auto"

    # -- behaviour -----------------------------------------------------------------

    def _on_toggled(self, _checked: bool) -> None:
        self._panel.setVisible(self.source == "ocr")
        self.source_changed.emit(self.source)

    def _on_pick(self) -> None:
        from app.region_picker_qt import RegionPicker

        self._picker = RegionPicker(self._picked, parent=self.window())
        self._picker.show()

    def _picked(self, region: OcrRegion | None) -> None:
        window = self.window()
        window.raise_()
        window.activateWindow()
        if region is None:
            return
        self._region = region
        self._pick_btn.setText(tr("wizard.source.repick"))
        self._test_btn.setEnabled(True)
        self._area_label.setText(tr("wizard.source.area_set"))
        self._result_label.setText("")
        self.region_changed.emit(region)

    def _on_test(self) -> None:
        if self._region is None:
            return
        settings = OcrSettings(
            region=self._region, script=self._script_for_test(), restore_token=self._portal_token,
        )
        self._test_btn.setEnabled(False)
        self._result_label.setText(tr("wizard.source.testing"))

        def worker() -> None:
            try:
                text = format_report(read_region_report(settings))
            except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
                text = tr("wizard.source.test_fail", e=exc)
            self._test_finished.emit(text)

        threading.Thread(target=worker, daemon=True).start()

    def _show_test_result(self, text: str) -> None:
        self._test_btn.setEnabled(True)
        self._result_label.setText(text)
