"""Full-screen picker for the chat area (PyQt6, the Windows front end).

Same contract as `region_picker_gtk`: one translucent surface per monitor, the
user drags a box around their WoW chat window on whichever screen shows the
game, and `on_done` receives the region (or None on Esc). The region is stored
as fractions of the monitor so it survives a change of resolution or scaling.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from app.i18n import tr
from app.ocr_capture import OcrRegion

_MIN_PX = 30


def _box(a: QPoint, b: QPoint) -> QRect:
    """The box between two corners, the same whichever way it was dragged.

    QRect(a, b) counts the second corner as inside the box and normalized() moves
    a backwards drag by a pixel, so the same drag would give two different boxes.
    """
    return QRect(min(a.x(), b.x()), min(a.y(), b.y()), abs(b.x() - a.x()), abs(b.y() - a.y()))


class _PickerSurface(QWidget):
    def __init__(
        self,
        screen,
        pixel_size: tuple[int, int],
        ordinal: int,
        finish: Callable[[OcrRegion | None], None],
        parent: QWidget | None = None,
    ) -> None:
        # The parent is not decoration. The wizard and the settings dialog are
        # application-modal, and Qt blocks all input to any window outside a modal
        # dialog's own hierarchy: an unparented picker gets no mouse events at all
        # and Windows answers every click with its "blocked" chime. A window whose
        # parent IS the dialog is part of that hierarchy and is not blocked.
        super().__init__(parent)
        self._screen = screen
        self._pixel_size = pixel_size
        self._ordinal = ordinal
        self._finish = finish
        self._start: QPoint | None = None
        self._rect = QRect()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setGeometry(screen.geometry())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 82))
        if not self._rect.isNull():
            painter.fillRect(self._rect, QColor(153, 204, 255, 52))
            painter.setPen(QPen(QColor("#99ccff"), 2))
            painter.drawRect(self._rect)
        painter.setPen(QColor("#ffffff"))
        font = QFont(self.font())
        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(
            QRect(0, 40, self.width(), 40), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, tr("region.hint")
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._rect = QRect()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._start is not None:
            self._rect = _box(self._start, event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        rect = _box(self._start, event.position().toPoint())
        self._start = None
        if rect.width() < _MIN_PX or rect.height() < _MIN_PX:
            self._rect = QRect()  # a click, or too small to be a chat window
            self.update()
            return
        geo = self._screen.geometry()
        width, height = max(1, self.width()), max(1, self.height())
        self._finish(
            OcrRegion(
                x=rect.x() / width, y=rect.y() / height, w=rect.width() / width, h=rect.height() / height,
                monitor=self._ordinal,
                abs=(geo.x() + rect.x(), geo.y() + rect.y(), rect.width(), rect.height()),
                px=self._pixel_size,
            )
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)
        else:
            super().keyPressEvent(event)


class RegionPicker:
    """Open on every monitor; call `on_done(region)` once, or `on_done(None)` on Esc."""

    def __init__(self, on_done: Callable[[OcrRegion | None], None], parent: QWidget | None = None) -> None:
        self._on_done = on_done
        self._parent = parent
        self._surfaces: list[_PickerSurface] = []
        self._finished = False

    def show(self) -> None:
        screens = QGuiApplication.screens()
        sizes = [
            (round(s.geometry().width() * s.devicePixelRatio()), round(s.geometry().height() * s.devicePixelRatio()))
            for s in screens
        ]
        seen: dict[tuple[int, int], int] = {}
        for screen, size in zip(screens, sizes, strict=True):
            ordinal = seen.get(size, 0)
            seen[size] = ordinal + 1
            surface = _PickerSurface(screen, size, ordinal, self._finish, self._parent)
            self._surfaces.append(surface)
            surface.show()

    def _finish(self, region: OcrRegion | None) -> None:
        if self._finished:
            return
        self._finished = True
        for surface in self._surfaces:
            surface.close()
        self._surfaces.clear()
        self._on_done(region)
