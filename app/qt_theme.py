"""The Qt stylesheet the settings dialog and wizard share.

Kept apart from the dialog because it is 150 lines of CSS with no logic in
it, and its presence made the dialog look twice the size it is.
"""

WOW_THEME_STYLESHEET = """
QDialog {
    background-color: #1a1a1a;
    color: #e0e0e0;
}

QTabWidget::pane {
    border: 1px solid #333;
    background: #1a1a1a;
    border-radius: 4px;
}
QTabBar::tab {
    background: #2a2a2a;
    color: #999;
    border: 1px solid #333;
    padding: 8px 16px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #333;
    color: #FFD200;
    border-bottom-color: #333;
}
QTabBar::tab:hover:!selected {
    color: #CCC;
    background: #2e2e2e;
}

/* Every tab and every wizard page sits inside a QScrollArea, and a scroll
   area paints its viewport from the palette, not from this stylesheet. On a
   machine whose Qt palette is light that viewport came out #efefef — a white
   page behind the dark group boxes, showing through wherever they did not
   cover it, which is a bar across each group title. Transparent lets the
   window's own colour through, whatever the palette happens to be. */
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollArea > QWidget#qt_scrollarea_viewport {
    background: transparent;
}

QGroupBox {
    border: 1px solid #444;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 16px;
    background: #222;
    font-weight: bold;
    color: #FFD200;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: #FFD200;
}

QLineEdit, QSpinBox {
    background: #111;
    color: #e0e0e0;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 6px;
    selection-background-color: #FFD200;
    selection-color: #000;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: #FFD200;
}

QComboBox {
    background: #111;
    color: #e0e0e0;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 6px;
}
QComboBox:focus { border-color: #FFD200; }
QComboBox::drop-down {
    border: none;
    background: #333;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #1a1a1a;
    color: #e0e0e0;
    selection-background-color: #FFD200;
    selection-color: #000;
    border: 1px solid #555;
}

QCheckBox {
    color: #e0e0e0;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #555;
    border-radius: 3px;
    background: #111;
}
QCheckBox::indicator:checked {
    background: #FFD200;
    border-color: #FFD200;
}
QCheckBox::indicator:hover {
    border-color: #FFD200;
}

QPushButton {
    background: #333;
    color: #e0e0e0;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #444;
    border-color: #FFD200;
    color: #FFD200;
}
QPushButton:pressed {
    background: #555;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #333;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #FFD200;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #997d00, stop:1 #FFD200);
    border-radius: 3px;
}

QProgressBar {
    border: 1px solid #555;
    border-radius: 3px;
    background: #111;
    text-align: center;
    color: #e0e0e0;
    height: 20px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #997d00, stop:1 #FFD200);
    border-radius: 3px;
}

QLabel {
    color: #ccc;
}

QDialogButtonBox QPushButton {
    min-width: 80px;
}
"""
