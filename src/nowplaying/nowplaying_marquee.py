from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget


class FadedScrollArea(QScrollArea):
    """Scroll area — just a clean wrapper for plain lyrics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setObjectName("NowPlayingLyricsScrollArea")
        self.setWidgetResizable(True)


class MarqueeLabel(QWidget):
    """
    A single-line label that scrolls (pans) its text horizontally when the
    text is wider than the widget.  No album-art space is consumed.
    """

    _SCROLL_STEP_PX = 1  # pixels per tick
    _SCROLL_INTERVAL_MS = 30  # ~33 fps
    _PAUSE_TICKS = 60  # ticks to pause at each end (~1.8 s)
    _FADE_WIDTH = 18  # px of fade-out at edges when scrolling

    def __init__(self, text: str, font: QFont, color: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._font = font
        self._color = color
        self._offset = 0  # current horizontal scroll offset
        self._direction = 1  # 1 = scrolling right-to-left, -1 = back
        self._pause_remaining = self._PAUSE_TICKS
        self._text_width = 0

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setProperty("bgTransparent", True)

        self._timer = QTimer(self)
        self._timer.setInterval(self._SCROLL_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def set_text(self, text: str):
        self._text = text
        self._offset = 0
        self._direction = 1
        self._pause_remaining = self._PAUSE_TICKS
        self._timer.stop()
        # Update width immediately so the new text isn't clipped against the
        # previous string's stale width before the deferred check below runs.
        self._text_width = self.fontMetrics().horizontalAdvance(self._text)
        self.update()
        # Defer scroll check until after first paint gives us real geometry
        QTimer.singleShot(200, self._check_scroll_needed)

    def _check_scroll_needed(self):
        fm = self.fontMetrics()
        self._text_width = fm.horizontalAdvance(self._text)
        if self._text_width > self.width():
            self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._check_scroll_needed()

    def _tick(self):
        if self._pause_remaining > 0:
            self._pause_remaining -= 1
            return
        max_offset = self._text_width - self.width() + self._FADE_WIDTH
        self._offset += self._SCROLL_STEP_PX * self._direction
        if self._offset >= max_offset:
            self._offset = max_offset
            self._direction = -1
            self._pause_remaining = self._PAUSE_TICKS
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
            self._pause_remaining = self._PAUSE_TICKS
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self._font)

        # Parse colour string into QColor
        c = QColor(self._color) if not self._color.startswith("rgba") else None
        if c is None:
            # Handle rgba(r,g,b,a) where a is 0–1
            nums = [
                x.strip() for x in self._color.lstrip("rgba(").rstrip(")").split(",")
            ]
            try:
                r, g, b = int(nums[0]), int(nums[1]), int(nums[2])
                a = int(float(nums[3]) * 255) if len(nums) > 3 else 255
            except (ValueError, IndexError):
                r, g, b, a = 180, 190, 240, 178
            c = QColor(r, g, b, a)

        painter.setPen(c)
        painter.drawText(
            QRect(-self._offset, 0, self._text_width + 4, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._text,
        )

        # Fade edges when scrolling
        if self._text_width > self.width():
            w = self.width()
            h = self.height()
            bg = QColor(0, 0, 0, 0)  # transparent
            for x, fade_right in ((0, False), (w - self._FADE_WIDTH, True)):
                grad = QLinearGradient(
                    QPoint(x, 0),
                    QPoint(x + self._FADE_WIDTH * (1 if fade_right else -1), 0),
                )
                grad.setColorAt(0.0, QColor(0, 0, 0, 200))
                grad.setColorAt(1.0, bg)
                painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
                painter.fillRect(
                    x if fade_right else x - self._FADE_WIDTH,
                    0,
                    self._FADE_WIDTH,
                    h,
                    grad,
                )
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        painter.end()

    def fontMetrics(self):
        return QFontMetrics(self._font)
