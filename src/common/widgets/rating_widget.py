"""Interactive 0-10 star rating widget with half-star precision."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

_MAX_RATING = 10.0
_STEP = 0.5
_OUTLINE_COLOR = QColor(120, 120, 120)
_FILL_COLOR = QColor(133, 153, 234)


class RatingStarsWidget(QWidget):
    """Interactive 0-10 rating stars widget with half-star precision (mouse or keyboard)."""

    rating_changed = Signal(float)  # Emits new rating

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setMinimumHeight(30)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)  # hover preview without a pressed button
        self.setAccessibleName("Rating")

        self.rating = 0.0
        self.hover_rating = 0.0
        self.is_hovering = False
        self.is_dragging = False
        self._refresh_accessible_text()

    def set_current_file(self, file_path):
        """No-op kept for the player dock; the dock tracks the playing file itself."""

    def set_rating(self, rating):
        """Set rating, clamped between 0 and 10, without emitting rating_changed."""
        self.rating = self._clamp(float(rating or 0.0))
        self._refresh_accessible_text()
        self.update()

    def mousePressEvent(self, event):
        """Start rating on mouse press."""
        if event.button() == Qt.LeftButton:
            self._update_rating(self._calculate_rating_from_pos(event.position()))
            self.is_dragging = True

    def mouseMoveEvent(self, event):
        """Preview the rating under the cursor; apply it while dragging."""
        rating = self._calculate_rating_from_pos(event.position())
        if self.is_dragging and event.buttons() & Qt.LeftButton and rating != self.rating:
            self._update_rating(rating)
        self.hover_rating = rating
        self.is_hovering = True
        self.update()

    def mouseReleaseEvent(self, event):
        """Finalize rating on mouse release."""
        if event.button() == Qt.LeftButton:
            self.is_dragging = False

    def leaveEvent(self, event):
        """Reset hover when cursor leaves widget."""
        self.is_hovering = False
        self.hover_rating = 0.0
        self.update()

    def keyPressEvent(self, event):
        """Left/Right (or Down/Up) step by half a star; Home/End go to 0/10."""
        key = event.key()
        if key in (Qt.Key_Right, Qt.Key_Up):
            new_rating = self.rating + _STEP
        elif key in (Qt.Key_Left, Qt.Key_Down):
            new_rating = self.rating - _STEP
        elif key == Qt.Key_Home:
            new_rating = 0.0
        elif key == Qt.Key_End:
            new_rating = _MAX_RATING
        else:
            super().keyPressEvent(event)
            return
        new_rating = self._clamp(new_rating)
        if new_rating != self.rating:
            self._update_rating(new_rating)
        event.accept()

    def _update_rating(self, rating: float):
        """Set rating and notify listeners immediately."""
        self.rating = rating
        self._refresh_accessible_text()
        self.rating_changed.emit(rating)
        self.update()

    def _refresh_accessible_text(self) -> None:
        text = f"{self.rating:g} of {_MAX_RATING:g}"
        self.setAccessibleDescription(text)
        self.setToolTip(f"Rating: {text}")

    @staticmethod
    def _clamp(rating: float) -> float:
        return max(0.0, min(_MAX_RATING, rating))

    def _star_geometry(self):
        """Return (star_size, spacing, start_x, start_y) shared by painting and hit-testing."""
        star_size = max(1, min(self.height() - 4, 12))
        spacing = star_size * 0.3
        total_width = 10 * star_size + 9 * spacing
        start_x = max(0, (self.width() - total_width) / 2)
        start_y = (self.height() - star_size) / 2
        return star_size, spacing, start_x, start_y

    def paintEvent(self, event):
        """Render the stars with hover and current rating."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        star_size, spacing, start_x, start_y = self._star_geometry()
        display_rating = self.hover_rating if self.is_hovering else self.rating

        for i in range(10):
            star_x = start_x + i * (star_size + spacing)
            star_fill = max(0.0, min(1.0, display_rating - i))
            self._draw_star(painter, star_x, start_y, star_size, star_fill)

        painter.end()

    def _draw_star(self, painter, x, y, size, fill):
        """Draw a single 5-pointed star with partial fill."""
        cx, cy = x + size / 2, y + size / 2
        outer_r, inner_r = size / 2, size / 2 * 0.5

        path = QPainterPath()
        for i in range(10):
            r = outer_r if i % 2 == 0 else inner_r
            angle = -math.pi / 2 + i * (math.pi / 5)
            point = QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        path.closeSubpath()

        pen_width = max(1.0, size * 0.06)
        painter.save()
        painter.setPen(QPen(_OUTLINE_COLOR, pen_width))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.restore()

        if fill > 0.0:
            painter.save()
            painter.setClipRect(QRectF(x, y, size * min(1.0, fill), size))
            painter.setPen(QPen(_FILL_COLOR, pen_width))
            painter.setBrush(_FILL_COLOR)
            painter.drawPath(path)
            painter.restore()

    def _calculate_rating_from_pos(self, pos) -> float:
        """Return rating based on mouse position, snapped to 0.5 steps."""
        star_size, spacing, start_x, _ = self._star_geometry()
        cell = star_size + spacing
        rel_x = pos.x() - start_x

        if rel_x < 0:
            return 0.0

        star_index = int(rel_x / cell)
        if star_index >= 10:
            return _MAX_RATING

        # Half/full boundary uses the star's drawn width, not the wider cell,
        # so the click zone matches how _draw_star fills it.
        pos_in_star = (rel_x - star_index * cell) / star_size
        return self._clamp(star_index + (0.5 if pos_in_star < 0.5 else 1.0))

    def sizeHint(self):
        """Provide sensible default size."""
        return self.minimumSize()
