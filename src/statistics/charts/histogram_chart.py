"""
histogram_chart.py

HistogramChart: continuous-value histogram (BPM, track gain, file size, the
16 advanced DSP columns, ...) rendered from a
src.statistics.stats.helpers.DistributionStats bucket list.
"""

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy

from src.statistics.charts.theme_palette import ThemedChartWidget

BAR_AREA_HEIGHT = 90
AXIS_LABEL_HEIGHT = 16
STATS_LABEL_HEIGHT = 18


class HistogramChart(ThemedChartWidget):
    """Bar histogram for a continuous numeric column, built from a
    DistributionStats instance (see stats/helpers.py)."""

    # (surface, bar, bar_border, text, muted_text)
    _THEME_PALETTE = {
        "dark_mode": (
            QColor("#11121a"),
            QColor("#8599EA"),
            QColor(133, 153, 234, 140),
            QColor("#b8c0f0"),
            QColor("#7a82a8"),
        ),
        "light_mode": (
            QColor("#ffffff"),
            QColor("#5566c0"),
            QColor(85, 102, 192, 140),
            QColor("#2b2c36"),
            QColor("#6b6f80"),
        ),
        "colorful_mode": (
            QColor("#ffffff"),
            QColor("#ea8599"),
            QColor(234, 133, 153, 140),
            QColor("#1c1c21"),
            QColor("#777777"),
        ),
        "accessibility_mode": (
            QColor("#ffffff"),
            QColor("#a8580c"),
            QColor(168, 88, 12, 140),
            QColor("#1c1c21"),
            QColor("#4a4a4a"),
        ),
    }

    def __init__(self, unit: str = "", value_format: str = "{:.1f}", parent=None):
        super().__init__(parent)
        self._distribution = None
        self._unit = unit
        self._value_format = value_format
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(BAR_AREA_HEIGHT + AXIS_LABEL_HEIGHT + STATS_LABEL_HEIGHT)
        self._apply_theme_palette()

    def set_data(self, distribution: Optional[object]):
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
        top_pad = 6
        bars_top = top_pad
        bars_bottom = top_pad + BAR_AREA_HEIGHT
        max_count = max(count for _s, _e, count in dist.buckets) or 1
        n_buckets = len(dist.buckets)
        bar_gap = 1
        bar_w = max((w - 8) / n_buckets - bar_gap, 1)

        painter.setPen(bar_border)
        painter.setBrush(bar_color)
        for i, (_start, _end, count) in enumerate(dist.buckets):
            x = 4 + i * (bar_w + bar_gap)
            bar_h = (count / max_count) * (BAR_AREA_HEIGHT - 4)
            rect = QRectF(x, bars_bottom - bar_h, bar_w, bar_h)
            painter.drawRect(rect)

        painter.setPen(muted_color)
        painter.setFont(QFont("Cambria", 8))
        axis_rect_left = QRectF(4, bars_bottom + 2, w / 2 - 4, AXIS_LABEL_HEIGHT)
        axis_rect_right = QRectF(w / 2, bars_bottom + 2, w / 2 - 4, AXIS_LABEL_HEIGHT)
        painter.drawText(
            axis_rect_left, Qt.AlignLeft | Qt.AlignVCenter, self._fmt(dist.minimum)
        )
        painter.drawText(
            axis_rect_right, Qt.AlignRight | Qt.AlignVCenter, self._fmt(dist.maximum)
        )

        painter.setPen(text_color)
        stats_rect = QRectF(
            4, bars_bottom + AXIS_LABEL_HEIGHT, w - 8, STATS_LABEL_HEIGHT
        )
        painter.drawText(
            stats_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            f"n={dist.n}  •  mean={self._fmt(dist.mean)}  •  "
            f"median={self._fmt(dist.median)}  •  σ={self._fmt(dist.stdev)}",
        )

        painter.end()

    def sizeHint(self):
        return QSize(320, BAR_AREA_HEIGHT + AXIS_LABEL_HEIGHT + STATS_LABEL_HEIGHT)
