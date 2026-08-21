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


def content_width(container: QWidget) -> int:
    """How wide the parts that cannot wrap need this container to be.

    A word-wrapped QLabel reports the width it would take on ONE line, so a
    paragraph of explanation claims twelve hundred pixels and would drag the
    window past the edge of the screen. Wrapping is exactly what those labels
    are for, so they are not what the width should be decided by. Rows of
    inputs and buttons are.
    """
    from PyQt6.QtWidgets import QLabel

    widest = 0
    for child in container.findChildren(QWidget):
        # Containers inherit the inflated hint from the wrapped labels inside
        # them, so asking a group box is asking the paragraph again.
        if child.findChildren(QWidget):
            continue
        if isinstance(child, QLabel) and child.wordWrap():
            continue
        widest = max(widest, child.sizeHint().width())
    return widest


def size_to_content(window: QWidget, *, margin: int = 96) -> None:
    """Open the window wide enough for what is inside it.

    A scroll area reports a small size hint whatever it holds, and horizontal
    scrolling is off on purpose — so a window sized from its own hint opened
    narrower than its rows and clipped their right-hand side, with nothing to
    scroll.

    Never larger than the screen it will appear on: a window that opens past
    the edge is the same problem with the other sign.
    """
    from PyQt6.QtWidgets import QApplication, QScrollArea

    widest = window.sizeHint().width()
    for area in window.findChildren(QScrollArea):
        content = area.widget()
        if content is not None:
            widest = max(widest, content_width(content) + margin)

    screen = QApplication.primaryScreen()
    available = screen.availableGeometry() if screen is not None else None
    if available is not None:
        widest = min(widest, int(available.width() * 0.9))
        height = min(max(window.height(), window.sizeHint().height()), int(available.height() * 0.9))
    else:
        height = max(window.height(), window.sizeHint().height())

    window.resize(max(widest, window.width()), height)
