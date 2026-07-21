import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from src.core.logger_config import logger


class RatingStarsWidget(QWidget):
    """Interactive 1–10 rating stars widget with half-star precision."""

    rating_changed = Signal(float)  # Emits new rating

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setMinimumHeight(30)

        self.rating = 0.0
        self.hover_rating = 0.0
        self.is_hovering = False
        self.is_dragging = False

        self.current_file = None

    def set_current_file(self, file_path):
        """Set current file for rating updates."""
        self.current_file = file_path

    def set_rating(self, rating):
        """Set rating, clamped between 0 and 10."""
        self.rating = max(0.0, min(10.0, float(rating or 0.0)))
        self.update()

    def mousePressEvent(self, event):
        """Start rating on mouse press."""
        if event.button() == Qt.LeftButton:
            rating = self._calculate_rating_from_pos(event.pos())
            self._update_rating(rating)
            self.is_dragging = True

    def mouseMoveEvent(self, event):
        """Update rating dynamically while dragging."""
        if self.is_dragging and event.buttons() & Qt.LeftButton:
            rating = self._calculate_rating_from_pos(event.pos())
            if rating != self.rating:
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

    def _update_rating(self, rating: float):
        """Set rating and notify listeners immediately."""
        self.rating = rating
        self.rating_changed.emit(rating)
        self.update()

    def _star_geometry(self):
        """
        Return (star_size, spacing, start_x, start_y) computed from the
        current widget size.  Both paintEvent and _calculate_rating_from_pos
        must use this so click zones match the drawn stars.
        """
        available_height = self.height() - 4
        star_size = min(available_height, 12)
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
        try:
            cx, cy = x + size / 2, y + size / 2
            outer_r, inner_r = size / 2, size / 2 * 0.5

            points = []
            start_angle = -math.pi / 2
            for i in range(10):
                r = outer_r if i % 2 == 0 else inner_r
                angle = start_angle + i * (math.pi / 5)
                points.append(
                    QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))
                )

            path = QPainterPath()
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
            path.closeSubpath()

            painter.save()
            painter.setPen(QPen(QColor(120, 120, 120), max(1.0, size * 0.06)))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            painter.restore()

            if fill > 0.0:
                painter.save()
                clip_w = size * max(0.0, min(1.0, fill))
                painter.setClipRect(QRectF(x, y, clip_w, size))
                painter.setPen(QPen(QColor(133, 153, 234), max(1.0, size * 0.06)))
                painter.setBrush(QColor(133, 153, 234))
                painter.drawPath(path)
                painter.restore()

        except Exception as e:
            logger.error(f"Error drawing star: {e}")

    def _calculate_rating_from_pos(self, pos) -> float:
        """
        Return rating based on mouse position, snapped to 0.5 steps.

        The half/full boundary is based on the star's own drawn width
        (not the wider star+spacing cell), matching how _draw_star fills
        each star so the visual boundary lines up with the click zone.
        """
        try:
            star_size, spacing, start_x, _ = self._star_geometry()
            cell = star_size + spacing
            rel_x = pos.x() - start_x

            if rel_x < 0:
                return 0.0

            star_index = int(rel_x / cell)
            if star_index >= 10:
                return 10.0

            pos_in_star = (rel_x - star_index * cell) / star_size
            rating = star_index + (0.5 if pos_in_star < 0.5 else 1.0)
            return max(0.0, min(10.0, rating))
        except Exception as e:
            logger.error(f"Error calculating rating from position: {e}")
            return 0.0

    def sizeHint(self):
        """Provide sensible default size."""
        return self.minimumSize()
