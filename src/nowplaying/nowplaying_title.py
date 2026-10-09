# ──────────────────────────────────────────────────────────────────────────────
#  Title line
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.nowplaying.nowplaying_marquee import MarqueeLabel


class _AdaptiveTitle(QWidget):
    """Track title that word-wraps up to ``_MAX_LINES`` lines and pans as a marquee beyond that."""

    # The choice is re-evaluated on every resize (width drives the line count);
    # the first evaluation retries briefly until real geometry exists.
    _MAX_LINES = 3
    _MAX_RETRIES = 10

    def __init__(self, text: str, font: QFont, color: str, parent=None):
        super().__init__(parent)
        self._font = font
        self._text = text
        self._retries = 0
        self.setProperty("bgTransparent", True)
        self.setAccessibleName(text)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._wrap = QLabel(text)
        self._wrap.setFont(font)
        self._wrap.setWordWrap(True)
        self._wrap.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._wrap.setStyleSheet(f"color: {color}; background: transparent;")
        self._wrap.setProperty("bgTransparent", True)
        lay.addWidget(self._wrap)

        # Off-screen twin used only to measure wrapped height. ``_wrap`` itself
        # can't be measured: ``_apply_layout`` pins it with ``setFixedHeight``
        # and ``QLabel.heightForWidth`` clamps to the widget's max height, so
        # measuring ``_wrap`` just reads back the previous title's pinned height
        # and the title could only ever grow, never shrink.
        self._probe = QLabel()
        self._probe.setFont(font)
        self._probe.setWordWrap(True)
        self._probe.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._probe.hide()

        self._marquee = MarqueeLabel(text, font, color)
        self._marquee.setFixedHeight(QFontMetrics(font).height())
        self._marquee.hide()
        lay.addWidget(self._marquee)

        self._apply_layout()

    # ── public API ────────────────────────────────────────────────────────
    def set_text(self, text: str):
        """Show ``text``, choosing wrap or marquee for the current width."""
        self._text = text
        self._retries = 0
        self.setAccessibleName(text)
        self._apply_layout()

    # ── internals ─────────────────────────────────────────────────────────
    def _avail_width(self) -> int:
        """Width available for the title text."""
        w = self.contentsRect().width()
        if w <= 0:
            w = self._wrap.contentsRect().width()
        return w

    def _line_count(self, text: str, width: int) -> int:
        """Number of lines ``text`` wraps to at ``width``."""
        if width <= 0:
            return 1
        # Measure with a QLabel configured exactly like the one that paints the
        # title: QFontMetrics.boundingRect can pick a different break point and
        # leave a blank trailing row. Read it off the unconstrained ``_probe``,
        # never off ``_wrap``, which ``_apply_layout`` pins with setFixedHeight.
        self._probe.setText(text)
        h = self._probe.heightForWidth(width)
        if h <= 0:
            return 1
        return max(1, round(h / QFontMetrics(self._font).lineSpacing()))

    def _apply_layout(self):
        """Show the wrapped label or the marquee, by the wrapped line count."""
        width = self._avail_width()
        if width <= 0 and self._retries < self._MAX_RETRIES:
            self._retries += 1
            QTimer.singleShot(50, self._apply_layout)
            return

        lines = self._line_count(self._text, width)
        if lines > self._MAX_LINES:
            self._wrap.hide()
            self._marquee.show()
            self._marquee.set_text(self._text)
        else:
            self._marquee.hide()
            self._wrap.setText(self._text)
            self._wrap.setFixedHeight(lines * QFontMetrics(self._font).lineSpacing())
            self._wrap.show()

    def resizeEvent(self, event):
        """Re-check wrap vs. marquee at the new width."""
        super().resizeEvent(event)
        self._apply_layout()
