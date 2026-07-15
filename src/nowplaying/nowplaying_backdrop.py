# ──────────────────────────────────────────────────────────────────────────────
#  Blurred backdrop
# ──────────────────────────────────────────────────────────────────────────────
from typing import Optional

from PySide6.QtCore import Property, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget


class _BlurredBackdrop(QWidget):
    """Full-widget blurred album-art background with vignette overlay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._opacity: float = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_pixmap(self, pixmap: Optional[QPixmap]):
        self._pixmap = pixmap
        self.update()

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float):
        self._opacity = v
        self.update()

    backdropOpacity = Property(float, _get_opacity, _set_opacity)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        # Deep background
        painter.fillRect(0, 0, w, h, QColor(12, 14, 22))

        if self._pixmap and not self._pixmap.isNull() and self._opacity > 0:
            painter.setOpacity(self._opacity * 0.28)
            scaled = self._pixmap.scaled(
                w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.setOpacity(1.0)

        # Vignette
        grad = QRadialGradient(w / 2, h / 2, max(w, h) * 0.72)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 190))
        painter.fillRect(0, 0, w, h, grad)

        # Bottom fade
        bot = QLinearGradient(0, h * 0.65, 0, h)
        bot.setColorAt(0.0, QColor(0, 0, 0, 0))
        bot.setColorAt(1.0, QColor(8, 10, 18, 210))
        painter.fillRect(0, int(h * 0.65), w, h, bot)

        painter.end()
