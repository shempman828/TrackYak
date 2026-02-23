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
    QVBoxLayout,
    QWidget,
)


class CalendarDayWidget(QFrame):
    """Widget for a single day in the calendar"""

    def __init__(self, day_number, events, is_current_month=True):
        super().__init__()
        self.day_number = day_number
        self.events = events
        self.is_current_month = is_current_month
        self.init_ui()

    def init_ui(self):
        self.setFrameStyle(QFrame.Box)
        self.setLineWidth(1)

        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        # Day number header
        day_label = QLabel(str(self.day_number) if self.day_number > 0 else "")
        day_font = QFont()
        day_font.setBold(True)
        day_label.setFont(day_font)
        day_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        # Style based on whether date has events or not
        if not self.is_current_month:
            self.setStyleSheet("""
                CalendarDayWidget {
                    background-color: #f0f0f0;
                    color: #888888;
                }
            """)
        elif self.events:
            self.setStyleSheet("""
                CalendarDayWidget {
                    background-color: #e8f4f8;
                    border: 2px solid #4a90e2;
                }
            """)
        else:
            self.setStyleSheet("""
                CalendarDayWidget {
                    background-color: #f8f8f8;
                }
            """)

        layout.addWidget(day_label)

        # Add events
        if self.events:
            for event in self.events[:3]:  # Show max 3 events per day
                event_text = f"{event['entity']}: {event['entity_name']}"
                event_label = QLabel(event_text)
                event_label.setWordWrap(True)
                event_label.setStyleSheet("font-size: 9px; color: #333333;")

                # Tooltip with full details
                tooltip = (
                    f"{event['entity_name']} ({event['entity']})\n"
                    f"Date: {event['month']}/{event['day']}/{event['year']}\n"
                    f"Description: {event['description']}"
                )
                event_label.setToolTip(tooltip)

                layout.addWidget(event_label)

            # Show "..." if there are more than 3 events
            if len(self.events) > 3:
                more_label = QLabel("...")
                more_label.setStyleSheet("font-size: 9px; color: #666666;")
                layout.addWidget(more_label)

        self.setLayout(layout)
        self.setMinimumHeight(100)
        self.setMaximumHeight(120)


class CalendarWidget(QWidget):
    """Main calendar widget that can be embedded in other applications"""

    def __init__(self, year, events_data=None, parent=None):
        super().__init__(parent)
        self.year = year
        self.events_data = events_data or []
        self.current_month = 1  # January by default

        # Organize events by date for quick lookup
        self.events_by_date = self._organize_events_by_date()

        self.init_ui()
        self.update_calendar()

    def _organize_events_by_date(self):
        """Organize events by (month, day) for quick lookup"""
        organized = {}
        for event in self.events_data:
            key = (event["month"], event["day"])
            if key not in organized:
                organized[key] = []
            organized[key].append(event)
        return organized

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)

        # Month navigation header
        header_layout = QHBoxLayout()

        # Previous month button
        self.prev_button = QPushButton("◀ Previous")
        self.prev_button.clicked.connect(self.previous_month)
        header_layout.addWidget(self.prev_button)

        # Month dropdown
        self.month_combo = QComboBox()
        months = [
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
        self.month_combo.addItems(months)
        self.month_combo.currentIndexChanged.connect(self.month_changed)
        header_layout.addWidget(self.month_combo)

        # Next month button
        self.next_button = QPushButton("Next ▶")
        self.next_button.clicked.connect(self.next_month)
        header_layout.addWidget(self.next_button)

        # Year display
        year_label = QLabel(f"Year: {self.year}")
        year_font = QFont()
        year_font.setPointSize(12)
        year_font.setBold(True)
        year_label.setFont(year_font)
        header_layout.addWidget(year_label)
        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        # Weekday headers
        weekdays = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        weekday_layout = QGridLayout()
        for i, day in enumerate(weekdays):
            day_label = QLabel(day[:3])  # Short form
            day_label.setAlignment(Qt.AlignCenter)
            day_label.setStyleSheet("""
                QLabel {
                    background-color: #4a90e2;
                    color: white;
                    padding: 5px;
                    font-weight: bold;
                    border: 1px solid #3a7bc8;
                }
            """)
            weekday_layout.addWidget(day_label, 0, i)
        main_layout.addLayout(weekday_layout)

        # Calendar grid
        self.calendar_grid = QGridLayout()
        self.calendar_grid.setSpacing(1)
        main_layout.addLayout(self.calendar_grid)

        # Summary label
        self.summary_label = QLabel("")
        self.summary_label.setAlignment(Qt.AlignCenter)
        self.summary_label.setStyleSheet(
            "font-size: 11px; color: #666666; margin-top: 10px;"
        )
        main_layout.addWidget(self.summary_label)

        self.setLayout(main_layout)

    def month_changed(self, index):
        """Handle month selection from dropdown"""
        self.current_month = index + 1
        self.update_calendar()

    def previous_month(self):
        """Navigate to previous month"""
        if self.current_month > 1:
            self.current_month -= 1
            self.month_combo.setCurrentIndex(self.current_month - 1)
            self.update_calendar()

    def next_month(self):
        """Navigate to next month"""
        if self.current_month < 12:
            self.current_month += 1
            self.month_combo.setCurrentIndex(self.current_month - 1)
            self.update_calendar()

    def get_days_in_month(self, year, month):
        """Get number of days in a month, accounting for leap years"""
        if month == 2:
            # February: check for leap year
            if year % 400 == 0:
                return 29
            elif year % 100 == 0:
                return 28
            elif year % 4 == 0:
                return 29
            else:
                return 28
        elif month in [4, 6, 9, 11]:
            return 30
        else:
            return 31

    def get_first_weekday(self, year, month):
        """Get the weekday of the first day of the month (0=Monday, 6=Sunday)"""
        d = date(year, month, 1)
        return d.weekday()  # Monday = 0, Sunday = 6

    def update_calendar(self):
        """Update the calendar display for the current month"""
        # Clear existing calendar widgets - FIXED: Proper widget cleanup
        while self.calendar_grid.count():
            item = self.calendar_grid.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                # Handle nested layouts if any
                while item.layout().count():
                    nested_item = item.layout().takeAt(0)
                    if nested_item.widget():
                        nested_item.widget().deleteLater()

        # Calculate calendar parameters
        days_in_month = self.get_days_in_month(self.year, self.current_month)
        first_weekday = self.get_first_weekday(self.year, self.current_month)

        # Fill in the days before the first day of the month
        day_counter = 1
        for row in range(6):  # Maximum 6 weeks needed
            for col in range(7):  # 7 days per week
                if row == 0 and col < first_weekday:
                    # Empty cell before the first day
                    day_widget = CalendarDayWidget(0, [], is_current_month=False)
                elif day_counter <= days_in_month:
                    # Get events for this day
                    day_events = self.events_by_date.get(
                        (self.current_month, day_counter), []
                    )
                    day_widget = CalendarDayWidget(day_counter, day_events)
                    day_counter += 1
                else:
                    # Empty cell after the last day
                    day_widget = CalendarDayWidget(0, [], is_current_month=False)

                self.calendar_grid.addWidget(day_widget, row + 1, col)

        # Update summary
        events_this_month = sum(
            1 for event in self.events_data if event["month"] == self.current_month
        )
        month_name = self.month_combo.currentText()
        self.summary_label.setText(
            f"{month_name} {self.year}: {events_this_month} event(s) this month"
        )

    def set_events(self, events_data):
        """Update the events data and refresh the calendar"""
        self.events_data = events_data
        self.events_by_date = self._organize_events_by_date()
        self.update_calendar()

    def set_year(self, year):
        """Change the year and refresh the calendar"""
        self.year = year
        # Update events_by_date to reflect the new year
        self.events_by_date = self._organize_events_by_date()
        self.update_calendar()

    def go_to_month(self, month):
        """Navigate to a specific month (1-12)"""
        if 1 <= month <= 12:
            self.current_month = month
            self.month_combo.setCurrentIndex(month - 1)
            self.update_calendar()
