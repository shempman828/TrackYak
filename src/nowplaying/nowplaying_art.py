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


# ──────────────────────────────────────────────────────────────────────────────
#  Art card
# ──────────────────────────────────────────────────────────────────────────────


class _ArtCard(QWidget):
    """Rounded album-art display with subtle glow."""

    _RADIUS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_art(self, pixmap: Optional[QPixmap]):
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        side = min(w, h)
        x, y = (w - side) // 2, (h - side) // 2

        path = QPainterPath()
        path.addRoundedRect(x, y, side, side, self._RADIUS, self._RADIUS)
        painter.setClipPath(path)

        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            ox = x + (side - scaled.width()) // 2
            oy = y + (side - scaled.height()) // 2
            painter.drawPixmap(ox, oy, scaled)
        else:
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
