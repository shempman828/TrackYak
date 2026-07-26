from typing import Optional

from PySide6.QtCore import Property, QRect, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QRegion,
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

    # Album art within this much of a perfect 1:1 ratio is shown at its native
    # aspect ratio instead of being cropped to a square (many covers are
    # scanned/exported slightly off-square).
    _SQUARE_TOLERANCE = 0.08

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._is_artist = False
        self._label: Optional[str] = None
        self._prev_pixmap: Optional[QPixmap] = None
        self._prev_is_artist = False
        self._prev_label: Optional[str] = None
        self._transition = 1.0  # 0 = showing prev, 1 = showing current
        self._prev_content_rect = QRect()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_art(
        self,
        pixmap: Optional[QPixmap],
        is_artist: bool = False,
        label: Optional[str] = None,
    ):
        # Remember the outgoing image so paintEvent can crossfade into the
        # new one instead of popping straight to it.
        self._prev_pixmap = self._pixmap
        self._prev_is_artist = self._is_artist
        self._prev_label = self._label

        self._pixmap = pixmap
        self._is_artist = is_artist
        self._label = label
        self._transition = 0.0
        self.update()

    def _get_transition(self) -> float:
        return self._transition

    def _set_transition(self, v: float):
        self._transition = v
        self.update()

    # Animated 0→1 crossfade progress between the previous and current image.
    transitionProgress = Property(float, _get_transition, _set_transition)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        t = max(0.0, min(1.0, self._transition))
        drawn_rect = QRect()

        if t < 1.0:
            painter.setOpacity(1.0 - t)
            prev_rect = self._paint_content(painter, self._prev_pixmap, self._prev_is_artist, w, h)
            painter.setOpacity(1.0)
            self._draw_label(
                painter, prev_rect, self._prev_label if self._prev_is_artist else None, 1.0 - t
            )
            drawn_rect = drawn_rect.united(prev_rect)

        if t > 0.0:
            painter.setOpacity(t)
            cur_rect = self._paint_content(painter, self._pixmap, self._is_artist, w, h)
            painter.setOpacity(1.0)
            self._draw_label(
                painter, cur_rect, self._label if self._is_artist else None, t
            )
            drawn_rect = drawn_rect.united(cur_rect)

        content_rect = drawn_rect

        # Clear only pixels left over from a previous, larger/differently
        # shaped paint (e.g. an artist photo's wide crop shrinking down to a
        # square cover) — never the permanent letterbox margin outside the
        # art itself, since that margin must keep showing the blurred
        # backdrop drawn underneath by the parent view.
        stale = QRegion(self._prev_content_rect).subtracted(QRegion(content_rect))
        if not stale.isEmpty():
            painter.setClipRegion(stale)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setClipping(False)

        self._prev_content_rect = content_rect
        painter.end()

    def _paint_content(
        self, painter: QPainter, pixmap: Optional[QPixmap], is_artist: bool, w: int, h: int
    ) -> QRect:
        if pixmap and not pixmap.isNull() and is_artist:
            return self._paint_artist_photo(painter, pixmap, w, h)
        elif pixmap and not pixmap.isNull():
            return self._paint_square_art(painter, pixmap, w, h)
        return self._paint_placeholder(painter, w, h)

    def _paint_placeholder(self, painter: QPainter, w: int, h: int) -> QRect:
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
        return QRect(x, y, side, side)

    def _draw_label(
        self, painter: QPainter, rect: QRect, text: Optional[str], opacity: float
    ):
        """Caption an artist photo with the artist's name, faded in/out in
        step with the photo's own crossfade opacity."""
        if not text or opacity <= 0.0 or not rect.isValid():
            return

        painter.save()
        painter.setOpacity(opacity)

        clip = QPainterPath()
        clip.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), self._RADIUS, self._RADIUS)
        painter.setClipPath(clip)

        bar_h = max(44, int(rect.height() * 0.18))
        grad = QLinearGradient(rect.x(), rect.bottom() - bar_h, rect.x(), rect.bottom())
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 170))
        painter.fillRect(rect.x(), rect.bottom() - bar_h, rect.width(), bar_h, grad)

        painter.setClipping(False)
        font = QFont("Cambria", max(13, min(20, rect.width() // 20)), QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 235))
        text_rect = QRect(rect.x() + 18, rect.bottom() - bar_h, rect.width() - 36, bar_h - 8)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignBottom, text)

        painter.restore()

    def _paint_square_art(self, painter: QPainter, pixmap: QPixmap, w: int, h: int):
        """Album art: fill the available space. Nearly-square art is shown at
        its native aspect ratio; anything further off-square is cropped to a
        perfect square, as before."""
        pw, ph = pixmap.width(), pixmap.height()

        if pw > 0 and ph > 0 and abs((pw / ph) - 1.0) <= self._SQUARE_TOLERANCE:
            fit_scale = min(w / pw, h / ph)
            scale = min(fit_scale, self._MAX_UPSCALE)
            art_w = max(1, round(pw * scale))
            art_h = max(1, round(ph * scale))
            x, y = (w - art_w) // 2, (h - art_h) // 2

            path = QPainterPath()
            path.addRoundedRect(x, y, art_w, art_h, self._RADIUS, self._RADIUS)
            painter.setClipPath(path)

            scaled = pixmap.scaled(
                art_w, art_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(x, y, scaled)
            return QRect(x, y, art_w, art_h)

        side = min(w, h)
        x, y = (w - side) // 2, (h - side) // 2

        path = QPainterPath()
        path.addRoundedRect(x, y, side, side, self._RADIUS, self._RADIUS)
        painter.setClipPath(path)

        scaled = pixmap.scaled(
            side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        ox = x + (side - scaled.width()) // 2
        oy = y + (side - scaled.height()) // 2
        painter.drawPixmap(ox, oy, scaled)
        return QRect(x, y, side, side)

    def _paint_artist_photo(self, painter: QPainter, pixmap: QPixmap, w: int, h: int):
        """Artist photo: keep its native aspect ratio and avoid over-enlarging
        small images, instead of cropping it into a forced square."""
        pw, ph = pixmap.width(), pixmap.height()
        if pw <= 0 or ph <= 0:
            return QRect()

        fit_scale = min(w / pw, h / ph)
        scale = min(fit_scale, self._MAX_UPSCALE)
        new_w = max(1, round(pw * scale))
        new_h = max(1, round(ph * scale))

        x, y = (w - new_w) // 2, (h - new_h) // 2

        path = QPainterPath()
        path.addRoundedRect(x, y, new_w, new_h, self._RADIUS, self._RADIUS)
        painter.setClipPath(path)

        scaled = pixmap.scaled(
            new_w, new_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        painter.drawPixmap(x, y, scaled)
        return QRect(x, y, new_w, new_h)
