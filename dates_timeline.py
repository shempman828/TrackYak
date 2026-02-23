from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class TimelineWidget(QWidget):
    """A modern, purely custom-drawn timeline for year selection."""

    yearSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.years: List[int] = []
        self.selected_year: Optional[int] = None
        self.hovered_index: Optional[int] = None

        # Modern Color Palette
        self.colors = {
            "track": QColor(60, 60, 60),
            "marker": QColor(100, 100, 100),
            "highlight": QColor(0, 120, 215),  # Modern Blue
            "text": QColor(180, 180, 180),
            "selected_text": QColor(255, 255, 255),
            "background": QColor(30, 30, 30),
        }

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(80)

    def set_years(self, years: List[int]):
        """Initializes the timeline with a sorted list of years."""
        if not years:
            return
        self.years = sorted(list(set(years)))
        self.selected_year = self.years[0]
        self.update()

    def _get_year_pos(self, index: int) -> int:
        """Calculates the x-coordinate for a given year index."""
        margin = 40
        width = self.width() - (2 * margin)
        if len(self.years) <= 1:
            return self.width() // 2
        return margin + int((index / (len(self.years) - 1)) * width)

    def paintEvent(self, event):
        if not self.years:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw Background
        painter.fillRect(self.rect(), self.colors["background"])

        cy = self.height() // 2  # Center Y
        margin = 40

        # 1. Draw Main Track
        pen = QPen(self.colors["track"], 4, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(margin, cy, self.width() - margin, cy)

        # 2. Draw Year Nodes and Labels
        for i, year in enumerate(self.years):
            x = self._get_year_pos(i)
            is_selected = year == self.selected_year
            is_hovered = i == self.hovered_index

            # Determine Node Style
            radius = 6
            color = self.colors["marker"]

            if is_selected:
                radius = 8
                color = self.colors["highlight"]
            elif is_hovered:
                radius = 7
                color = self.colors["selected_text"]

            # Draw Node
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(x, cy), radius, radius)

            # Draw Text for specific conditions (Start, End, Selected)
            if is_selected or i == 0 or i == len(self.years) - 1:
                painter.setPen(
                    self.colors["selected_text"] if is_selected else self.colors["text"]
                )
                font = QFont("Segoe UI", 9)
                font.setBold(is_selected)
                painter.setFont(font)

                text_rect = QRect(x - 25, cy + 15, 50, 20)
                painter.drawText(text_rect, Qt.AlignCenter, str(year))

    def mouseMoveEvent(self, event):
        """Detects hovering over years to provide visual feedback."""
        pos = event.position().x()
        found_hover = None

        for i in range(len(self.years)):
            if abs(pos - self._get_year_pos(i)) < 15:  # 15px hit box
                found_hover = i
                break

        if found_hover != self.hovered_index:
            self.hovered_index = found_hover
            self.setCursor(
                Qt.PointingHandCursor if found_hover is not None else Qt.ArrowCursor
            )
            self.update()

    def mousePressEvent(self, event):
        """Selects the year when clicked."""
        if self.hovered_index is not None:
            self.selected_year = self.years[self.hovered_index]
            self.yearSelected.emit(self.selected_year)
            self.update()

    def sizeHint(self):
        return QSize(400, 100)
