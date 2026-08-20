"""
leaderboard_list.py

LeaderboardListWidget: ranked rows with a proportional bar-in-background,
replacing the ad hoc QLabel-per-row loops used for every "top N" list in
the old dialog (top artists, top genres, top moods, and every new
leaderboard added across the statistics expansion).
"""

from typing import List, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy, QToolTip

from src.statistics.charts.theme_palette import ThemedChartWidget

ROW_HEIGHT = 26


class LeaderboardListWidget(ThemedChartWidget):
    """Vertical list of ranked (name, value) rows, each with a bar sized
    proportionally to the row's value relative to the top row."""

    # (surface, bar, text, text_muted, rank_text)
    _THEME_PALETTE = {
        "dark_mode": (
            QColor("#11121a"),
            QColor(133, 153, 234, 90),
            QColor("#b8c0f0"),
            QColor("#7a82a8"),
            QColor("#EAD685"),
        ),
        "light_mode": (
            QColor("#ffffff"),
            QColor(133, 153, 234, 70),
            QColor("#2b2c36"),
            QColor("#6b6f80"),
            QColor("#c9a227"),
        ),
        "colorful_mode": (
            QColor("#ffffff"),
            QColor(133, 153, 234, 60),
            QColor("#1c1c21"),
            QColor("#777777"),
            QColor("#ea8599"),
        ),
        "accessibility_mode": (
            QColor("#ffffff"),
            QColor(100, 119, 212, 80),
            QColor("#1c1c21"),
            QColor("#4a4a4a"),
            QColor("#a8580c"),
        ),
    }

    def __init__(self, value_suffix: str = "", parent=None):
        super().__init__(parent)
        # rows: list of (name, value, secondary_label)
        self._rows: List[Tuple[str, float, Optional[str]]] = []
        self._value_suffix = value_suffix
        self._hovered_index: Optional[int] = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._apply_theme_palette()

    def set_data(self, rows: List[Tuple[str, float, Optional[str]]]):
        """rows: list of (name, value, secondary_label_or_None)."""
        self._apply_theme_palette()
        self._rows = rows or []
        self._hovered_index = None
        self.setMinimumHeight(max(ROW_HEIGHT, len(self._rows) * ROW_HEIGHT + 8))
        self.update()

    def paintEvent(self, event):
        surface, bar_color, text_color, muted_color, rank_color = self._palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), surface)

        if not self._rows:
            painter.setPen(muted_color)
            painter.setFont(QFont("Cambria", 10))
            painter.drawText(self.rect(), Qt.AlignCenter, "No data yet")
            painter.end()
            return

        max_value = max((v for _n, v, _s in self._rows), default=0) or 1
        w = self.width()

        for i, (name, value, secondary) in enumerate(self._rows):
            y = 4 + i * ROW_HEIGHT
            row_rect = QRect(0, y, w, ROW_HEIGHT - 2)

            if i == self._hovered_index:
                painter.fillRect(row_rect, QColor(bar_color.red(), bar_color.green(), bar_color.blue(), 20))

            bar_w = (value / max_value) * (w - 16) if max_value else 0
            bar_rect = QRectF(8, y + 3, bar_w, ROW_HEIGHT - 8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bar_color)
            painter.drawRoundedRect(bar_rect, 3, 3)

            painter.setPen(rank_color)
            painter.setFont(QFont("Cambria", 8, QFont.Bold))
            rank_rect = QRect(4, y, 22, ROW_HEIGHT - 2)
            painter.drawText(rank_rect, Qt.AlignLeft | Qt.AlignVCenter, f"{i + 1}.")

            painter.setPen(text_color)
            painter.setFont(QFont("Cambria", 9))
            label = name if not secondary else f"{name} — {secondary}"
            text_rect = QRect(28, y, w - 100, ROW_HEIGHT - 2)
            elided = painter.fontMetrics().elidedText(
                label, Qt.ElideRight, text_rect.width()
            )
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)

            painter.setPen(muted_color)
            value_rect = QRect(w - 72, y, 68, ROW_HEIGHT - 2)
            value_str = f"{value:g}{self._value_suffix}"
            painter.drawText(value_rect, Qt.AlignRight | Qt.AlignVCenter, value_str)

        painter.end()

    def _index_at(self, y: int) -> Optional[int]:
        if not self._rows:
            return None
        idx = (y - 4) // ROW_HEIGHT
        if 0 <= idx < len(self._rows):
            return int(idx)
        return None

    def mouseMoveEvent(self, event):
        idx = self._index_at(int(event.position().y()))
        if idx != self._hovered_index:
            self._hovered_index = idx
            self.update()
        if idx is not None:
            name, value, secondary = self._rows[idx]
            label = name if not secondary else f"{name} — {secondary}"
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{label}: {value:g}{self._value_suffix}",
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
        return QSize(400, max(ROW_HEIGHT, len(self._rows) * ROW_HEIGHT + 8))
