"""
year_time_series_chart.py

YearTimeSeriesChart: a filled-area time series of a count-per-calendar-year
distribution, drawn on a continuous year axis. Built for the album Release
Year distribution, where 100+ distinct years make a one-bar-per-year
categorical chart (BarDistributionChart) unreadable -- bars collapse to
slivers and every x-axis label elides to "19...".

Missing years inside the range are treated as zero so the area profile is
continuous. Hover snaps to the nearest year and shows its exact count.
"""

from typing import ClassVar

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QToolTip

from src.statistics.widgets.theme_palette import ThemedChartWidget

_MIN_HEIGHT = 180


class YearTimeSeriesChart(ThemedChartWidget):
    """Filled-area chart: one value per calendar year on a continuous axis."""

    # (surface, line, fill, text, muted_text, gridline)
    _THEME_PALETTE: ClassVar[dict] = {
        "dark_mode": (
            QColor("#11121a"),
            QColor("#8599ea"),
            QColor(133, 153, 234, 70),
            QColor("#b8c0f0"),
            QColor("#7a82a8"),
            QColor(133, 153, 234, 40),
        ),
        "light_mode": (
            QColor("#ffffff"),
            QColor("#5566c0"),
            QColor(85, 102, 192, 60),
            QColor("#2b2c36"),
            QColor("#6b6f80"),
            QColor(43, 44, 54, 30),
        ),
        "colorful_mode": (
            QColor("#ffffff"),
            QColor("#ea8599"),
            QColor(234, 133, 153, 60),
            QColor("#1c1c21"),
            QColor("#777777"),
            QColor(133, 153, 234, 35),
        ),
        "accessibility_mode": (
            QColor("#ffffff"),
            QColor("#a8580c"),
            QColor(168, 88, 12, 55),
            QColor("#1c1c21"),
            QColor("#4a4a4a"),
            QColor(28, 28, 33, 45),
        ),
    }

    MARGIN_LEFT = 40
    MARGIN_RIGHT = 12
    MARGIN_TOP = 14
    MARGIN_BOTTOM = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: dict[int, int] = {}
        # (year, x_px, y_px) for every year in range, rebuilt each paint.
        self._year_points: list[tuple[int, float, float]] = []
        self._hovered_year: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(_MIN_HEIGHT)
        self._apply_theme_palette()

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def set_data(self, counts: dict | None):
        """`counts` maps year -> count (year may be int or str). Re-reads the
        active theme in case it changed since construction."""
        self._apply_theme_palette()
        cleaned: dict[int, int] = {}
        for year, count in (counts or {}).items():
            try:
                cleaned[int(year)] = int(count)
            except (TypeError, ValueError):
                continue
        self._counts = cleaned
        self._hovered_year = None
        self.update()

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _tick_step(span: int, target_ticks: int = 7) -> int:
        """A 'nice' year interval so the x-axis gets roughly `target_ticks`
        labels (1, 2, 5, 10, 20, 25, 50, 100 ...)."""
        raw = max(span / target_ticks, 1)
        for step in (1, 2, 5, 10, 20, 25, 50, 100, 200):
            if step >= raw:
                return step
        return 500

    # ------------------------------------------------------------------ #
    #  Paint                                                              #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        surface, line_color, fill_color, text_color, muted_color, grid_color = self._palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), surface)

        self._year_points = []

        if not self._counts:
            painter.setPen(muted_color)
            painter.setFont(QFont("Cambria", 10))
            painter.drawText(self.rect(), Qt.AlignCenter, "No release years yet")
            painter.end()
            return

        year_min = min(self._counts)
        year_max = max(self._counts)
        if year_min == year_max:
            year_min -= 1
            year_max += 1
        years = list(range(year_min, year_max + 1))
        counts = [self._counts.get(y, 0) for y in years]
        max_count = max(counts) or 1

        w, h = self.width(), self.height()
        plot_left = self.MARGIN_LEFT
        plot_right = w - self.MARGIN_RIGHT
        plot_top = self.MARGIN_TOP
        plot_bottom = h - self.MARGIN_BOTTOM
        plot_w = max(plot_right - plot_left, 1)
        plot_h = max(plot_bottom - plot_top, 1)
        span = year_max - year_min

        def x_of(year: int) -> float:
            return plot_left + (year - year_min) / span * plot_w

        def y_of(count: int) -> float:
            return plot_bottom - (count / max_count) * plot_h

        # Gridlines + y-axis labels at 0 / 50 / 100 % of the peak year.
        painter.setFont(QFont("Cambria", 8))
        for frac in (0.0, 0.5, 1.0):
            y = plot_bottom - frac * plot_h
            painter.setPen(QPen(grid_color, 1))
            painter.drawLine(int(plot_left), int(y), int(plot_right), int(y))
            painter.setPen(text_color)
            painter.drawText(
                QRect(0, int(y) - 8, self.MARGIN_LEFT - 6, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                str(round(max_count * frac)),
            )

        # Filled area under the profile.
        line_points = [(x_of(y), y_of(c)) for y, c in zip(years, counts, strict=True)]
        area = QPainterPath()
        area.moveTo(plot_left, plot_bottom)
        for px, py in line_points:
            area.lineTo(px, py)
        area.lineTo(plot_right, plot_bottom)
        area.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill_color)
        painter.drawPath(area)

        # Profile line on top.
        painter.setPen(QPen(line_color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(QPolygonF([QPoint(int(px), int(py)) for px, py in line_points]))

        self._year_points = [
            (year, px, py) for year, (px, py) in zip(years, line_points, strict=True)
        ]

        # X-axis tick labels on 'nice' year boundaries.
        step = self._tick_step(span)
        painter.setPen(muted_color)
        painter.setFont(QFont("Cambria", 8))
        first_tick = ((year_min + step - 1) // step) * step
        last_tick_x = plot_left
        for year in range(first_tick, year_max + 1, step):
            tx = x_of(year)
            last_tick_x = tx
            painter.drawText(
                QRect(int(tx) - 22, int(plot_bottom) + 4, 44, 16), Qt.AlignCenter, str(year)
            )
        # Anchor the final year too, but only when it won't crowd the last
        # regular tick (otherwise "2020" and "2026" overprint).
        if plot_right - last_tick_x > 36:
            painter.drawText(
                QRect(int(plot_right) - 44, int(plot_bottom) + 4, 44, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                str(year_max),
            )

        # Hover marker + dot.
        if self._hovered_year is not None and self._hovered_year in self._counts:
            hx = x_of(self._hovered_year)
            hy = y_of(self._counts[self._hovered_year])
            painter.setPen(QPen(muted_color, 1, Qt.DashLine))
            painter.drawLine(int(hx), int(plot_top), int(hx), int(plot_bottom))
            painter.setPen(Qt.NoPen)
            painter.setBrush(line_color)
            painter.drawEllipse(QRectF(hx - 3, hy - 3, 6, 6))

        painter.end()

    # ------------------------------------------------------------------ #
    #  Hover / tooltip                                                    #
    # ------------------------------------------------------------------ #

    def _year_at(self, pos: QPoint) -> int | None:
        if not self._year_points:
            return None
        nearest = min(self._year_points, key=lambda yp: abs(yp[1] - pos.x()))
        return nearest[0]

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        year = self._year_at(pos)
        if year != self._hovered_year:
            self._hovered_year = year
            self.update()
        if year is not None:
            count = self._counts.get(year, 0)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{year} — {count} album{'s' if count != 1 else ''}",
                self,
            )
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):
        if self._hovered_year is not None:
            self._hovered_year = None
            self.update()
        QToolTip.hideText()

    def sizeHint(self):
        return QSize(560, 200)
