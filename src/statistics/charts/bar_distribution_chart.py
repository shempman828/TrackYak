"""
bar_distribution_chart.py

BarDistributionChart: categorical bar chart (musical key, time signature,
instrumental/classical split, album release years, ...) rendered from a
{label: count} dict.
"""

from typing import Dict, Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QSizePolicy

from src.statistics.charts.theme_palette import STANDARD_BAR_PALETTE, ThemedChartWidget

BAR_AREA_HEIGHT = 90
LABEL_HEIGHT = 16


class BarDistributionChart(ThemedChartWidget):
    """Vertical bar chart over an unordered set of categories."""

    # (surface, bar, bar_border, text, muted_text)
    _THEME_PALETTE = STANDARD_BAR_PALETTE

    def __init__(
        self,
        sort_by: str = "count",
        max_categories: int = 24,
        parent=None,
    ):
        """`sort_by`: "count" (largest first) or "label" (as given, e.g. for
        chronological year labels)."""
        super().__init__(parent)
        self._data: Dict[str, int] = {}
        self._sort_by = sort_by
        self._max_categories = max_categories
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(BAR_AREA_HEIGHT + LABEL_HEIGHT)
        self._apply_theme_palette()

    def set_data(self, data: Optional[Dict[str, int]]):
        self._apply_theme_palette()
        self._data = dict(data) if data else {}
        self.update()

    def _ordered_items(self):
        items = list(self._data.items())
        if self._sort_by == "label":
            items.sort(key=lambda kv: kv[0])
        else:
            items.sort(key=lambda kv: kv[1], reverse=True)
        if len(items) > self._max_categories:
            items = items[: self._max_categories]
        return items

    def paintEvent(self, event):
        surface, bar_color, bar_border, text_color, muted_color = self._palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), surface)

        items = self._ordered_items()
        if not items:
            painter.setPen(muted_color)
            painter.setFont(QFont("Cambria", 10))
            painter.drawText(self.rect(), Qt.AlignCenter, "No data yet")
            painter.end()
            return

        w = self.width()
        bars_bottom = 6 + BAR_AREA_HEIGHT
        max_count = max(count for _label, count in items) or 1
        n = len(items)
        bar_gap = 4
        bar_w = max((w - 8) / n - bar_gap, 2)

        painter.setFont(QFont("Cambria", 7))
        for i, (label, count) in enumerate(items):
            x = 4 + i * (bar_w + bar_gap)
            bar_h = (count / max_count) * (BAR_AREA_HEIGHT - 4)
            painter.setPen(bar_border)
            painter.setBrush(bar_color)
            painter.drawRect(QRectF(x, bars_bottom - bar_h, bar_w, bar_h))

            painter.setPen(text_color)
            label_rect = QRectF(x - 2, bars_bottom + 2, bar_w + 4, LABEL_HEIGHT)
            elided = painter.fontMetrics().elidedText(
                str(label), Qt.ElideRight, int(bar_w + 4)
            )
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignVCenter, elided)

        painter.end()

    def sizeHint(self):
        return QSize(400, BAR_AREA_HEIGHT + LABEL_HEIGHT)
