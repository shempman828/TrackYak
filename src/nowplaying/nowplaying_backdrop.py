# ──────────────────────────────────────────────────────────────────────────────
#  Blurred backdrop
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Property, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget

# The art is shrunk to this longest side before the blur: a large radius on a
# small image is cheap, and the smooth upscale in paintEvent adds more softness.
_BLUR_SOURCE_PX = 160
_BLUR_RADIUS = 16

# Opacity of the blurred art over the deep base colour, and the dark scrim on
# top of it that keeps light lyric text readable over a bright cover.
_ART_OPACITY = 0.55
_SCRIM = QColor(8, 10, 18, 95)

# Used for the tint when the cover has no clear hue (greyscale art).
_FALLBACK_TINT = QColor(133, 153, 234)


def _blur_pixmap(pixmap: QPixmap) -> QPixmap:
    """A small, heavily blurred copy of ``pixmap``.

    The blur fades the image edges to transparent, so a margin of one blur
    radius is cropped from each side of the result.
    """
    small = pixmap.scaled(
        _BLUR_SOURCE_PX, _BLUR_SOURCE_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(_BLUR_RADIUS)
    effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    m = _BLUR_RADIUS
    src = QRectF(m, m, max(1, small.width() - 2 * m), max(1, small.height() - 2 * m))
    out = QImage(src.size().toSize(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    scene.render(painter, QRectF(out.rect()), src)
    painter.end()
    return QPixmap.fromImage(out)


def _tint_from(pixmap: QPixmap) -> QColor:
    """Average colour of ``pixmap``, pushed to a saturation and value that
    reads as a colour wash on the dark theme."""
    avg = pixmap.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).toImage()
    c = avg.pixelColor(0, 0)
    h, s, v, _ = c.getHsv()
    if h < 0 or s < 25:
        return QColor(_FALLBACK_TINT)
    return QColor.fromHsv(h, max(90, min(200, s)), max(110, min(180, v)))


class _BlurredBackdrop(QWidget):
    """Full-widget blurred album-art background with a colour wash taken from
    the art, a scrim, and a vignette."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._blurred: QPixmap | None = None
        self._tint = QColor(_FALLBACK_TINT)
        self._scaled_cache: tuple[QSize, QPixmap] | None = None
        self._opacity: float = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pixmap = pixmap
        self._scaled_cache = None
        if pixmap and not pixmap.isNull():
            self._blurred = _blur_pixmap(pixmap)
            self._tint = _tint_from(pixmap)
        else:
            self._blurred = None
            self._tint = QColor(_FALLBACK_TINT)
        self.update()

    def tint(self) -> QColor:
        return QColor(self._tint)

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float):
        self._opacity = v
        self.update()

    backdropOpacity = Property(float, _get_opacity, _set_opacity)

    def _scaled_blur(self, w: int, h: int) -> QPixmap:
        size = QSize(w, h)
        if self._scaled_cache is None or self._scaled_cache[0] != size:
            scaled = self._blurred.scaled(
                w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            self._scaled_cache = (size, scaled)
        return self._scaled_cache[1]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        # Deep background
        painter.fillRect(0, 0, w, h, QColor(12, 14, 22))

        if self._blurred and not self._blurred.isNull() and self._opacity > 0 and w and h:
            scaled = self._scaled_blur(w, h)
            painter.setOpacity(self._opacity * _ART_OPACITY)
            painter.drawPixmap((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)

            # Colour wash centred behind the art column.
            painter.setOpacity(self._opacity)
            wash = QRadialGradient(w * 0.28, h * 0.45, max(w, h) * 0.75)
            inner = QColor(self._tint)
            inner.setAlpha(70)
            outer = QColor(self._tint)
            outer.setAlpha(0)
            wash.setColorAt(0.0, inner)
            wash.setColorAt(1.0, outer)
            painter.fillRect(0, 0, w, h, wash)
            painter.setOpacity(1.0)

        painter.fillRect(0, 0, w, h, _SCRIM)

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
