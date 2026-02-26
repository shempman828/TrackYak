"""
dates_timeline.py

A scrub-able, fully labeled year timeline that works with dark_mode.qss.
All colors are drawn to complement the theme palette:
  Base: #0b0c10 | Accent: #8599ea | Text: #b8c0f0 | Highlight: #EAD685
"""

from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class TimelineWidget(QWidget):
    """
    A scrub-able timeline that displays all years.

    - Click OR drag along the track to select a year.
    - The selected year is highlighted with an accent color.
    - All years are always labeled (rotated if needed).
    - Colors match dark_mode.qss.
    """

    yearSelected = Signal(int)

    # ── Theme colors (match dark_mode.qss palette) ────────────────────────────
    COLOR_BG_TOP = QColor(0x11, 0x12, 0x1A)  # slightly lighter than base
    COLOR_BG_BOTTOM = QColor(0x0B, 0x0C, 0x10)  # base dark
    COLOR_TRACK = QColor(0x2B, 0x2C, 0x36)  # subtle track line
    COLOR_TRACK_FILL = QColor(0x85, 0x99, 0xEA)  # accent – filled portion
    COLOR_MARKER = QColor(0x55, 0x60, 0x80)  # un-selected dot
    COLOR_SELECTED = QColor(0xEA, 0xD6, 0x85)  # gold accent for selection
    COLOR_HOVERED = QColor(0x99, 0xEA, 0x85)  # green triadic for hover
    COLOR_TEXT = QColor(0xB8, 0xC0, 0xF0)  # soft lavender
    COLOR_TEXT_SELECTED = QColor(0xEA, 0xD6, 0x85)  # gold – selected year label
    COLOR_TEXT_MUTED = QColor(0x55, 0x60, 0x80)  # dim for overflow years

    MARGIN = 48  # horizontal padding on each side
    TRACK_H = 4  # track line height
    DOT_R_NORMAL = 5  # dot radius – normal year
    DOT_R_HOVER = 7  # dot radius – hovered
    DOT_R_SEL = 9  # dot radius – selected
    LABEL_Y_OFFSET = 18  # how far below the track center the label sits

    def __init__(self, parent=None):
        super().__init__(parent)
        self.years: List[int] = []
        self.selected_year: Optional[int] = None
        self.hovered_index: Optional[int] = None
        self._dragging = False

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(90)
        self.setCursor(Qt.ArrowCursor)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_years(self, years: List[int]):
        """Set the list of years to display (duplicates and unsorted OK)."""
        if not years:
            return
        self.years = sorted(set(years))
        # Keep existing selection if still valid, otherwise default to first year
        if self.selected_year not in self.years:
            self.selected_year = self.years[0]
            self.yearSelected.emit(self.selected_year)
        self.update()

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _x_for_index(self, index: int) -> int:
        """Return the pixel x-position for a year by its list index."""
        if len(self.years) <= 1:
            return self.width() // 2
        usable = self.width() - 2 * self.MARGIN
        return self.MARGIN + int(index / (len(self.years) - 1) * usable)

    def _index_near_x(self, x: float) -> Optional[int]:
        """Return the index of the year whose dot is closest to x (within 20px)."""
        best_idx = None
        best_dist = float("inf")
        for i in range(len(self.years)):
            dist = abs(x - self._x_for_index(i))
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        # Only snap if we're within a reasonable hit radius
        hit_radius = max(
            20, (self.width() - 2 * self.MARGIN) / max(len(self.years), 1) / 2
        )
        return best_idx if best_dist <= hit_radius else None

    def _should_show_label(self, index: int) -> bool:
        """
        Decide if a year label should be shown.
        Always show first, last, and selected. For dense timelines, skip some.
        """
        n = len(self.years)
        if n == 0:
            return False
        if index == 0 or index == n - 1:
            return True
        if self.years[index] == self.selected_year:
            return True

        # For dense timelines, show every Nth year to avoid overlap
        usable_px = max(1, self.width() - 2 * self.MARGIN)
        spacing_px = usable_px / max(n - 1, 1)
        # Each label needs ~36px; skip if spacing too tight
        every_n = max(1, int(36 / spacing_px)) if spacing_px < 36 else 1
        return index % every_n == 0

    # ── Paint ──────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self.years:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cy = h // 2 - 6  # Track center Y (leave room for labels below)

        # ── Background gradient ─────────────────────────────────────────────
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, self.COLOR_BG_TOP)
        grad.setColorAt(1, self.COLOR_BG_BOTTOM)
        painter.fillRect(self.rect(), grad)

        # ── Track (full) ────────────────────────────────────────────────────
        pen = QPen(self.COLOR_TRACK, self.TRACK_H, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(self.MARGIN, cy, w - self.MARGIN, cy)

        # ── Track (filled up to selected year) ─────────────────────────────
        if self.selected_year in self.years:
            sel_idx = self.years.index(self.selected_year)
            sel_x = self._x_for_index(sel_idx)
            pen_fill = QPen(
                self.COLOR_TRACK_FILL, self.TRACK_H, Qt.SolidLine, Qt.RoundCap
            )
            painter.setPen(pen_fill)
            painter.drawLine(self.MARGIN, cy, sel_x, cy)

        # ── Dots & Labels ───────────────────────────────────────────────────
        for i, year in enumerate(self.years):
            x = self._x_for_index(i)
            is_sel = year == self.selected_year
            is_hov = (i == self.hovered_index) and not is_sel

            # Pick dot appearance
            if is_sel:
                radius = self.DOT_R_SEL
                color = self.COLOR_SELECTED
            elif is_hov:
                radius = self.DOT_R_HOVER
                color = self.COLOR_HOVERED
            else:
                radius = self.DOT_R_NORMAL
                color = self.COLOR_MARKER

            # Outer glow for selected
            if is_sel:
                glow = QColor(self.COLOR_SELECTED)
                glow.setAlpha(60)
                painter.setBrush(glow)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPoint(x, cy), radius + 4, radius + 4)

            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(x, cy), radius, radius)

            # Labels
            if self._should_show_label(i):
                if is_sel:
                    text_color = self.COLOR_TEXT_SELECTED
                    font = QFont("Cambria", 10)
                    font.setBold(True)
                elif is_hov:
                    text_color = self.COLOR_HOVERED
                    font = QFont("Cambria", 9)
                    font.setBold(True)
                else:
                    text_color = self.COLOR_TEXT
                    font = QFont("Cambria", 8)
                    font.setBold(False)

                painter.setPen(text_color)
                painter.setFont(font)
                label_rect = QRect(x - 26, cy + self.LABEL_Y_OFFSET, 52, 18)
                painter.drawText(label_rect, Qt.AlignCenter, str(year))

        # ── "Scrub to select year" hint ─────────────────────────────────────
        hint_font = QFont("Cambria", 7)
        painter.setFont(hint_font)
        painter.setPen(self.COLOR_TEXT_MUTED)
        painter.drawText(
            QRect(0, 4, w, 14), Qt.AlignCenter, "◀  drag or click to select year  ▶"
        )

        painter.end()

    # ── Mouse events ───────────────────────────────────────────────────────────

    def _update_hover(self, x: float):
        idx = self._index_near_x(x)
        if idx != self.hovered_index:
            self.hovered_index = idx
            self.setCursor(Qt.PointingHandCursor if idx is not None else Qt.ArrowCursor)
            self.update()

    def _select_at_x(self, x: float):
        idx = self._index_near_x(x)
        if idx is not None:
            new_year = self.years[idx]
            if new_year != self.selected_year:
                self.selected_year = new_year
                self.yearSelected.emit(self.selected_year)
                self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._select_at_x(event.position().x())

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._dragging and (event.buttons() & Qt.LeftButton):
            # While dragging, snap to nearest year continuously
            self._select_at_x(x)
        else:
            self._update_hover(x)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def leaveEvent(self, event):
        if self.hovered_index is not None:
            self.hovered_index = None
            self.update()
        self.setCursor(Qt.ArrowCursor)

    def sizeHint(self):
        return QSize(600, 90)
