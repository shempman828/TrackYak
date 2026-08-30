"""
word_cloud_widget.py

WordCloudWidget: flow-wrapped "tag list" layout for ranked (word, weight)
pairs -- the lyrics corpus word cloud and the high/low-rated word lists all
use this rather than a spiral/collision-packed layout, matching the
QPainter house style and keeping the implementation small.
"""

import math

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QToolTip

from src.statistics.charts.theme_palette import ThemedChartWidget

MIN_FONT_SIZE = 9
MAX_FONT_SIZE = 24
CHIP_PADDING_X = 8
CHIP_PADDING_Y = 4
CHIP_GAP_X = 6
CHIP_GAP_Y = 6


class WordCloudWidget(ThemedChartWidget):
    """Frequency-sorted words wrapped like chips, font size log-scaled to
    each word's weight relative to the top word in the set."""

    # (surface, chip_fill, chip_border, text)
    _THEME_PALETTE = {
        "dark_mode": (
            QColor("#11121a"),
            QColor(133, 153, 234, 45),
            QColor(133, 153, 234, 110),
            QColor("#b8c0f0"),
        ),
        "light_mode": (
            QColor("#ffffff"),
            QColor(133, 153, 234, 35),
            QColor(133, 153, 234, 130),
            QColor("#2b2c36"),
        ),
        "colorful_mode": (
            QColor("#ffffff"),
            QColor(234, 133, 153, 40),
            QColor(234, 133, 153, 140),
            QColor("#1c1c21"),
        ),
        "accessibility_mode": (
            QColor("#ffffff"),
            QColor(168, 88, 12, 45),
            QColor(168, 88, 12, 150),
            QColor("#1c1c21"),
        ),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._words: list[tuple[str, float]] = []
        # (rect, word, weight, font_size)
        self._layout: list[tuple[QRect, str, float, int]] = []
        self._hovered_index: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._apply_theme_palette()

    def set_data(self, words: list[tuple[str, float]]):
        """words: [(word, weight), ...] sorted most-significant first."""
        self._apply_theme_palette()
        self._words = words or []
        self._hovered_index = None
        self._relayout(self.width() or 400)
        self.update()

    def resizeEvent(self, event):
        self._relayout(event.size().width())
        super().resizeEvent(event)

    def _font_size_for(self, weight, max_weight):
        if max_weight <= 0:
            return MIN_FONT_SIZE
        ratio = math.log(1 + weight) / math.log(1 + max_weight)
        return int(MIN_FONT_SIZE + ratio * (MAX_FONT_SIZE - MIN_FONT_SIZE))

    def _relayout(self, width):
        self._layout = []
        if not self._words or width <= 0:
            self.setMinimumHeight(MAX_FONT_SIZE + 20)
            return

        max_weight = max(weight for _word, weight in self._words)
        x = CHIP_PADDING_X
        y = CHIP_PADDING_Y
        row_height = 0

        for word, weight in self._words:
            font_size = self._font_size_for(weight, max_weight)
            bold = font_size >= MAX_FONT_SIZE * 0.7
            font = QFont("Cambria", font_size, QFont.Bold if bold else QFont.Normal)
            metrics = QFontMetrics(font)
            chip_w = metrics.horizontalAdvance(word) + 2 * CHIP_PADDING_X
            chip_h = metrics.height() + 2 * CHIP_PADDING_Y

            if x + chip_w > width - CHIP_PADDING_X and x > CHIP_PADDING_X:
                x = CHIP_PADDING_X
                y += row_height + CHIP_GAP_Y
                row_height = 0

            self._layout.append((QRect(x, y, chip_w, chip_h), word, weight, font_size))
            x += chip_w + CHIP_GAP_X
            row_height = max(row_height, chip_h)

        total_height = y + row_height + CHIP_PADDING_Y
        self.setMinimumHeight(max(MAX_FONT_SIZE + 20, total_height))

    def paintEvent(self, event):
        surface, chip_fill, chip_border, text_color = self._palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), surface)

        if not self._layout:
            painter.setPen(text_color)
            painter.setFont(QFont("Cambria", 10))
            painter.drawText(self.rect(), Qt.AlignCenter, "No data yet")
            painter.end()
            return

        for index, (rect, word, _weight, font_size) in enumerate(self._layout):
            painter.setPen(chip_border)
            painter.setBrush(chip_fill.lighter(130) if index == self._hovered_index else chip_fill)
            painter.drawRoundedRect(rect, 4, 4)

            painter.setPen(text_color)
            bold = font_size >= MAX_FONT_SIZE * 0.7
            painter.setFont(QFont("Cambria", font_size, QFont.Bold if bold else QFont.Normal))
            painter.drawText(rect, Qt.AlignCenter, word)

        painter.end()

    def _index_at(self, pos):
        for index, (rect, _word, _weight, _size) in enumerate(self._layout):
            if rect.contains(pos):
                return index
        return None

    def mouseMoveEvent(self, event):
        idx = self._index_at(event.position().toPoint())
        if idx != self._hovered_index:
            self._hovered_index = idx
            self.update()
        if idx is not None:
            _rect, word, weight, _size = self._layout[idx]
            QToolTip.showText(event.globalPosition().toPoint(), f"{word}: {weight:g}", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):
        if self._hovered_index is not None:
            self._hovered_index = None
            self.update()
        QToolTip.hideText()

    def sizeHint(self):
        return QSize(400, self.minimumHeight())
