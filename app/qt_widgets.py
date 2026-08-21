"""Small Qt helpers shared by the settings dialog and the setup wizard.

Both windows hit the same problem: their content is taller than a laptop
screen, and a Qt layout short of vertical space does not clip — it squeezes,
taking the difference out of whatever will give. That produced an 11px caption
in a 9px box in one window and 32px credential fields rendered at 6px in the
other, neither of which raises anything.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QScrollArea, QWidget


def scrollable(content: QWidget) -> QScrollArea:
    """Put a page or a tab behind a vertical scroll bar.

    A window whose minimum size is its content's full height cannot be made to
    fit a smaller screen: the settings dialog could not go below 1020px and the
    wizard demanded 767px against a declared minimum of 480, so on a 1366x768
    laptop the buttons sat past the bottom edge with nothing to scroll.

    Horizontal scrolling stays off deliberately — a settings window that scrolls
    sideways is a layout bug, not a feature — so anything wide enough to need it
    has to wrap instead.
    """
    area = QScrollArea()
    area.setWidget(content)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area
