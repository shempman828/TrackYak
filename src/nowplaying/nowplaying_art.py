from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QSizePolicy,
    QWidget,
)

from src.core.logger_config import logger


# ──────────────────────────────────────────────────────────────────────────────
#  Art card
# ──────────────────────────────────────────────────────────────────────────────


class _ArtCard(QWidget):
    """Rounded album-art display with subtle glow."""

    _RADIUS = 18

    # Cap how far a small/low-res image is blown up so it doesn't turn to mush.
    _MAX_UPSCALE = 1.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._is_artist = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_art(self, pixmap: Optional[QPixmap], is_artist: bool = False):
        self._pixmap = pixmap
        self._is_artist = is_artist
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        if self._pixmap and not self._pixmap.isNull() and self._is_artist:
            self._paint_artist_photo(painter, w, h)
        elif self._pixmap and not self._pixmap.isNull():
            self._paint_square_art(painter, w, h)
        else:
            side = min(w, h)
            x, y = (w - side) // 2, (h - side) // 2
            path = QPainterPath()
            path.addRoundedRect(x, y, side, side, self._RADIUS, self._RADIUS)
            painter.setClipPath(path)
            # Default art: dark gradient with a music note
            bg = QLinearGradient(x, y, x + side, y + side)
            bg.setColorAt(0.0, QColor(30, 35, 60))
            bg.setColorAt(1.0, QColor(15, 18, 35))
            painter.fillPath(path, bg)

            # Draw a simple music note using text
            painter.setClipping(False)
            note_font = QFont("Arial", max(24, side // 4), QFont.Bold)
            painter.setFont(note_font)
            painter.setPen(QColor(100, 120, 200, 80))
            painter.drawText(x, y, side, side, Qt.AlignCenter, "♪")

        painter.end()

    def _paint_square_art(self, painter: QPainter, w: int, h: int):
        """Album art: crop to a square that fills the available space."""
        side = min(w, h)
        x, y = (w - side) // 2, (h - side) // 2

        path = QPainterPath()
        path.addRoundedRect(x, y, side, side, self._RADIUS, self._RADIUS)
        painter.setClipPath(path)

        scaled = self._pixmap.scaled(
            side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        ox = x + (side - scaled.width()) // 2
        oy = y + (side - scaled.height()) // 2
        painter.drawPixmap(ox, oy, scaled)

    def _paint_artist_photo(self, painter: QPainter, w: int, h: int):
        """Artist photo: keep its native aspect ratio and avoid over-enlarging
        small images, instead of cropping it into a forced square."""
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return

        fit_scale = min(w / pw, h / ph)
        scale = min(fit_scale, self._MAX_UPSCALE)
        new_w = max(1, round(pw * scale))
        new_h = max(1, round(ph * scale))

        x, y = (w - new_w) // 2, (h - new_h) // 2

        path = QPainterPath()
        path.addRoundedRect(x, y, new_w, new_h, self._RADIUS, self._RADIUS)
        painter.setClipPath(path)

        scaled = self._pixmap.scaled(
            new_w, new_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        painter.drawPixmap(x, y, scaled)
