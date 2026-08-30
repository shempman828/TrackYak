"""
rating_distribution_chart.py

A custom-painted bar chart showing how tracks are spread across the
0.5 - 10 user rating scale, replacing the old 21-row plain-text list.

Colors are theme-aware (matches the app's dark/light/colorful/accessibility
QSS palettes) using the same lookup pattern as InfluenceGraphView.
"""

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip

from src.core.logger_config import logger
from src.statistics.charts.theme_palette import ThemedChartWidget

RATING_MIN = 0.5
RATING_MAX = 10.0
RATING_STEP = 0.5


class RatingDistributionChart(ThemedChartWidget):
    """Vertical bar chart: one bar per 0.5-point rating bucket."""

    # (surface, bar, bar_peak, text, text_muted, gridline)
    _THEME_PALETTE = {
        "dark_mode": (
            QColor("#11121a"),
            QColor("#8599ea"),
            QColor("#EAD685"),
            QColor("#b8c0f0"),
            QColor("#7a82a8"),
            QColor(133, 153, 234, 40),
        ),
        "light_mode": (
            QColor("#ffffff"),
            QColor("#8599ea"),
            QColor("#c9a227"),
            QColor("#2b2c36"),
            QColor("#6b6f80"),
            QColor(43, 44, 54, 30),
        ),
        "colorful_mode": (
            QColor("#ffffff"),
            QColor("#8599ea"),
            QColor("#ea8599"),
            QColor("#1c1c21"),
            QColor("#777777"),
            QColor(133, 153, 234, 35),
        ),
        "accessibility_mode": (
            QColor("#ffffff"),
            QColor("#6477d4"),
            QColor("#a8580c"),
            QColor("#1c1c21"),
            QColor("#4a4a4a"),
            QColor(28, 28, 33, 45),
        ),
    }

    MARGIN_LEFT = 44
    MARGIN_RIGHT = 12
    MARGIN_TOP = 22
    MARGIN_BOTTOM = 26
    BAR_MAX_WIDTH = 24
    BAR_RADIUS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._distribution: dict[float, int] = {}
        self._ratings = [
            round(RATING_MIN + i * RATING_STEP, 1)
            for i in range(int((RATING_MAX - RATING_MIN) / RATING_STEP) + 1)
        ]
        self._hovered_index: int | None = None
        self._bar_rects: list = []

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(240)
        self._apply_theme_palette()

    # ------------------------------------------------------------------ #
    #  Theming                                                            #
    # ------------------------------------------------------------------ #

    def _apply_theme_palette(self):
        super()._apply_theme_palette()
        (
            self._color_surface,
            self._color_bar,
            self._color_peak,
            self._color_text,
            self._color_muted,
            self._color_grid,
        ) = self._palette

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def set_data(self, distribution: dict[float, int]):
        """Set rating -> track_count. Re-reads the active theme too, in case
        it changed since the chart was created."""
        self._apply_theme_palette()
        self._distribution = distribution or {}
        self._hovered_index = None
        logger.debug(f"Rating distribution chart updated with {len(self._distribution)} buckets")
        self.update()

    # ------------------------------------------------------------------ #
    #  Paint                                                              #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self._color_surface)

        counts = [self._distribution.get(r, 0) for r in self._ratings]
        total = sum(counts)

        if total == 0:
            painter.setPen(self._color_muted)
            painter.setFont(QFont("Cambria", 10))
            painter.drawText(self.rect(), Qt.AlignCenter, "No ratings yet")
            painter.end()
            return

        max_count = max(counts)
        w, h = self.width(), self.height()
        plot_left = self.MARGIN_LEFT
        plot_right = w - self.MARGIN_RIGHT
        plot_top = self.MARGIN_TOP
        plot_bottom = h - self.MARGIN_BOTTOM
        plot_h = max(plot_bottom - plot_top, 1)
        plot_w = max(plot_right - plot_left, 1)

        n = len(self._ratings)
        slot_w = plot_w / n

        # Gridlines + y-axis labels at 0%, 50%, 100% of the peak bucket
        painter.setFont(QFont("Cambria", 8))
        for frac in (0.0, 0.5, 1.0):
            y = plot_bottom - frac * plot_h
            pen = QPen(self._color_grid, 1)
            painter.setPen(pen)
            painter.drawLine(int(plot_left), int(y), int(plot_right), int(y))

            painter.setPen(self._color_muted)
            label = str(round(max_count * frac))
            label_rect = QRect(0, int(y) - 8, self.MARGIN_LEFT - 6, 16)
            painter.drawText(label_rect, Qt.AlignRight | Qt.AlignVCenter, label)

        # Bars
        self._bar_rects = []
        peak_index = counts.index(max_count) if max_count > 0 else None
        bar_w = min(self.BAR_MAX_WIDTH, slot_w - 2)

        for i, (rating, count) in enumerate(zip(self._ratings, counts)):
            slot_center = plot_left + (i + 0.5) * slot_w
            bar_h = (count / max_count) * plot_h if max_count else 0
            bar_rect = QRectF(slot_center - bar_w / 2, plot_bottom - bar_h, bar_w, bar_h)
            self._bar_rects.append(bar_rect)

            if count == 0:
                continue

            is_peak = i == peak_index
            is_hover = i == self._hovered_index
            color = QColor(self._color_peak if is_peak else self._color_bar)
            if is_hover:
                color = color.lighter(120)

            # Rounded top, square baseline: draw a fully-rounded rect, then
            # re-fill the bottom half as a plain rect to square it off.
            r = min(self.BAR_RADIUS, bar_rect.width() / 2, bar_rect.height())
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(bar_rect, r, r)
            if bar_rect.height() > r:
                painter.drawRect(
                    QRectF(bar_rect.x(), bar_rect.y() + r, bar_rect.width(), bar_rect.height() - r)
                )

            # Direct label on the tallest bar only
            if is_peak:
                painter.setPen(self._color_text)
                painter.setFont(QFont("Cambria", 8, QFont.Bold))
                label_rect = QRect(
                    int(bar_rect.x() - 20), int(bar_rect.y() - 16), int(bar_w + 40), 14
                )
                painter.drawText(label_rect, Qt.AlignCenter, str(count))

            # X-axis label on whole-star ticks only
            if rating % 1 == 0:
                painter.setPen(self._color_muted)
                painter.setFont(QFont("Cambria", 8))
                tick_rect = QRect(int(slot_center - 16), int(plot_bottom + 6), 32, 16)
                painter.drawText(tick_rect, Qt.AlignCenter, f"{int(rating)}")

        # Baseline
        painter.setPen(QPen(self._color_grid, 1))
        painter.drawLine(int(plot_left), int(plot_bottom), int(plot_right), int(plot_bottom))

        painter.end()

    # ------------------------------------------------------------------ #
    #  Hover / tooltip                                                    #
    # ------------------------------------------------------------------ #

    def _index_at(self, pos: QPoint) -> int | None:
        for i, rect in enumerate(self._bar_rects):
            hit = QRectF(
                rect.x() - 2, self.MARGIN_TOP, rect.width() + 4, self.height() - self.MARGIN_TOP
            )
            if hit.contains(pos):
                return i
        return None

    def mouseMoveEvent(self, event):
        idx = self._index_at(event.position().toPoint())
        if idx != self._hovered_index:
            self._hovered_index = idx
            self.update()

        if idx is not None:
            rating = self._ratings[idx]
            count = self._distribution.get(rating, 0)
            total = sum(self._distribution.values())
            pct = (count / total * 100) if total else 0
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{rating:g}/10 — {count} track{'s' if count != 1 else ''} ({pct:.1f}%)",
                self,
            )
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):
        if self._hovered_index is not None:
            self._hovered_index = None
            self.update()
        QToolTip.hideText()

    def sizeHint(self):
        return QSize(700, 260)
