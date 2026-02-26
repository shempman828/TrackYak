"""
dates_calendar.py

A modern calendar widget that is fully compatible with dark_mode.qss.
All inline styles use the theme palette so empty cells never look stark or broken.

Palette reference (dark_mode.qss):
  Base bg:    #0b0c10
  Slightly lighter bg: #11121a / #1a1b26
  Accent:     #8599ea
  Gold:       #EAD685
  Pink:       #EA8599
  Green:      #99EA85
  Text:       #b8c0f0
"""

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ── Palette constants (mirror dark_mode.qss) ─────────────────────────────────
_BG_BASE = "#0b0c10"
_BG_SLIGHT = "#11121a"
_BG_CARD = "#14151e"  # day cell background
_BG_EMPTY = "#0e0f15"  # empty / outside-month cell (subtle, not stark)
_ACCENT = "#8599ea"
_ACCENT_DIM = "rgba(133,153,234,0.18)"
_ACCENT_BORDER = "rgba(133,153,234,0.45)"
_GOLD = "#EAD685"
_PINK = "#EA8599"
_GREEN = "#99EA85"
_TEXT = "#b8c0f0"
_TEXT_DIM = "#555e7a"
_TEXT_DARK = "#0b0c10"
_BORDER_SUBTLE = "#1e1f2b"


class CalendarDayWidget(QFrame):
    """
    Represents a single day cell in the calendar grid.
    Styled entirely within the dark_mode.qss palette.
    """

    def __init__(self, day_number: int, events: list, is_current_month: bool = True):
        super().__init__()
        self.day_number = day_number
        self.events = events
        self.is_current_month = is_current_month
        self._init_ui()

    def _init_ui(self):
        self.setFrameStyle(QFrame.NoFrame)

        layout = QVBoxLayout()
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(2)

        # ── Cell styling ─────────────────────────────────────────────────────
        if not self.is_current_month:
            # Empty / filler cell — very subtle, not stark black
            self.setStyleSheet(f"""
                CalendarDayWidget {{
                    background-color: {_BG_EMPTY};
                    border: 1px solid {_BORDER_SUBTLE};
                    border-radius: 4px;
                }}
            """)
        elif self.events:
            # Day with events — gently highlighted with accent
            self.setStyleSheet(f"""
                CalendarDayWidget {{
                    background-color: {_ACCENT_DIM};
                    border: 1px solid {_ACCENT_BORDER};
                    border-radius: 4px;
                }}
                CalendarDayWidget:hover {{
                    background-color: rgba(133,153,234,0.28);
                    border: 1px solid {_ACCENT};
                }}
            """)
        else:
            # Normal day — slightly elevated from base
            self.setStyleSheet(f"""
                CalendarDayWidget {{
                    background-color: {_BG_CARD};
                    border: 1px solid {_BORDER_SUBTLE};
                    border-radius: 4px;
                }}
                CalendarDayWidget:hover {{
                    background-color: {_BG_SLIGHT};
                    border: 1px solid rgba(133,153,234,0.3);
                }}
            """)

        # ── Day number label ─────────────────────────────────────────────────
        if self.day_number > 0:
            day_label = QLabel(str(self.day_number))
            font = QFont("Cambria", 9)
            font.setBold(True)
            day_label.setFont(font)
            day_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

            if not self.is_current_month:
                day_label.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
            elif self.events:
                day_label.setStyleSheet(f"color: {_GOLD}; background: transparent;")
            else:
                day_label.setStyleSheet(f"color: {_TEXT}; background: transparent;")

            layout.addWidget(day_label)

        # ── Event chips ───────────────────────────────────────────────────────
        if self.events and self.is_current_month:
            for event in self.events[:3]:
                chip = QLabel(f"· {event['entity_name']}")
                chip.setWordWrap(False)
                chip.setMaximumWidth(120)

                # Colour-code by entity type
                entity = event.get("entity", "").lower()
                if "album" in entity:
                    chip_color = _ACCENT
                elif "artist" in entity:
                    chip_color = _PINK
                elif "track" in entity:
                    chip_color = _GREEN
                else:
                    chip_color = _GOLD

                chip.setStyleSheet(f"""
                    QLabel {{
                        font-size: 8px;
                        color: {chip_color};
                        background: transparent;
                        padding: 0px;
                    }}
                """)

                # Full tooltip
                tooltip = (
                    f"{event['entity_name']} ({event['entity']})\n"
                    f"Date: {event.get('month', '?')}/{event.get('day', '?')}/{event.get('year', '?')}\n"
                    f"Description: {event.get('description', '—')}"
                )
                chip.setToolTip(tooltip)
                layout.addWidget(chip)

            if len(self.events) > 3:
                more = QLabel(f"+{len(self.events) - 3} more")
                more.setStyleSheet(
                    f"font-size: 8px; color: {_TEXT_DIM}; background: transparent;"
                )
                layout.addWidget(more)

        layout.addStretch()
        self.setLayout(layout)
        self.setMinimumHeight(90)
        self.setMaximumHeight(115)


class CalendarWidget(QWidget):
    """
    A full monthly calendar view.
    Accepts a year and a list of event dicts; refreshes automatically when either changes.
    """

    def __init__(self, year: int, events_data: list = None, parent=None):
        super().__init__(parent)
        self.year = year
        self.events_data = events_data or []
        self.current_month = 1

        self.events_by_date = self._organize_events_by_date()
        self._init_ui()
        self.update_calendar()

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_events(self, events_data: list):
        """Replace events and redraw."""
        self.events_data = events_data
        self.events_by_date = self._organize_events_by_date()
        self.update_calendar()

    def set_year(self, year: int):
        """Change the year and redraw."""
        self.year = year
        self._year_label.setText(str(year))
        self.events_by_date = self._organize_events_by_date()
        self.update_calendar()

    def go_to_month(self, month: int):
        """Navigate to a specific month (1–12) without rebuilding everything."""
        if 1 <= month <= 12:
            self.current_month = month
            self.month_combo.blockSignals(True)
            self.month_combo.setCurrentIndex(month - 1)
            self.month_combo.blockSignals(False)
            self.update_calendar()

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _organize_events_by_date(self) -> dict:
        organized = {}
        for event in self.events_data:
            key = (event.get("month"), event.get("day"))
            if None not in key:
                organized.setdefault(key, []).append(event)
        return organized

    def _days_in_month(self, year: int, month: int) -> int:
        if month == 2:
            leap = (year % 400 == 0) or (year % 4 == 0 and year % 100 != 0)
            return 29 if leap else 28
        return 30 if month in (4, 6, 9, 11) else 31

    def _first_weekday(self, year: int, month: int) -> int:
        """0 = Monday … 6 = Sunday"""
        return date(year, month, 1).weekday()

    # ── UI construction ─────────────────────────────────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Header row ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(6)

        self.prev_button = QPushButton("◀")
        self.prev_button.setFixedWidth(34)
        self.prev_button.setToolTip("Previous month")
        self.prev_button.clicked.connect(self.previous_month)

        self.month_combo = QComboBox()
        self.month_combo.addItems(
            [
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ]
        )
        self.month_combo.setCurrentIndex(self.current_month - 1)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)

        self.next_button = QPushButton("▶")
        self.next_button.setFixedWidth(34)
        self.next_button.setToolTip("Next month")
        self.next_button.clicked.connect(self.next_month)

        # Year badge (read-only display — year is driven by the timeline)
        self._year_label = QLabel(str(self.year))
        year_font = QFont("Cambria", 13)
        year_font.setBold(True)
        self._year_label.setFont(year_font)
        self._year_label.setStyleSheet(
            f"color: {_GOLD}; background: transparent; padding: 0 8px;"
        )
        self._year_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.summary_label = QLabel("")
        self.summary_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.summary_label.setStyleSheet(
            f"color: {_TEXT_DIM}; font-size: 10px; background: transparent;"
        )

        header.addWidget(self.prev_button)
        header.addWidget(self.month_combo, 2)
        header.addWidget(self.next_button)
        header.addSpacing(8)
        header.addWidget(self._year_label)
        header.addStretch()
        header.addWidget(self.summary_label)
        root.addLayout(header)

        # ── Weekday name row ─────────────────────────────────────────────────
        weekday_row = QGridLayout()
        weekday_row.setSpacing(1)
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for col, name in enumerate(weekdays):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            is_weekend = col >= 5
            color = _PINK if is_weekend else _ACCENT
            lbl.setStyleSheet(f"""
                QLabel {{
                    background-color: {_BG_SLIGHT};
                    color: {color};
                    padding: 4px 2px;
                    font-weight: bold;
                    font-size: 10px;
                    border-bottom: 2px solid {color};
                    border-radius: 3px;
                }}
            """)
            weekday_row.addWidget(lbl, 0, col)
        root.addLayout(weekday_row)

        # ── Calendar grid (inside a scroll area for very dense months) ───────
        self.calendar_grid = QGridLayout()
        self.calendar_grid.setSpacing(2)

        grid_container = QWidget()
        grid_container.setLayout(self.calendar_grid)
        grid_container.setStyleSheet(f"background: {_BG_BASE};")

        scroll = QScrollArea()
        scroll.setWidget(grid_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        self.setLayout(root)

    # ── Calendar rendering ──────────────────────────────────────────────────────

    def update_calendar(self):
        """Rebuild the day-cell grid for the current month/year."""
        # Remove old cells
        while self.calendar_grid.count():
            item = self.calendar_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        days_in_month = self._days_in_month(self.year, self.current_month)
        first_weekday = self._first_weekday(self.year, self.current_month)

        day_counter = 1
        for row in range(6):
            for col in range(7):
                if row == 0 and col < first_weekday:
                    cell = CalendarDayWidget(0, [], is_current_month=False)
                elif day_counter <= days_in_month:
                    day_events = self.events_by_date.get(
                        (self.current_month, day_counter), []
                    )
                    cell = CalendarDayWidget(
                        day_counter, day_events, is_current_month=True
                    )
                    day_counter += 1
                else:
                    cell = CalendarDayWidget(0, [], is_current_month=False)

                self.calendar_grid.addWidget(cell, row, col)

            # Stop adding rows once all days are placed and rest would be empty
            if day_counter > days_in_month and row >= 3:
                break

        # Summary
        events_this_month = sum(
            1 for e in self.events_data if e.get("month") == self.current_month
        )
        month_name = self.month_combo.currentText()
        if events_this_month:
            self.summary_label.setText(
                f"{month_name} {self.year} — {events_this_month} event{'s' if events_this_month != 1 else ''}"
            )
        else:
            self.summary_label.setText(f"{month_name} {self.year} — no events")

    # ── Navigation ──────────────────────────────────────────────────────────────

    def _on_month_changed(self, index: int):
        self.current_month = index + 1
        self.update_calendar()

    def previous_month(self):
        if self.current_month > 1:
            self.go_to_month(self.current_month - 1)

    def next_month(self):
        if self.current_month < 12:
            self.go_to_month(self.current_month + 1)
