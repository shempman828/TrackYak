"""
waveform_seekbar.py — the Player Dock's click/drag seek control.

Draws the current track's min/max amplitude envelope (from
:mod:`src.player.waveform_cache`) split into played / unplayed regions with a
playhead line. With no envelope available yet — still generating, decode
failed, unreadable file — it falls back to a plain filled progress bar and
stays fully seekable.

Colours come from the widget palette so light and dark themes both work.
The widget never seeks on its own: it emits ``seek_requested(ms)`` on mouse
release and leaves the actual seek to the dock (which still gates it on
"same track, non-zero duration").
"""

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

from src.player.waveform_cache import N_BUCKETS

_MIN_WIDTH = 200
_HEIGHT = 40
_V_PAD = 4  # px above/below the envelope
_TRACK_H = 6  # fallback progress-bar thickness


class WaveformSeekBar(QWidget):
    """A waveform-or-progress seek bar. Drop-in for the old ``QSlider``."""

    seek_requested = Signal(int)  # milliseconds, emitted on release
    scrub_started = Signal()  # mouse pressed on the bar (drag/click begun)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms: int = 0
        self._position_ms: int = 0
        self._peaks: np.ndarray | None = None
        self._dragging: bool = False
        self._drag_ms: int = 0

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(_MIN_WIDTH)
        self.setMinimumHeight(_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

    # -- public API ------------------------------------------------------------

    def set_duration(self, ms: int) -> None:
        self._duration_ms = max(0, int(ms))
        self.update()

    def set_position(self, ms: int) -> None:
        """Move the playhead. Ignored while the user is dragging so a stale
        engine tick can't yank the handle off the cursor."""
        if self._dragging:
            return
        self._position_ms = max(0, int(ms))
        self.update()

    def set_peaks(self, peaks: np.ndarray | None) -> None:
        """Attach (or clear, with ``None``) the min/max envelope for the
        current track. Clearing selects the plain-bar render."""
        self._peaks = peaks if self._peaks_usable(peaks) else None
        self.update()

    def clear(self) -> None:
        """Reset to the empty, inert state for a track change."""
        self._peaks = None
        self._position_ms = 0
        self._duration_ms = 0
        self._dragging = False
        self.update()

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    # -- geometry ------------------------------------------------------------

    def _usable_width(self) -> int:
        return max(1, self.width())

    def _ms_at_x(self, x: float) -> int:
        if self._duration_ms <= 0:
            return 0
        frac = min(1.0, max(0.0, x / self._usable_width()))
        return round(frac * self._duration_ms)

    def _x_at_ms(self, ms: int) -> float:
        if self._duration_ms <= 0:
            return 0.0
        return min(1.0, max(0.0, ms / self._duration_ms)) * self._usable_width()

    @staticmethod
    def _peaks_usable(peaks: np.ndarray | None) -> bool:
        return (
            peaks is not None
            and getattr(peaks, "ndim", 0) == 2
            and peaks.shape[1] == 2
            and len(peaks) > 0
        )

    # -- interaction -------------------------------------------------------

    def mousePressEvent(self, event):
        if not self.isEnabled() or self._duration_ms <= 0 or event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self._drag_ms = self._ms_at_x(event.position().x())
        self.scrub_started.emit()
        self.update()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        self._drag_ms = self._ms_at_x(event.position().x())
        self.update()

    def mouseReleaseEvent(self, event):
        if not self._dragging or event.button() != Qt.LeftButton:
            return
        self._dragging = False
        self._drag_ms = self._ms_at_x(event.position().x())
        self.seek_requested.emit(int(self._drag_ms))
        self.update()

    # -- painting -------------------------------------------------------------

    def _played_ms(self) -> int:
        return self._drag_ms if self._dragging else self._position_ms

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        pal = self.palette()
        played_col = pal.color(QPalette.Highlight)
        unplayed_col = pal.color(QPalette.Mid)
        head_col = pal.color(QPalette.Text)
        if not self.isEnabled():
            played_col = _dim(played_col)
            unplayed_col = _dim(unplayed_col)
            head_col = _dim(head_col)

        w = self._usable_width()
        h = self.height()
        painter.fillRect(self.rect(), pal.color(QPalette.Base))

        frac_played = 0.0
        if self._duration_ms > 0:
            frac_played = min(1.0, max(0.0, self._played_ms() / self._duration_ms))
        split_x = frac_played * w

        if self._peaks is not None:
            self._paint_envelope(painter, w, h, split_x, played_col, unplayed_col)
        else:
            self._paint_progress(painter, w, h, split_x, played_col, unplayed_col)

        if self._duration_ms > 0:
            head_x = self._x_at_ms(self._played_ms())
            painter.setPen(head_col)
            painter.drawLine(int(head_x), 0, int(head_x), h)

        painter.end()

    def _paint_envelope(self, painter, w, h, split_x, played_col, unplayed_col):
        mid = h / 2.0
        amp = max(1.0, mid - _V_PAD)
        cols = max(1, min(w, N_BUCKETS))
        edges = np.linspace(0, N_BUCKETS, cols + 1).astype(np.int64)
        lo = np.minimum.reduceat(self._peaks[:, 0].astype(np.float32), edges[:-1]) / 127.0
        hi = np.maximum.reduceat(self._peaks[:, 1].astype(np.float32), edges[:-1]) / 127.0

        for j in range(cols):
            x0 = round(j * w / cols)
            x1 = max(x0 + 1, round((j + 1) * w / cols))
            top = mid - hi[j] * amp
            bottom = mid - lo[j] * amp
            if bottom - top < 1.0:  # keep near-silent buckets visible
                top = mid - 0.5
                bottom = mid + 0.5
            colour = played_col if (x0 + x1) / 2.0 <= split_x else unplayed_col
            painter.fillRect(QRectF(x0, top, x1 - x0, bottom - top), colour)

    def _paint_progress(self, painter, w, h, split_x, played_col, unplayed_col):
        top = (h - _TRACK_H) / 2.0
        painter.setBrush(unplayed_col)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(0, top, w, _TRACK_H), _TRACK_H / 2.0, _TRACK_H / 2.0)
        if split_x > 0:
            painter.setBrush(played_col)
            painter.drawRoundedRect(
                QRectF(0, top, split_x, _TRACK_H), _TRACK_H / 2.0, _TRACK_H / 2.0
            )

    # -- sizing -------------------------------------------------------------

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(240, _HEIGHT)


def _dim(colour: QColor) -> QColor:
    faded = QColor(colour)
    faded.setAlpha(90)
    return faded
