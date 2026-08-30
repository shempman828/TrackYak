"""
stat_tile.py

StatTileWidget: a painted "card" (label + big value + optional subtitle)
for single-value headline stats (most influential artist, most niche
genre, oldest living artist, ...), replacing the _HIGHLIGHT_COLOR
HTML-span-in-QLabel pattern used throughout the old dialog.
"""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSizePolicy

from src.statistics.charts.theme_palette import ThemedChartWidget


class StatTileWidget(ThemedChartWidget):
    """Card showing one label + one headline value + an optional subtitle."""

    # (surface, border, label_text, value_text, subtitle_text)
    _THEME_PALETTE = {
        "dark_mode": (
            QColor("#181a24"),
            QColor(133, 153, 234, 60),
            QColor("#7a82a8"),
            QColor("#EAD685"),
            QColor("#b8c0f0"),
        ),
        "light_mode": (
            QColor("#f5f6fa"),
            QColor(43, 44, 54, 40),
            QColor("#6b6f80"),
            QColor("#c9a227"),
            QColor("#2b2c36"),
        ),
        "colorful_mode": (
            QColor("#f5f6fa"),
            QColor(133, 153, 234, 50),
            QColor("#777777"),
            QColor("#ea8599"),
            QColor("#1c1c21"),
        ),
        "accessibility_mode": (
            QColor("#f5f6fa"),
            QColor(28, 28, 33, 60),
            QColor("#4a4a4a"),
            QColor("#a8580c"),
            QColor("#1c1c21"),
        ),
    }

    def __init__(self, label: str = "", parent=None):
        super().__init__(parent)
        self._label = label
        self._value = "N/A"
        self._subtitle = ""
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(76)
        self._apply_theme_palette()

    def set_data(self, value, subtitle: str = ""):
        """Set the headline value (and optional subtitle). Re-reads the
        active theme too, in case it changed since the tile was created."""
        self._apply_theme_palette()
        self._value = str(value) if value not in (None, "") else "N/A"
        self._subtitle = subtitle
        self.update()

    def paintEvent(self, event):
        (surface, border, label_color, value_color, subtitle_color) = self._palette

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(border)
        painter.setBrush(surface)
        painter.drawRoundedRect(rect, 8, 8)

        padding = 12
        content_rect = rect.adjusted(padding, 8, -padding, -8)

        painter.setPen(label_color)
        painter.setFont(QFont("Cambria", 9))
        label_rect = QRect(
            content_rect.left(), content_rect.top(), content_rect.width(), 16
        )
        painter.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, self._label)

        painter.setPen(value_color)
        painter.setFont(QFont("Cambria", 13, QFont.Bold))
        value_rect = QRect(
            content_rect.left(),
            content_rect.top() + 18,
            content_rect.width(),
            22,
        )
        elided = painter.fontMetrics().elidedText(
            self._value, Qt.ElideRight, value_rect.width()
        )
        painter.drawText(value_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)

        if self._subtitle:
            painter.setPen(subtitle_color)
            painter.setFont(QFont("Cambria", 8))
            subtitle_rect = QRect(
                content_rect.left(),
                content_rect.top() + 42,
                content_rect.width(),
                16,
            )
            elided_sub = painter.fontMetrics().elidedText(
                self._subtitle, Qt.ElideRight, subtitle_rect.width()
            )
            painter.drawText(
                subtitle_rect, Qt.AlignLeft | Qt.AlignVCenter, elided_sub
            )

        painter.end()

    def sizeHint(self):
        return QSize(220, 76)
