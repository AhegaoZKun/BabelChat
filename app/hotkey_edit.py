"""A one-line widget that captures a key combination.

Lives on its own because it is a reusable input control, not part of the
settings dialog's structure — and because the dialog was well past the
project's file-size limit with it inside.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.i18n import tr


class HotkeyEdit(QWidget):
    """Widget for capturing keyboard shortcuts: shows current combo + Change button."""

    hotkey_changed = pyqtSignal(str)

    _MOD_NAMES = {
        Qt.Key.Key_Control: "Ctrl",
        Qt.Key.Key_Shift: "Shift",
        Qt.Key.Key_Alt: "Alt",
        Qt.Key.Key_Meta: "Win",
    }

    def __init__(self, current: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hotkey = current
        self._recording = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._label = QLabel(current or tr("settings.hk.none"))
        self._label.setStyleSheet(
            "color: #FFD200; font-weight: bold; font-size: 12px; padding: 4px 8px;"
            "background: #111; border: 1px solid #555; border-radius: 3px;"
        )
        self._label.setMinimumWidth(140)
        layout.addWidget(self._label)

        self._btn = QPushButton(tr("settings.hk.change"))
        self._btn.setFixedWidth(90)
        self._btn.clicked.connect(self._start_recording)
        layout.addWidget(self._btn)

        self._clear_btn = QPushButton(tr("settings.hk.clear"))
        self._clear_btn.setFixedWidth(70)
        self._clear_btn.clicked.connect(self._clear)
        layout.addWidget(self._clear_btn)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def text(self) -> str:
        return self._hotkey

    def _start_recording(self) -> None:
        self._recording = True
        self._label.setText(tr("settings.hk.press_keys"))
        self._label.setStyleSheet(
            "color: #40FF40; font-weight: bold; font-size: 12px; padding: 4px 8px;"
            "background: #111; border: 1px solid #40FF40; border-radius: 3px;"
        )
        self._btn.setText(tr("settings.hk.cancel"))
        self._btn.clicked.disconnect()
        self._btn.clicked.connect(self._cancel_recording)
        self.setFocus()

    def _cancel_recording(self) -> None:
        self._recording = False
        self._label.setText(self._hotkey or tr("settings.hk.none"))
        self._label.setStyleSheet(
            "color: #FFD200; font-weight: bold; font-size: 12px; padding: 4px 8px;"
            "background: #111; border: 1px solid #555; border-radius: 3px;"
        )
        self._btn.setText(tr("settings.hk.change"))
        self._btn.clicked.disconnect()
        self._btn.clicked.connect(self._start_recording)

    def _clear(self) -> None:
        self._hotkey = ""
        self._label.setText(tr("settings.hk.none"))
        self._cancel_recording()
        self.hotkey_changed.emit("")

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # type: ignore[override]
        if not self._recording or event is None:
            super().keyPressEvent(event)  # type: ignore[arg-type]
            return

        key = event.key()
        # Ignore bare modifier presses
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        parts: list[str] = []
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")

        # Map key to name
        key_name = ""
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            key_name = chr(key)
        elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            key_name = f"F{key - Qt.Key.Key_F1 + 1}"
        elif key == Qt.Key.Key_Escape:
            self._cancel_recording()
            return
        else:
            # Try Qt enum name
            try:
                key_name = Qt.Key(key).name.replace("Key_", "")
            except (ValueError, AttributeError):
                key_name = f"0x{key:X}"

        if not parts:
            # Require at least one modifier
            return

        parts.append(key_name)
        combo = "+".join(parts)
        self._hotkey = combo
        self._label.setText(combo)
        self._recording = False
        self._label.setStyleSheet(
            "color: #FFD200; font-weight: bold; font-size: 12px; padding: 4px 8px;"
            "background: #111; border: 1px solid #555; border-radius: 3px;"
        )
        self._btn.setText(tr("settings.hk.change"))
        self._btn.clicked.disconnect()
        self._btn.clicked.connect(self._start_recording)
        self.hotkey_changed.emit(combo)
