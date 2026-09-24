# ──────────────────────────────────────────────────────────────────────────────
#  Lyric column (painted, scrolling)
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class _LyricColumn(QWidget):
    """Every lyric line of the track in one painted, scrolling column.

    Synced lyrics: the active line is bright and sits at the vertical centre
    of the widget; the lines above (already sung) and below (coming up) dim
    with distance. The scroll is clamped to the content, so the first lines
    start at the top, the last lines end at the bottom, and lyrics that fit
    the widget never scroll. Each change of active line animates both the scroll and
    the brightness hand-over from the old line to the new one.

    While ``is_following()`` is True the column scrolls itself to keep the
    active line centred (as far as the clamp allows). A mouse-wheel scroll, or ``set_following(False)``,
    lets the user browse every line freely; the active line keeps its
    highlight so it can still be found. ``follow_changed`` reports both.

    Plain (unsynced) lyrics have no active line: every line gets the same
    brightness and the column scrolls freely from the top.
    """

    follow_changed = Signal(bool)

    _FONT = QFont("Georgia", 20, QFont.Bold)
    _LINE_GAP = 14
    _BLANK_H = 10  # an empty lyric line (instrumental break) is a short gap
    _ANCHOR = 0.5  # active line centre, as a fraction of the widget height
    _EDGE_FADE = 56  # px over which lines fade out at the top/bottom edge
    _SIDE_PAD = 8
    _BOTTOM_PAD = 24  # free scroll past the last line
    _WHEEL_STEP_PX = 64  # per 120 units of wheel angle (one notch)
    _SCROLL_MS = 480
    _EMPHASIS_MS = 380

    _ACTIVE_RGB = QColor(236, 240, 255)
    _INACTIVE_RGB = QColor(170, 182, 240)
    _ACTIVE_ALPHA = 0.96
    _BROWSE_ALPHA = 0.55  # every non-active line while not following
    _PLAIN_ALPHA = 0.80  # unsynced lyrics

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("bgTransparent", True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._lines: list[str] = []
        self._synced = False
        self._following = False
        self._active = -1
        self._prev_active = -1
        self._emphasis = 1.0  # 0 → highlight still on _prev_active, 1 → on _active
        self._scroll = 0.0  # content y shown at the widget's top edge

        self._tops: list[int] = []
        self._heights: list[int] = []
        self._content_h = 0
        self._layout_w = -1

        self._scroll_anim = QPropertyAnimation(self, b"scrollPos", self)
        self._scroll_anim.setDuration(self._SCROLL_MS)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._emphasis_anim = QPropertyAnimation(self, b"emphasis", self)
        self._emphasis_anim.setDuration(self._EMPHASIS_MS)
        self._emphasis_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._emphasis_anim.setStartValue(0.0)
        self._emphasis_anim.setEndValue(1.0)

    # ── animated properties ───────────────────────────────────────────────

    def _get_scroll(self) -> float:
        return self._scroll

    def _set_scroll(self, v: float):
        self._scroll = v
        self.update()

    scrollPos = Property(float, _get_scroll, _set_scroll)

    def _get_emphasis(self) -> float:
        return self._emphasis

    def _set_emphasis(self, v: float):
        self._emphasis = v
        self.update()

    emphasis = Property(float, _get_emphasis, _set_emphasis)

    # ── public API ────────────────────────────────────────────────────────

    def set_lines(self, lines: list[str], synced: bool):
        """Show ``lines`` scrolled to the top. Synced lyrics start in follow
        mode with no active line yet."""
        self._scroll_anim.stop()
        self._emphasis_anim.stop()
        self._lines = list(lines)
        self._synced = synced and bool(self._lines)
        self._active = -1
        self._prev_active = -1
        self._emphasis = 1.0
        self._tops, self._heights, self._content_h = [], [], 0
        self._layout_w = -1
        self._relayout()
        self._scroll = 0.0
        self._change_following(self._synced)
        self.update()

    def clear(self):
        self.set_lines([], synced=False)

    def lines(self) -> list[str]:
        return list(self._lines)

    def active_index(self) -> int:
        return self._active

    def active_text(self) -> str:
        return self._lines[self._active] if 0 <= self._active < len(self._lines) else ""

    def is_following(self) -> bool:
        return self._following

    def set_active(self, idx: int):
        """Move the highlight to line ``idx`` (-1 = before the first line)."""
        if not self._synced or idx == self._active:
            return
        self._prev_active = self._active
        self._active = idx
        self._emphasis_anim.stop()
        self._emphasis_anim.start()
        if self._following:
            self._scroll_to(self._follow_scroll(idx))

    def set_following(self, on: bool):
        """Turn follow mode on/off. Plain lyrics cannot follow."""
        on = on and self._synced
        if on == self._following:
            return
        self._change_following(on)
        if on:
            self._scroll_to(self._follow_scroll(self._active))
        self.update()

    # ── layout ────────────────────────────────────────────────────────────

    def _change_following(self, on: bool):
        if on != self._following:
            self._following = on
            self.follow_changed.emit(on)

    def _text_width(self) -> int:
        return self.width() - 2 * self._SIDE_PAD

    def _relayout(self):
        w = self._text_width()
        if w <= 0 or w == self._layout_w:
            return
        self._layout_w = w
        fm = QFontMetrics(self._FONT)
        self._tops, self._heights = [], []
        y = 0
        for text in self._lines:
            wrap_box = QRect(0, 0, w, 100_000)
            flags = Qt.AlignHCenter | Qt.TextWordWrap
            h = fm.boundingRect(wrap_box, flags, text).height() if text.strip() else self._BLANK_H
            self._tops.append(y)
            self._heights.append(h)
            y += h + self._LINE_GAP
        self._content_h = max(0, y - self._LINE_GAP)

    def _follow_scroll(self, idx: int) -> float:
        """Scroll value that centres line ``idx`` (-1 = before the first
        line), clamped so no empty space shows above or below the lyrics."""
        if not self._tops:
            return 0.0
        idx = max(0, min(idx, len(self._tops) - 1))
        centre = self._tops[idx] + self._heights[idx] / 2
        return self._clamp_scroll(centre - self.height() * self._ANCHOR)

    def _scroll_bounds(self) -> tuple[float, float]:
        hi = self._content_h + self._BOTTOM_PAD - self.height()
        return 0.0, max(0.0, hi)

    def _clamp_scroll(self, v: float) -> float:
        lo, hi = self._scroll_bounds()
        return max(lo, min(hi, v))

    def _scroll_to(self, target: float):
        self._scroll_anim.stop()
        if not self.isVisible():
            self._scroll = target
            self.update()
            return
        self._scroll_anim.setStartValue(self._scroll)
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    # ── events ────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()
        self._scroll_anim.stop()
        if self._following:
            self._scroll = self._follow_scroll(self._active)
        else:
            self._scroll = self._clamp_scroll(self._scroll)

    def wheelEvent(self, event):
        if not self._lines:
            event.ignore()
            return
        pixel = event.pixelDelta().y()
        delta = -pixel if pixel else -event.angleDelta().y() / 120 * self._WHEEL_STEP_PX
        self._change_following(False)
        self._scroll_anim.stop()
        self._scroll = self._clamp_scroll(self._scroll + delta)
        self.update()
        event.accept()

    def paintEvent(self, event):
        self._relayout()
        if not self._lines or len(self._tops) != len(self._lines):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self._FONT)
        h = self.height()
        w = self._text_width()
        flags = Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap

        for i, text in enumerate(self._lines):
            top = self._tops[i] - self._scroll
            line_h = self._heights[i]
            if top + line_h < 0:
                continue
            if top > h:
                break
            if not text.strip():
                continue
            centre = top + line_h / 2
            edge = max(0.0, min(1.0, centre / self._EDGE_FADE, (h - centre) / self._EDGE_FADE))
            weight = self._line_weight(i)
            alpha = self._base_alpha(i)
            alpha += (self._ACTIVE_ALPHA - alpha) * weight
            color = self._mix(self._INACTIVE_RGB, self._ACTIVE_RGB, weight)
            color.setAlphaF(max(0.0, min(1.0, alpha * edge)))
            painter.setPen(color)
            painter.drawText(QRectF(self._SIDE_PAD, top, w, line_h), flags, text)

        painter.end()

    # ── line styling ──────────────────────────────────────────────────────

    def _line_weight(self, i: int) -> float:
        """0..1 share of the active-line look that line ``i`` currently has."""
        if not self._synced:
            return 0.0
        if i == self._active:
            return self._emphasis
        if i == self._prev_active:
            return 1.0 - self._emphasis
        return 0.0

    def _base_alpha(self, i: int) -> float:
        if not self._synced:
            return self._PLAIN_ALPHA
        if not self._following:
            return self._BROWSE_ALPHA
        rel = i - self._active
        dist = abs(rel)
        alpha = max(0.16, 0.62 - 0.11 * (dist - 1))
        # Lines already sung recede faster than the ones coming up.
        return alpha * 0.7 if rel < 0 else alpha

    @staticmethod
    def _mix(a: QColor, b: QColor, t: float) -> QColor:
        return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t), round(a.blue() + (b.blue() - a.blue()) * t))
