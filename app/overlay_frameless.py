"""Dragging and resizing a window that has no frame to drag or resize by.

The overlay is frameless so it can sit over a fullscreen game without a
title bar stealing the look, which means the window manager does none of
this for us: the edges, the cursor shapes and the drag arithmetic are all
ours. It is a hundred lines of geometry with no product decisions in it,
and it reads better away from the code that decides what to show.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QCursor

from app.overlay_chrome import _EDGE_MARGIN

#: Smallest the window may be dragged to.
_MIN_WIDTH = 350
_MIN_HEIGHT = 200


class FramelessDragResizeMixin:
    """Mouse handling for a frameless window.

    Expects the host widget to carry `_drag_pos` and `_resize_edge`, and to
    provide `_save_overlay_state()`.
    """

    def _hit_edge(self, pos: QPoint) -> str | None:
        """Return resize edge name if mouse is near a border, else None."""
        r = self.rect()
        m = _EDGE_MARGIN
        on_left = pos.x() < m
        on_right = pos.x() > r.width() - m
        on_top = pos.y() < m
        on_bottom = pos.y() > r.height() - m
        if on_bottom and on_right:
            return "br"
        if on_bottom and on_left:
            return "bl"
        if on_top and on_right:
            return "tr"
        if on_top and on_left:
            return "tl"
        if on_bottom:
            return "b"
        if on_right:
            return "r"
        if on_left:
            return "l"
        if on_top:
            return "t"
        return None

    def mousePressEvent(self, event: object) -> None:
        if (
            hasattr(event, "button") and event.button() == Qt.MouseButton.LeftButton  # type: ignore[union-attr]
        ):
            # Don't intercept clicks on interactive child widgets
            # (reply input, buttons, combos) — let Qt route them normally
            pos = event.position().toPoint()  # type: ignore[union-attr]
            child = self.childAt(pos)
            if child is not None:
                from PyQt6.QtWidgets import QAbstractScrollArea, QComboBox, QLineEdit, QPushButton

                if isinstance(child, (QLineEdit, QPushButton, QComboBox, QAbstractScrollArea)):
                    child.setFocus()
                    self.activateWindow()
                    return
                # Also check parent chain — click may land on child of QLineEdit etc.
                parent = child.parent()
                while parent is not None and parent is not self:
                    if isinstance(parent, (QLineEdit, QPushButton, QComboBox, QAbstractScrollArea)):
                        parent.setFocus()
                        self.activateWindow()
                        return
                    parent = parent.parent()

            edge = self._hit_edge(pos)
            if edge:
                self._resize_edge = edge
                self._drag_pos = event.globalPosition().toPoint()  # type: ignore[union-attr]
            else:
                self._resize_edge = None
                self._drag_pos = event.globalPosition().toPoint() - self.pos()  # type: ignore[union-attr]

    def mouseMoveEvent(self, event: object) -> None:
        pos = event.position().toPoint()  # type: ignore[union-attr]

        # Update cursor when hovering (no button pressed)
        if not (
            hasattr(event, "buttons") and event.buttons() & Qt.MouseButton.LeftButton  # type: ignore[union-attr]
        ):
            edge = self._hit_edge(pos)
            if edge:
                self.setCursor(QCursor(self._EDGE_CURSORS[edge]))
            else:
                self.unsetCursor()
            return
        if self._drag_pos is None:
            return
        gpos = event.globalPosition().toPoint()  # type: ignore[union-attr]
        if self._resize_edge:
            self._do_resize(gpos)
        else:
            self.move(gpos - self._drag_pos)  # type: ignore[union-attr]

    def _do_resize(self, gpos: QPoint) -> None:
        """Resize the overlay based on which edge is being dragged."""
        dx = gpos.x() - self._drag_pos.x()  # type: ignore[union-attr]
        dy = gpos.y() - self._drag_pos.y()  # type: ignore[union-attr]
        geo = self.geometry()
        e = self._resize_edge
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        if "r" in e:  # type: ignore[operator]
            geo.setWidth(max(min_w, geo.width() + dx))
        if "b" in e:  # type: ignore[operator]
            geo.setHeight(max(min_h, geo.height() + dy))
        if "l" in e:  # type: ignore[operator]
            new_w = max(min_w, geo.width() - dx)
            geo.setLeft(geo.right() - new_w)
        if "t" in e:  # type: ignore[operator]
            new_h = max(min_h, geo.height() - dy)
            geo.setTop(geo.bottom() - new_h)
        self.setGeometry(geo)
        self._drag_pos = gpos  # type: ignore[assignment]

    def mouseReleaseEvent(self, event: object) -> None:
        self._drag_pos = None
        self._resize_edge = None
        self._save_overlay_state()
