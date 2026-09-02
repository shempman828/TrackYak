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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.common.layout_utils import clear_layout
from src.foundation.display_settings import apply_scaled_style
from src.foundation.logger_config import logger

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

_MONTH_NAMES = [
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

# ── Date-type labels ──────────────────────────────────────────────────────────
# Maps an event's "type" field to a human-readable (singular, plural) label pair.
_TYPE_LABELS = {
    "album_release": ("Album Release", "Album Releases"),
    "artist_begin": ("Artist Born", "Artists Born"),
    "artist_end": ("Artist Died", "Artists Died"),
    "track_recorded": ("Track Recorded", "Tracks Recorded"),
    "track_composed": ("Track Composed", "Tracks Composed"),
    "track_first_performed": ("Track First Performed", "Tracks First Performed"),
    "track_remastered": ("Track Remastered", "Tracks Remastered"),
    "publisher_begin": ("Publisher Founded", "Publishers Founded"),
    "publisher_end": ("Publisher Closed", "Publishers Closed"),
    "award": ("Award Given", "Awards Given"),
}


def _type_label(type_key: str, count: int = 1) -> str:
    """Human-readable label for a date "type", pluralized when count != 1."""
    fallback = (type_key or "other").replace("_", " ").title()
    singular, plural = _TYPE_LABELS.get(type_key, (fallback, fallback))
    return singular if count == 1 else plural


def _entity_color(entity: str) -> str:
    entity = (entity or "").lower()
    if "album" in entity:
        return _ACCENT
    if "artist" in entity:
        return _PINK
    if "track" in entity:
        return _GREEN
    return _GOLD


def _group_events_by_type(events: list) -> dict:
    groups = {}
    for event in events:
        groups.setdefault(event.get("type", "other"), []).append(event)
    return groups


def _build_event_row(event: dict) -> QFrame:
    frame = QFrame()
    apply_scaled_style(
        frame,
        f"""
        QFrame {{
            background-color: {_BG_CARD};
            border: 1px solid {_BORDER_SUBTLE};
            border-radius: 4px;
        }}
    """,
    )
    row_layout = QVBoxLayout(frame)
    row_layout.setContentsMargins(8, 6, 8, 6)
    row_layout.setSpacing(2)

    color = _entity_color(event.get("entity", ""))

    name_label = QLabel(event.get("entity_name", "Unknown"))
    name_font = QFont("Cambria", 11)
    name_font.setBold(True)
    name_label.setFont(name_font)
    name_label.setStyleSheet(f"color: {color}; background: transparent;")
    row_layout.addWidget(name_label)

    type_label = QLabel(_type_label(event.get("type", "")))
    apply_scaled_style(type_label, f"color: {_TEXT_DIM}; font-size: 10px; background: transparent;")
    row_layout.addWidget(type_label)

    if event.get("description"):
        desc_label = QLabel(event["description"])
        desc_label.setWordWrap(True)
        apply_scaled_style(desc_label, f"color: {_TEXT}; font-size: 10px; background: transparent;")
        row_layout.addWidget(desc_label)

    return frame


class CalendarDayWidget(QFrame):
    """
    Represents a single day cell in the calendar grid.
    Styled entirely within the dark_mode.qss palette.
    """

    day_clicked = Signal(int, list)  # day_number, events

    def __init__(self, day_number: int, events: list, is_current_month: bool = True):
        super().__init__()
        self.day_number = day_number
        self.events = events
        self.is_current_month = is_current_month
        self._init_ui()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_current_month and self.events:
            self.day_clicked.emit(self.day_number, self.events)
        super().mousePressEvent(event)

    def _init_ui(self):
        self.setFrameStyle(QFrame.NoFrame)

        layout = QVBoxLayout()
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(2)

        # ── Cell styling ─────────────────────────────────────────────────────
        if not self.is_current_month:
            # Empty / filler cell — very subtle, not stark black
            apply_scaled_style(
                self,
                f"""
                CalendarDayWidget {{
                    background-color: {_BG_EMPTY};
                    border: 1px solid {_BORDER_SUBTLE};
                    border-radius: 4px;
                }}
            """,
            )
        elif self.events:
            # Day with events — gently highlighted with accent
            apply_scaled_style(
                self,
                f"""
                CalendarDayWidget {{
                    background-color: {_ACCENT_DIM};
                    border: 1px solid {_ACCENT_BORDER};
                    border-radius: 4px;
                }}
                CalendarDayWidget:hover {{
                    background-color: rgba(133,153,234,0.28);
                    border: 1px solid {_ACCENT};
                }}
            """,
            )
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Click for full details")
        else:
            # Normal day — slightly elevated from base
            apply_scaled_style(
                self,
                f"""
                CalendarDayWidget {{
                    background-color: {_BG_CARD};
                    border: 1px solid {_BORDER_SUBTLE};
                    border-radius: 4px;
                }}
                CalendarDayWidget:hover {{
                    background-color: {_BG_SLIGHT};
                    border: 1px solid rgba(133,153,234,0.3);
                }}
            """,
            )

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

        # ── Event type chips (grouped, e.g. "Album Releases: 2") ──────────────
        if self.events and self.is_current_month:
            groups = _group_events_by_type(self.events)
            sorted_types = sorted(groups.keys(), key=lambda t: _type_label(t))
            max_lines = 3

            for type_key in sorted_types[:max_lines]:
                group_events = groups[type_key]
                count = len(group_events)
                label_text = _type_label(type_key, count)

                chip = QLabel(f"{label_text}: {count}")
                chip.setWordWrap(False)
                chip.setMaximumWidth(120)

                chip_color = _entity_color(group_events[0].get("entity", ""))
                apply_scaled_style(
                    chip,
                    f"""
                    QLabel {{
                        font-size: 8px;
                        color: {chip_color};
                        background: transparent;
                        padding: 0px;
                    }}
                """,
                )

                names = "\n".join(f"• {e.get('entity_name', '?')}" for e in group_events[:8])
                if count > 8:
                    names += f"\n… and {count - 8} more"
                chip.setToolTip(f"{label_text}\n{names}")
                layout.addWidget(chip)

            if len(sorted_types) > max_lines:
                more = QLabel(f"+{len(sorted_types) - max_lines} more type(s)")
                apply_scaled_style(
                    more, f"font-size: 8px; color: {_TEXT_DIM}; background: transparent;"
                )
                layout.addWidget(more)

        layout.addStretch()
        self.setLayout(layout)
        self.setMinimumHeight(90)
        self.setMaximumHeight(115)


class DateDetailDialog(QDialog):
    """Shows full details for every event on a single calendar day."""

    def __init__(self, year: int, month: int, day: int, events: list, parent=None):
        super().__init__(parent)
        month_name = _MONTH_NAMES[month - 1] if 1 <= month <= 12 else str(month)
        self.setWindowTitle(f"{month_name} {day}, {year}")
        self.setMinimumSize(420, 320)
        self._init_ui(month_name, day, year, events)

    def _init_ui(self, month_name, day, year, events):
        layout = QVBoxLayout(self)

        header = QLabel(f"{month_name} {day}, {year}")
        header_font = QFont("Cambria", 14)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setStyleSheet(f"color: {_GOLD};")
        layout.addWidget(header)

        count_label = QLabel(f"{len(events)} event{'s' if len(events) != 1 else ''}")
        apply_scaled_style(count_label, f"color: {_TEXT_DIM}; font-size: 11px;")
        layout.addWidget(count_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(8)

        sorted_events = sorted(
            events, key=lambda e: (_type_label(e.get("type", "")), e.get("entity_name", ""))
        )
        for event in sorted_events:
            container_layout.addWidget(_build_event_row(event))
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)


class OnThisDayDialog(QDialog):
    """Shows every event that ever happened on a given month/day, grouped by year."""

    def __init__(self, month: int, day: int, events: list, parent=None):
        super().__init__(parent)
        month_name = _MONTH_NAMES[month - 1] if 1 <= month <= 12 else str(month)
        self.setWindowTitle(f"On This Day — {month_name} {day}")
        self.setMinimumSize(460, 380)
        self._init_ui(month_name, day, events)

    def _init_ui(self, month_name, day, events):
        layout = QVBoxLayout(self)

        header = QLabel(f"On This Day — {month_name} {day}")
        header_font = QFont("Cambria", 14)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setStyleSheet(f"color: {_GOLD};")
        layout.addWidget(header)

        years_count = len({e.get("year") for e in events if e.get("year")})
        count_label = QLabel(
            f"{len(events)} event{'s' if len(events) != 1 else ''} across {years_count} year{'s' if years_count != 1 else ''}"
        )
        apply_scaled_style(count_label, f"color: {_TEXT_DIM}; font-size: 11px;")
        layout.addWidget(count_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(10)

        if events:
            events_by_year = {}
            for event in events:
                events_by_year.setdefault(event.get("year"), []).append(event)

            for year in sorted(events_by_year.keys(), reverse=True):
                year_label = QLabel(str(year))
                year_font = QFont("Cambria", 12)
                year_font.setBold(True)
                year_label.setFont(year_font)
                year_label.setStyleSheet(f"color: {_ACCENT}; background: transparent;")
                container_layout.addWidget(year_label)

                sorted_events = sorted(
                    events_by_year[year],
                    key=lambda e: (_type_label(e.get("type", "")), e.get("entity_name", "")),
                )
                for event in sorted_events:
                    container_layout.addWidget(_build_event_row(event))
        else:
            empty_label = QLabel("No events found for this day in any year.")
            apply_scaled_style(empty_label, f"color: {_TEXT_DIM}; font-size: 11px;")
            container_layout.addWidget(empty_label)

        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)


class CalendarWidget(QWidget):
    """
    A full monthly calendar view.
    Accepts a year and a list of event dicts; refreshes automatically when either changes.
    """

    on_this_day_requested = Signal()

    def __init__(self, year: int, events_data: list = None, parent=None):
        super().__init__(parent)
        self.year = year
        self.all_events = events_data or []
        self.events_data = list(self.all_events)
        self.current_month = 1

        self.events_by_date = self._organize_events_by_date()
        self._init_ui()
        self._populate_type_filter()
        self._refresh_month_availability()
        self.update_calendar()

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_events(self, events_data: list):
        """Replace events and redraw."""
        self.all_events = events_data or []
        self._populate_type_filter()
        self._apply_filter()

    def set_year(self, year: int):
        """Change the year and redraw."""
        logger.debug(f"Calendar year changed to {year}")
        self.year = year
        self._year_label.setText(str(year))
        self.events_by_date = self._organize_events_by_date()
        self._refresh_month_availability()
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

    def _populate_type_filter(self):
        """Refresh the filter dropdown's options to match the currently loaded events."""
        types_present = sorted(
            {e.get("type") for e in self.all_events if e.get("type")}, key=lambda t: _type_label(t)
        )
        previous_selection = (
            self.type_filter_combo.currentData() if self.type_filter_combo.count() else None
        )

        self.type_filter_combo.blockSignals(True)
        self.type_filter_combo.clear()
        self.type_filter_combo.addItem("All Event Types", None)
        for type_key in types_present:
            self.type_filter_combo.addItem(_type_label(type_key), type_key)

        restored_index = (
            self.type_filter_combo.findData(previous_selection) if previous_selection else 0
        )
        self.type_filter_combo.setCurrentIndex(restored_index if restored_index >= 0 else 0)
        self.type_filter_combo.blockSignals(False)

    def _apply_filter(self):
        """Rebuild events_data/events_by_date based on the active type filter."""
        active_type = (
            self.type_filter_combo.currentData() if self.type_filter_combo.count() else None
        )
        if active_type:
            self.events_data = [e for e in self.all_events if e.get("type") == active_type]
        else:
            self.events_data = list(self.all_events)

        self.events_by_date = self._organize_events_by_date()
        self._refresh_month_availability()
        self.update_calendar()

    def _refresh_month_availability(self):
        """Grey out (disable) months in the dropdown that have no events."""
        months_with_events = {e.get("month") for e in self.events_data if e.get("month")}
        model = self.month_combo.model()
        for month in range(1, 13):
            item = model.item(month - 1)
            if item is not None:
                item.setEnabled(month in months_with_events)

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
        self.month_combo.addItems(_MONTH_NAMES)
        self.month_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.month_combo.setMinimumContentsLength(10)
        self.month_combo.setMaximumWidth(150)
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
        apply_scaled_style(
            self._year_label, f"color: {_GOLD}; background: transparent; padding: 0 8px;"
        )
        self._year_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.summary_label = QLabel("")
        self.summary_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        apply_scaled_style(
            self.summary_label, f"color: {_TEXT_DIM}; font-size: 10px; background: transparent;"
        )

        self.on_this_day_button = QPushButton("On This Day")
        self.on_this_day_button.setToolTip(
            "See what happened on today's date across every year in your library"
        )
        apply_scaled_style(
            self.on_this_day_button,
            f"""
            QPushButton {{
                background-color: {_ACCENT_DIM};
                color: {_GOLD};
                border: 1px solid {_ACCENT_BORDER};
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(133,153,234,0.28);
                border: 1px solid {_ACCENT};
            }}
        """,
        )
        self.on_this_day_button.clicked.connect(self.on_this_day_requested.emit)

        header.addWidget(self.prev_button)
        header.addWidget(self.month_combo)
        header.addWidget(self.next_button)
        header.addSpacing(8)
        header.addWidget(self._year_label)
        header.addStretch()
        header.addWidget(self.on_this_day_button)
        header.addSpacing(8)
        header.addWidget(self.summary_label)
        root.addLayout(header)

        # ── Filter row ──────────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        filter_label = QLabel("Filter:")
        apply_scaled_style(filter_label, f"color: {_TEXT_DIM}; font-size: 10px;")

        self.type_filter_combo = QComboBox()
        self.type_filter_combo.addItem("All Event Types", None)
        self.type_filter_combo.currentIndexChanged.connect(self._on_filter_changed)

        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.type_filter_combo, 1)
        filter_row.addStretch(2)
        root.addLayout(filter_row)

        # ── Weekday name row ─────────────────────────────────────────────────
        weekday_row = QGridLayout()
        weekday_row.setSpacing(1)
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for col, name in enumerate(weekdays):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            is_weekend = col >= 5
            color = _PINK if is_weekend else _ACCENT
            apply_scaled_style(
                lbl,
                f"""
                QLabel {{
                    background-color: {_BG_SLIGHT};
                    color: {color};
                    padding: 4px 2px;
                    font-weight: bold;
                    font-size: 10px;
                    border-bottom: 2px solid {color};
                    border-radius: 3px;
                }}
            """,
            )
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
        clear_layout(self.calendar_grid)

        days_in_month = self._days_in_month(self.year, self.current_month)
        first_weekday = self._first_weekday(self.year, self.current_month)

        day_counter = 1
        for row in range(6):
            for col in range(7):
                if row == 0 and col < first_weekday:
                    cell = CalendarDayWidget(0, [], is_current_month=False)
                elif day_counter <= days_in_month:
                    day_events = self.events_by_date.get((self.current_month, day_counter), [])
                    cell = CalendarDayWidget(day_counter, day_events, is_current_month=True)
                    if day_events:
                        cell.day_clicked.connect(self._show_day_detail)
                    day_counter += 1
                else:
                    cell = CalendarDayWidget(0, [], is_current_month=False)

                self.calendar_grid.addWidget(cell, row, col)

            # Stop adding rows once all days are placed and rest would be empty
            if day_counter > days_in_month and row >= 3:
                break

        # Summary
        events_this_month = sum(1 for e in self.events_data if e.get("month") == self.current_month)
        month_name = self.month_combo.currentText()
        if events_this_month:
            self.summary_label.setText(
                f"{month_name} {self.year} — {events_this_month} event{'s' if events_this_month != 1 else ''}"
            )
        else:
            self.summary_label.setText(f"{month_name} {self.year} — no events")

    def _show_day_detail(self, day_number: int, events: list):
        dialog = DateDetailDialog(self.year, self.current_month, day_number, events, parent=self)
        dialog.exec()

    # ── Navigation ──────────────────────────────────────────────────────────────

    def _on_month_changed(self, index: int):
        self.current_month = index + 1
        self.update_calendar()

    def _on_filter_changed(self, index: int):
        self._apply_filter()

    def previous_month(self):
        if self.current_month > 1:
            self.go_to_month(self.current_month - 1)

    def next_month(self):
        if self.current_month < 12:
            self.go_to_month(self.current_month + 1)
