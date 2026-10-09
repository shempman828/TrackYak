# ──────────────────────────────────────────────────────────────────────────────
#  Progress strip (read-only elapsed / remaining bar under the art)
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


def format_ms(ms: int) -> str:
    """``m:ss`` (or ``h:mm:ss`` from one hour) for a position in ms."""
    total = max(0, int(ms)) // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _ProgressStrip(QWidget):
    """Read-only playback bar with elapsed time on the left and remaining time on the right."""

    # Seeking stays in the player dock. With no known duration only the empty track is painted.

    _BAR_H = 3
    _TEXT_GAP = 6
    _FONT = QFont("Cambria", 9)

    _TRACK = QColor(184, 192, 240, 40)
    _FILL = QColor(133, 153, 234, 210)
    _TEXT = QColor(184, 192, 240, 140)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("bgTransparent", True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._duration_ms = 0
        self._position_ms = 0
        self.setAccessibleName("Song progress")

    # ── public API ────────────────────────────────────────────────────────

    def set_duration(self, ms: int):
        """Set the track length (ms); 0 means unknown."""
        self._duration_ms = max(0, int(ms or 0))
        self._position_ms = min(self._position_ms, self._duration_ms)
        self._sync_accessible()
        self.update()

    def set_position(self, ms: int):
        """Set the playback position (ms), repainting only when something visible changes."""
        ms = max(0, int(ms or 0))
        if self._duration_ms:
            ms = min(ms, self._duration_ms)
        # Repaint only when the shown second or the bar pixel changes.
        if ms // 1000 != self._position_ms // 1000 or self._fill_px(ms) != self._fill_px(self._position_ms):
            second_changed = ms // 1000 != self._position_ms // 1000
            self._position_ms = ms
            if second_changed:
                self._sync_accessible()
            self.update()
        else:
            self._position_ms = ms

    def reset(self):
        """Clear the duration and position."""
        self._duration_ms = 0
        self._position_ms = 0
        self._sync_accessible()
        self.update()

    def fraction(self) -> float:
        """Played share of the track, 0..1."""
        return self._position_ms / self._duration_ms if self._duration_ms else 0.0

    def elapsed_text(self) -> str:
        """Elapsed time, or "" with no duration."""
        return format_ms(self._position_ms) if self._duration_ms else ""

    def remaining_text(self) -> str:
        """Remaining time with a leading minus, or "" with no duration."""
        if not self._duration_ms:
            return ""
        return f"−{format_ms(self._duration_ms - self._position_ms)}"  # noqa: RUF001 (U+2212 minus glyph)

    # ── painting ──────────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        return QSize(200, self._BAR_H + self._TEXT_GAP + QFontMetrics(self._FONT).height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _sync_accessible(self):
        """Give screen readers the elapsed and remaining time."""
        if self._duration_ms:
            self.setAccessibleDescription(f"{self.elapsed_text()} elapsed, {format_ms(self._duration_ms - self._position_ms)} remaining")
        else:
            self.setAccessibleDescription("")

    def _fill_px(self, ms: int) -> int:
        return round(self.width() * ms / self._duration_ms) if self._duration_ms else 0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        r = self._BAR_H / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._TRACK)
        painter.drawRoundedRect(QRectF(0, 0, w, self._BAR_H), r, r)

        if not self._duration_ms:
            painter.end()
            return

        fill = w * self.fraction()
        if fill > 0:
            painter.setBrush(self._FILL)
            painter.drawRoundedRect(QRectF(0, 0, max(fill, self._BAR_H), self._BAR_H), r, r)

        painter.setFont(self._FONT)
        painter.setPen(self._TEXT)
        text_rect = QRectF(0, self._BAR_H + self._TEXT_GAP, w, self.height())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, self.elapsed_text())
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignTop, self.remaining_text())
        painter.end()
