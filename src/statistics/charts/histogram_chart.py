"""
histogram_chart.py

HistogramChart: continuous-value histogram (BPM, track gain, file size, the
16 advanced DSP columns, ...) rendered from a
src.statistics.stats.helpers.DistributionStats bucket list.
"""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QSizePolicy

from src.statistics.charts.theme_palette import STANDARD_BAR_PALETTE, ThemedChartWidget

# Minimum drawable bar-area height; bars are painted relative to whatever
# vertical space the widget is actually given (>= this).
BAR_AREA_HEIGHT = 170
AXIS_LABEL_HEIGHT = 18
STATS_LABEL_HEIGHT = 20
TOP_PAD = 6


class HistogramChart(ThemedChartWidget):
    """Bar histogram for a continuous numeric column, built from a
    DistributionStats instance (see stats/helpers.py)."""

    # (surface, bar, bar_border, text, muted_text)
    _THEME_PALETTE = STANDARD_BAR_PALETTE

    def __init__(self, unit: str = "", value_format: str = "{:.1f}", parent=None):
        super().__init__(parent)
        self._distribution = None
        self._unit = unit
        self._value_format = value_format
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(BAR_AREA_HEIGHT + AXIS_LABEL_HEIGHT + STATS_LABEL_HEIGHT + TOP_PAD)
        self._apply_theme_palette()

    def set_data(self, distribution: object | None):
        """`distribution` is a DistributionStats or None (no qualifying data)."""
        self._apply_theme_palette()
        self._distribution = distribution
        self.update()

    def _fmt(self, value: float) -> str:
        return f"{self._value_format.format(value)}{self._unit}"

    def paintEvent(self, event):
        surface, bar_color, bar_border, text_color, muted_color = self._palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), surface)

        dist = self._distribution
        if not dist or not dist.buckets:
            painter.setPen(muted_color)
            painter.setFont(QFont("Cambria", 10))
            painter.drawText(self.rect(), Qt.AlignCenter, "Not enough data")
            painter.end()
            return

        w = self.width()
        bar_area_h = max(self.height() - AXIS_LABEL_HEIGHT - STATS_LABEL_HEIGHT - TOP_PAD * 2, 40)
        bars_bottom = TOP_PAD + bar_area_h
        max_count = max(count for _s, _e, count in dist.buckets) or 1
        n_buckets = len(dist.buckets)
        bar_gap = 1
        bar_w = max((w - 8) / n_buckets - bar_gap, 1)

        painter.setPen(bar_border)
        painter.setBrush(bar_color)
        for i, (_start, _end, count) in enumerate(dist.buckets):
            x = 4 + i * (bar_w + bar_gap)
            bar_h = (count / max_count) * (bar_area_h - 4)
            rect = QRectF(x, bars_bottom - bar_h, bar_w, bar_h)
            painter.drawRect(rect)

        painter.setPen(muted_color)
        painter.setFont(QFont("Cambria", 8))
        axis_rect_left = QRectF(4, bars_bottom + 2, w / 2 - 4, AXIS_LABEL_HEIGHT)
        axis_rect_right = QRectF(w / 2, bars_bottom + 2, w / 2 - 4, AXIS_LABEL_HEIGHT)
        painter.drawText(axis_rect_left, Qt.AlignLeft | Qt.AlignVCenter, self._fmt(dist.minimum))
        painter.drawText(axis_rect_right, Qt.AlignRight | Qt.AlignVCenter, self._fmt(dist.maximum))

        painter.setPen(text_color)
        stats_rect = QRectF(4, bars_bottom + AXIS_LABEL_HEIGHT, w - 8, STATS_LABEL_HEIGHT)
        painter.drawText(
            stats_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            f"n={dist.n}  •  mean={self._fmt(dist.mean)}  •  "
            f"median={self._fmt(dist.median)}  •  σ={self._fmt(dist.stdev)}",  # noqa: RUF001
        )

        painter.end()

    def sizeHint(self):
        return QSize(360, BAR_AREA_HEIGHT + AXIS_LABEL_HEIGHT + STATS_LABEL_HEIGHT + TOP_PAD)
