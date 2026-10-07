# ──────────────────────────────────────────────────────────────────────────────
#  Lyric column (painted, scrolling)
# ──────────────────────────────────────────────────────────────────────────────

from bisect import bisect_right

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
    brightness. With no timing to follow, seeing more text wins: the font
    shrinks (down to ``_MIN_PT``) until every line fits the widget. Lyrics too
    long even at that size are paced by song progress instead: while
    following, ``set_progress()`` centres the line at that share of the
    content height, the same way synced lyrics centre the active line, but
    without highlighting it (the estimate is rough).
    """

    follow_changed = Signal(bool)

    _FONT = QFont("Georgia", 20, QFont.Bold)  # synced lyrics; plain lyrics' largest size
    _MIN_PT = 13  # smallest plain-lyrics size that stays readable over the backdrop
    _LINE_GAP = 14  # at _FONT's size; scales with the font
    _BLANK_H = 10  # an empty lyric line (instrumental break) is a short gap; scales too
    _ANCHOR = 0.5  # active line centre, as a fraction of the widget height
    _EDGE_FADE = 56  # px over which lines fade out at an edge more text lies past
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
        self._paced = -1  # plain lyrics: line expected now from song progress
        self._scroll = 0.0  # content y shown at the widget's top edge

        self._font = QFont(self._FONT)
        self._tops: list[int] = []
        self._heights: list[int] = []
        self._content_h = 0
        self._layout_key: tuple[int, int] | None = None

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
        """Show ``lines`` scrolled to the top, in follow mode with no active
        (synced) or paced (plain) line yet."""
        self._scroll_anim.stop()
        self._emphasis_anim.stop()
        self._lines = list(lines)
        self._synced = synced and bool(self._lines)
        self._active = -1
        self._prev_active = -1
        self._paced = -1
        self._emphasis = 1.0
        self._tops, self._heights, self._content_h = [], [], 0
        self._layout_key = None
        self._relayout()
        self._scroll = 0.0
        self._change_following(bool(self._lines))
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

    def set_progress(self, fraction: float):
        """Plain lyrics: centre the line at ``fraction`` (0..1) of the content
        height while following. Synced lyrics ignore this."""
        if self._synced or not self._tops:
            return
        y = max(0.0, min(1.0, fraction)) * self._content_h
        idx = max(0, bisect_right(self._tops, y) - 1)
        if idx == self._paced:
            return
        self._paced = idx
        if self._following:
            self._scroll_to(self._follow_scroll(idx))

    def set_following(self, on: bool):
        """Turn follow mode on/off. An empty column cannot follow."""
        on = on and bool(self._lines)
        if on == self._following:
            return
        self._change_following(on)
        if on:
            self._scroll_to(self._follow_scroll(self._follow_idx()))
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
        # Plain lyrics fit their size to the height, so it is part of the key.
        key = (w, 0 if self._synced else self.height())
        if w <= 0 or key == self._layout_key:
            return
        self._layout_key = key
        self._font = self._FONT if self._synced else self._fit_font(w, self.height())
        self._tops, self._heights, self._content_h = self._measure(self._font, w)

    def _fit_font(self, w: int, h: int) -> QFont:
        """Largest font from ``_FONT``'s size down to ``_MIN_PT`` at which every
        line fits height ``h``; ``_MIN_PT`` when even that overflows."""
        lo, hi = self._MIN_PT, self._FONT.pointSize()
        while lo < hi:  # binary search: content height grows with the size
            mid = (lo + hi + 1) // 2
            if self._measure(self._sized(mid), w)[2] <= h:
                lo = mid
            else:
                hi = mid - 1
        return self._sized(lo)

    def _sized(self, pt: int) -> QFont:
        font = QFont(self._FONT)
        font.setPointSize(pt)
        return font

    def _measure(self, font: QFont, w: int) -> tuple[list[int], list[int], int]:
        """Line tops, line heights and content height with ``font`` at width ``w``."""
        scale = font.pointSize() / self._FONT.pointSize()
        gap = round(self._LINE_GAP * scale)
        blank_h = round(self._BLANK_H * scale)
        fm = QFontMetrics(font)
        wrap_box = QRect(0, 0, w, 100_000)
        flags = Qt.AlignHCenter | Qt.TextWordWrap
        tops, heights = [], []
        y = 0
        for text in self._lines:
            h = fm.boundingRect(wrap_box, flags, text).height() if text.strip() else blank_h
            tops.append(y)
            heights.append(h)
            y += h + gap
        return tops, heights, max(0, y - gap)

    def _follow_idx(self) -> int:
        """The line follow mode keeps centred: active (synced) or paced (plain)."""
        return self._active if self._synced else self._paced

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
            self._scroll = self._follow_scroll(self._follow_idx())
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
        painter.setFont(self._font)
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
            edge = self._edge_alpha(top + line_h / 2)
            weight = self._line_weight(i)
            alpha = self._base_alpha(i)
            alpha += (self._ACTIVE_ALPHA - alpha) * weight
            color = self._mix(self._INACTIVE_RGB, self._ACTIVE_RGB, weight)
            color.setAlphaF(max(0.0, min(1.0, alpha * edge)))
            painter.setPen(color)
            painter.drawText(QRectF(self._SIDE_PAD, top, w, line_h), flags, text)

        painter.end()

    # ── line styling ──────────────────────────────────────────────────────

    def _edge_alpha(self, centre: float) -> float:
        """Fade factor for a line centred at widget y ``centre``. An edge fades
        only while more text is scrolled past it, so the first and last lines
        are never dimmed when they are really the first and last."""
        lo, hi = self._scroll_bounds()
        fade = 1.0
        if self._scroll > lo + 0.5:
            fade = min(fade, centre / self._EDGE_FADE)
        if self._scroll < hi - 0.5:
            fade = min(fade, (self.height() - centre) / self._EDGE_FADE)
        return max(0.0, min(1.0, fade))

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
