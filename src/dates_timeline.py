"""
dates_timeline.py

A two-level year timeline that works with dark_mode.qss.
  - Default view: decades (e.g. 1990s, 2000s, 2010s)
  - Click a decade: expands to show individual years in that decade
  - Click a year: selects it, emits yearSelected, collapses back to decades
  - The decade containing the selected year stays highlighted (gold dot)
  - No drag-to-scrub — click only

All colors complement the dark_mode.qss theme palette:
  Base: #0b0c10 | Accent: #8599ea | Text: #b8c0f0 | Highlight: #EAD685
"""

from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class TimelineWidget(QWidget):
    """
    A two-level timeline: decades → years.

    External API is identical to the old widget:
      - set_years(years)       — populate the timeline
      - yearSelected signal    — emitted with the int year when user picks one
    """

    yearSelected = Signal(int)

    # ── Theme colors ──────────────────────────────────────────────────────────
    COLOR_BG_TOP = QColor(0x11, 0x12, 0x1A)
    COLOR_BG_BOTTOM = QColor(0x0B, 0x0C, 0x10)
    COLOR_TRACK = QColor(0x2B, 0x2C, 0x36)
    COLOR_TRACK_FILL = QColor(0x85, 0x99, 0xEA)
    COLOR_MARKER = QColor(0x55, 0x60, 0x80)
    COLOR_SELECTED = QColor(0xEA, 0xD6, 0x85)  # gold – selected decade / year
    COLOR_HOVERED = QColor(0x99, 0xEA, 0x85)  # green – hovered item
    COLOR_TEXT = QColor(0xB8, 0xC0, 0xF0)  # soft lavender
    COLOR_TEXT_SELECTED = QColor(0xEA, 0xD6, 0x85)  # gold label
    COLOR_TEXT_MUTED = QColor(0x55, 0x60, 0x80)  # dim hint text

    MARGIN = 48  # horizontal padding each side
    TRACK_H = 4  # track line height
    DOT_R_NORMAL = 5  # dot radius – normal
    DOT_R_HOVER = 7  # dot radius – hovered
    DOT_R_SEL = 9  # dot radius – selected / active decade
    LABEL_Y_OFFSET = 18  # pixels below track centre for labels

    def __init__(self, parent=None):
        super().__init__(parent)

        # Raw data
        self.years: List[int] = []  # all unique years, sorted
        self.selected_year: Optional[int] = None

        # Derived data (rebuilt whenever self.years changes)
        self._decades: List[int] = []  # e.g. [1990, 2000, 2010]
        self._decade_years: dict = {}  # {1990: [1990,1991,...,1999], ...}

        # UI state
        self._expanded_decade: Optional[int] = None  # None → decade view
        self._hovered_index: Optional[int] = None  # index into current items

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
        self._build_decades()

        # Keep selection valid
        if self.selected_year not in self.years:
            self.selected_year = self.years[0]
            self.yearSelected.emit(self.selected_year)

        self._expanded_decade = None
        self._hovered_index = None
        self.update()

    # ── Decade helpers ─────────────────────────────────────────────────────────

    def _build_decades(self):
        """Group years into decades. A decade key is the floor decade (e.g. 1990)."""
        self._decade_years = {}
        for y in self.years:
            decade = (y // 10) * 10
            self._decade_years.setdefault(decade, []).append(y)
        self._decades = sorted(self._decade_years.keys())

    def _decade_for_year(self, year: int) -> int:
        return (year // 10) * 10

    # ── Current display items ──────────────────────────────────────────────────

    def _current_items(self) -> List:
        """
        Return the list of items currently drawn on the track.
        In decade view: list of decade ints (e.g. [1990, 2000]).
        In expanded view: list of year ints for the open decade.
        """
        if self._expanded_decade is None:
            return self._decades
        return self._decade_years.get(self._expanded_decade, [])

    # ── Geometry helpers ───────────────────────────────────────────────────────

    def _x_for_index(self, index: int, total: int) -> int:
        if total <= 1:
            return self.width() // 2
        usable = self.width() - 2 * self.MARGIN
        return self.MARGIN + int(index / (total - 1) * usable)

    def _index_near_x(self, x: float) -> Optional[int]:
        items = self._current_items()
        n = len(items)
        if n == 0:
            return None
        best_idx, best_dist = None, float("inf")
        for i in range(n):
            dist = abs(x - self._x_for_index(i, n))
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        hit_radius = max(20, (self.width() - 2 * self.MARGIN) / max(n, 1) / 2)
        return best_idx if best_dist <= hit_radius else None

    # ── Paint ──────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not self.years:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cy = h // 2 - 6  # track centre Y

        # Background gradient
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, self.COLOR_BG_TOP)
        grad.setColorAt(1, self.COLOR_BG_BOTTOM)
        painter.fillRect(self.rect(), grad)

        items = self._current_items()
        n = len(items)
        in_year_view = self._expanded_decade is not None

        # Track (full)
        pen = QPen(self.COLOR_TRACK, self.TRACK_H, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(self.MARGIN, cy, w - self.MARGIN, cy)

        # Track fill up to selected item
        selected_item = (
            self.selected_year
            if in_year_view
            else self._decade_for_year(self.selected_year)
            if self.selected_year
            else None
        )
        if selected_item in items:
            sel_idx = items.index(selected_item)
            sel_x = self._x_for_index(sel_idx, n)
            pen_fill = QPen(
                self.COLOR_TRACK_FILL, self.TRACK_H, Qt.SolidLine, Qt.RoundCap
            )
            painter.setPen(pen_fill)
            painter.drawLine(self.MARGIN, cy, sel_x, cy)

        # Dots and labels
        for i, item in enumerate(items):
            x = self._x_for_index(i, n)

            # Determine state
            if in_year_view:
                is_sel = item == self.selected_year
            else:
                # Decade view: highlight the decade that owns the selected year
                is_sel = (
                    (item == self._decade_for_year(self.selected_year))
                    if self.selected_year
                    else False
                )

            is_hov = (i == self._hovered_index) and not is_sel

            # Dot appearance
            if is_sel:
                radius, color = self.DOT_R_SEL, self.COLOR_SELECTED
            elif is_hov:
                radius, color = self.DOT_R_HOVER, self.COLOR_HOVERED
            else:
                radius, color = self.DOT_R_NORMAL, self.COLOR_MARKER

            # Glow behind selected dot
            if is_sel:
                glow = QColor(self.COLOR_SELECTED)
                glow.setAlpha(60)
                painter.setBrush(glow)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPoint(x, cy), radius + 4, radius + 4)

            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(x, cy), radius, radius)

            # Label
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

            if in_year_view:
                label = str(item)
            else:
                label = f"{item}s"  # e.g. "1990s"

            label_rect = QRect(x - 26, cy + self.LABEL_Y_OFFSET, 52, 18)
            painter.drawText(label_rect, Qt.AlignCenter, label)

        # Hint text at top
        if in_year_view:
            hint = f"◀  {self._expanded_decade}s — click year to select  ▶"
        else:
            hint = "◀  click a decade to explore years  ▶"

        hint_font = QFont("Cambria", 7)
        painter.setFont(hint_font)
        painter.setPen(self.COLOR_TEXT_MUTED)
        painter.drawText(QRect(0, 4, w, 14), Qt.AlignCenter, hint)

        # In year view: show a small "back" indicator on the left
        if in_year_view:
            back_font = QFont("Cambria", 8)
            painter.setFont(back_font)
            painter.setPen(self.COLOR_TRACK_FILL)
            painter.drawText(QRect(4, cy - 10, 40, 20), Qt.AlignCenter, "← back")

        painter.end()

    # ── Mouse events ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        x = event.position().x()
        y = event.position().y()

        # "back" hit area (left 44px, centre band) — only in year view
        if self._expanded_decade is not None and x < 44:
            self._collapse_to_decades()
            return

        idx = self._index_near_x(x)
        if idx is None:
            return

        items = self._current_items()
        clicked = items[idx]

        if self._expanded_decade is None:
            # Decade view → expand clicked decade
            self._expanded_decade = clicked
            self._hovered_index = None
            self.update()
        else:
            # Year view → select year, collapse back to decades
            if clicked != self.selected_year:
                self.selected_year = clicked
                self.yearSelected.emit(self.selected_year)
            self._collapse_to_decades()

    def mouseMoveEvent(self, event):
        """Update hover highlight — no drag selection."""
        idx = self._index_near_x(event.position().x())
        if idx != self._hovered_index:
            self._hovered_index = idx
            self.setCursor(Qt.PointingHandCursor if idx is not None else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        if self._hovered_index is not None:
            self._hovered_index = None
            self.update()
        self.setCursor(Qt.ArrowCursor)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _collapse_to_decades(self):
        self._expanded_decade = None
        self._hovered_index = None
        self.update()

    def sizeHint(self):
        return QSize(600, 90)
