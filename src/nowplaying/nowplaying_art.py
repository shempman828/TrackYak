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

    # Subtle Ken-Burns-style scale accompanying each crossfade: the outgoing
    # image eases out to 1+ZOOM, the incoming one eases in from 1-ZOOM, so
    # the transition reads as motion rather than a flat opacity dissolve.
    _ZOOM_AMOUNT = 0.035

    def __init__(self, parent=None, backdrop: Optional[QWidget] = None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._is_artist = False
        self._label: Optional[str] = None
        self._prev_pixmap: Optional[QPixmap] = None
        self._prev_is_artist = False
        self._prev_label: Optional[str] = None
        self._transition = 1.0  # 0 = showing prev, 1 = showing current
        self._prev_content_rect = QRect()
        # The widget the letterbox margin around the art should reveal — see
        # the stale-region handling in paintEvent().
        self._backdrop = backdrop
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

        prev_rect = self._layout_rect(self._prev_pixmap, self._prev_is_artist, w, h)
        cur_rect = self._layout_rect(self._pixmap, self._is_artist, w, h)
        # The visible frame morphs between the outgoing and incoming content's
        # own shape over the course of the crossfade, so e.g. a square cover
        # swapping for a portrait artist photo reads as one smooth resize
        # instead of two mismatched rectangles snapping in and out.
        frame = self._lerp_rect(prev_rect, cur_rect, t)

        content_rect = QRect()

        if t < 1.0:
            zoom = 1.0 + self._ZOOM_AMOUNT * t
            self._paint_layer(
                painter, self._prev_pixmap, self._prev_is_artist, self._prev_label,
                frame, 1.0 - t, zoom,
            )
            content_rect = content_rect.united(self._scale_rect_about_center(frame, zoom))

        if t > 0.0:
            zoom = 1.0 - self._ZOOM_AMOUNT * (1.0 - t)
            self._paint_layer(
                painter, self._pixmap, self._is_artist, self._label,
                frame, t, zoom,
            )
            content_rect = content_rect.united(self._scale_rect_about_center(frame, zoom))

        # Pixels left over from a previous, larger/differently shaped paint
        # (e.g. an artist photo's wide crop shrinking down to a square cover)
        # need to go back to showing the blurred backdrop drawn underneath by
        # the parent view. This widget has no alpha channel of its own (it's
        # an ordinary opaque child, not a translucent window), so painting
        # "transparent" here doesn't reveal anything — it just writes solid
        # black and permanently hides the backdrop under that patch. Instead,
        # leave those pixels untouched and ask the backdrop to repaint them;
        # it always redraws its full rect opaquely, so the patch is correct
        # again by the very next frame.
        stale = QRegion(self._prev_content_rect).subtracted(QRegion(content_rect))
        if not stale.isEmpty() and self._backdrop is not None:
            bounds = stale.boundingRect()
            # mapTo() requires an actual ancestor, and the backdrop is a
            # sibling (not a parent) of this widget, so go via global coords.
            top_left = self._backdrop.mapFromGlobal(self.mapToGlobal(bounds.topLeft()))
            self._backdrop.update(QRect(top_left, bounds.size()))

        self._prev_content_rect = content_rect
        painter.end()

    def _paint_layer(
        self,
        painter: QPainter,
        pixmap: Optional[QPixmap],
        is_artist: bool,
        label: Optional[str],
        rect: QRect,
        opacity: float,
        zoom: float,
    ):
        painter.save()
        painter.setOpacity(opacity)
        if zoom != 1.0:
            center = rect.center()
            painter.translate(center)
            painter.scale(zoom, zoom)
            painter.translate(-center)
        if pixmap and not pixmap.isNull():
            self._paint_pixmap_cover(painter, pixmap, rect)
        else:
            self._paint_placeholder_in_rect(painter, rect)
        painter.restore()
        self._draw_label(painter, rect, label if is_artist else None, opacity)

    @staticmethod
    def _lerp_rect(a: QRect, b: QRect, t: float) -> QRect:
        if not a.isValid():
            return b
        if not b.isValid():
            return a
        x = round(a.x() + (b.x() - a.x()) * t)
        y = round(a.y() + (b.y() - a.y()) * t)
        w = round(a.width() + (b.width() - a.width()) * t)
        h = round(a.height() + (b.height() - a.height()) * t)
        return QRect(x, y, w, h)

    @staticmethod
    def _scale_rect_about_center(rect: QRect, zoom: float) -> QRect:
        if zoom == 1.0 or not rect.isValid():
            return rect
        cx, cy = rect.center().x(), rect.center().y()
        w, h = rect.width() * zoom, rect.height() * zoom
        return QRect(round(cx - w / 2), round(cy - h / 2), round(w), round(h))

    def _layout_rect(self, pixmap: Optional[QPixmap], is_artist: bool, w: int, h: int) -> QRect:
        """Compute the rect this content would occupy at rest (transition
        progress 1), without painting anything."""
        if pixmap and not pixmap.isNull() and is_artist:
            return self._artist_photo_rect(pixmap, w, h)
        elif pixmap and not pixmap.isNull():
            return self._square_art_rect(pixmap, w, h)
        return self._placeholder_rect(w, h)

    def _placeholder_rect(self, w: int, h: int) -> QRect:
        side = min(w, h)
        x, y = (w - side) // 2, (h - side) // 2
        return QRect(x, y, side, side)

    def _paint_placeholder_in_rect(self, painter: QPainter, rect: QRect):
        if not rect.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return
        x, y, rw, rh = rect.x(), rect.y(), rect.width(), rect.height()
        path = QPainterPath()
        path.addRoundedRect(x, y, rw, rh, self._RADIUS, self._RADIUS)
        painter.setClipPath(path)
        # Default art: dark gradient with a music note
        bg = QLinearGradient(x, y, x + rw, y + rh)
        bg.setColorAt(0.0, QColor(30, 35, 60))
        bg.setColorAt(1.0, QColor(15, 18, 35))
        painter.fillPath(path, bg)

        # Draw a simple music note using text
        painter.setClipping(False)
        note_font = QFont("Arial", max(24, min(rw, rh) // 4), QFont.Bold)
        painter.setFont(note_font)
        painter.setPen(QColor(100, 120, 200, 80))
        painter.drawText(x, y, rw, rh, Qt.AlignCenter, "♪")

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

    def _square_art_rect(self, pixmap: QPixmap, w: int, h: int) -> QRect:
        """Album art's at-rest rect: nearly-square art keeps its native aspect
        ratio; anything further off-square is cropped to a perfect square."""
        pw, ph = pixmap.width(), pixmap.height()

        if pw > 0 and ph > 0 and abs((pw / ph) - 1.0) <= self._SQUARE_TOLERANCE:
            fit_scale = min(w / pw, h / ph)
            scale = min(fit_scale, self._MAX_UPSCALE)
            art_w = max(1, round(pw * scale))
            art_h = max(1, round(ph * scale))
            x, y = (w - art_w) // 2, (h - art_h) // 2
            return QRect(x, y, art_w, art_h)

        side = min(w, h)
        x, y = (w - side) // 2, (h - side) // 2
        return QRect(x, y, side, side)

    def _artist_photo_rect(self, pixmap: QPixmap, w: int, h: int) -> QRect:
        """Artist photo's at-rest rect: keeps its native aspect ratio and
        avoids over-enlarging small images, instead of cropping it into a
        forced square."""
        pw, ph = pixmap.width(), pixmap.height()
        if pw <= 0 or ph <= 0:
            return QRect()

        fit_scale = min(w / pw, h / ph)
        scale = min(fit_scale, self._MAX_UPSCALE)
        new_w = max(1, round(pw * scale))
        new_h = max(1, round(ph * scale))
        x, y = (w - new_w) // 2, (h - new_h) // 2
        return QRect(x, y, new_w, new_h)

    def _paint_pixmap_cover(self, painter: QPainter, pixmap: QPixmap, rect: QRect):
        """Draw `pixmap` scaled/cropped to cover `rect`, clipped to its
        rounded shape. When `rect` is the content's own at-rest rect (the
        steady-state case, once the crossfade settles) this reproduces the
        un-cropped layout exactly; mid-transition, where `rect` is
        interpolated between the outgoing and incoming content's shapes, it
        crops as needed so the frame morphs smoothly instead of the image
        stretching or snapping between aspect ratios."""
        if not rect.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(
            rect.x(), rect.y(), rect.width(), rect.height(), self._RADIUS, self._RADIUS
        )
        painter.setClipPath(path)
        scaled = pixmap.scaled(
            rect.width(), rect.height(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        ox = rect.x() + (rect.width() - scaled.width()) // 2
        oy = rect.y() + (rect.height() - scaled.height()) // 2
        painter.drawPixmap(ox, oy, scaled)
        painter.setClipping(False)
