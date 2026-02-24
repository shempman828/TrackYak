from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from dates_calendar import CalendarWidget
from dates_timeline import TimelineWidget
from logger_config import logger


class TimelineView(QWidget):
    """Parent view that coordinates calendar and timeline widgets."""

    # Signals for communication with child widgets
    years_loaded = Signal(list)  # List of unique years
    months_loaded = Signal(list)  # List of unique months (1-12)
    dates_loaded = Signal(list)  # List of all date items with year/month/day

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.all_dates = []  # Will hold all date dictionaries
        self.current_year = None  # Currently selected year
        self.current_month = None  # Currently selected month

        self.setup_ui()
        self.connect_signals()
        self.load_dates_from_db()

    def setup_ui(self):
        """Initialize the UI layout."""
        self.setWindowTitle("Music Calendar")
        self.setGeometry(100, 100, 1200, 800)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Create splitter for top (calendar) and bottom (timeline)
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Vertical)
        splitter.setHandleWidth(3)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #8599ea;
                margin: 2px;
            }
            QSplitter::handle:vertical {
                height: 3px;
            }
            QSplitter::handle:hover {
                background-color: #EAD685;
            }
        """)

        # Initialize calendar widget (will be populated when data is loaded)
        self.calendar_widget = CalendarWidget(
            year=2024,  # Default year, will be updated
            events_data=[],
        )
        self.calendar_widget.setMinimumHeight(400)

        # Initialize timeline widget
        self.timeline_widget = TimelineWidget()
        self.timeline_widget.setMinimumHeight(90)

        splitter.addWidget(self.calendar_widget)
        splitter.addWidget(self.timeline_widget)

        # Set initial sizes (calendar gets most space, timeline is compact)
        splitter.setSizes([640, 120])

        layout.addWidget(splitter)

    def connect_signals(self):
        """Connect signals between widgets."""
        # Connect timeline year selection to calendar updates
        self.timeline_widget.yearSelected.connect(self.on_timeline_year_selected)

        # Connect calendar month changes to update view
        self.calendar_widget.month_combo.currentIndexChanged.connect(
            lambda index: self.update_calendar_for_month(index + 1)
        )

    def load_dates_from_db(self):
        """Load all date integers from the database."""
        if not self.controller:
            logger.error("No controller available to access database")
            return

        try:
            self.all_dates = self.collect_all_dates()
            logger.info(f"Loaded {len(self.all_dates)} date entries from database")

            # Extract unique years and months
            years = self.extract_unique_years()
            months = self.extract_unique_months()

            # Set up timeline with years
            if years:
                self.timeline_widget.set_years(years)
                self.current_year = years[0]

                # Set initial calendar year to first available year
                self.calendar_widget.set_year(self.current_year)

                # Load initial data for the first year
                self.update_calendar_for_year(self.current_year)

            # Emit signals (if other components need them)
            self.years_loaded.emit(years)
            self.months_loaded.emit(months)
            self.dates_loaded.emit(self.all_dates)

        except Exception as e:
            logger.error(f"Error loading dates from database: {e}")

    def on_timeline_year_selected(self, year):
        """Handle year selection from timeline."""
        logger.info(f"Timeline selected year: {year}")
        self.current_year = year
        self.update_calendar_for_year(year)

    def update_calendar_for_year(self, year):
        """Update calendar to show events for the selected year."""
        # Update calendar widget year
        self.calendar_widget.set_year(year)

        # Get events for this year
        year_events = self.filter_dates_by_year(year)

        # Filter out events without month/day for calendar display
        calendar_events = [
            event
            for event in year_events
            if event.get("month") is not None and event.get("day") is not None
        ]

        # Update calendar with events
        self.calendar_widget.set_events(calendar_events)

        # Log statistics
        logger.info(
            f"Calendar updated for year {year} with {len(calendar_events)} dated events"
        )

        # Also update the month combo to show current month if we have data
        if calendar_events:
            # Find the month with most events, or default to current month
            month_counts = {}
            for event in calendar_events:
                month = event.get("month")
                if month:
                    month_counts[month] = month_counts.get(month, 0) + 1

            if month_counts:
                # Get month with most events
                best_month = max(month_counts, key=month_counts.get)
                self.calendar_widget.go_to_month(best_month)
                self.current_month = best_month

    def update_calendar_for_month(self, month):
        """Handle month selection from calendar dropdown."""
        self.current_month = month
        if self.current_year:
            # Get events for this specific month
            month_events = self.filter_dates_by_year_and_month(self.current_year, month)
            calendar_events = [
                event for event in month_events if event.get("day") is not None
            ]

            # Update just the current month display (calendar handles this internally)
            # We don't need to call set_events here as the calendar already has all year data
            logger.info(f"Viewing month {month} with {len(calendar_events)} events")

    def collect_all_dates(self):
        """Collect ALL date integers from various database tables."""
        dates = []

        # 1. Collect Album dates
        dates.extend(self.get_album_dates())

        # 2. Collect Track dates
        dates.extend(self.get_track_dates())

        # 3. Collect Artist dates
        dates.extend(self.get_artist_dates())

        # 4. Collect Publisher dates
        dates.extend(self.get_publisher_dates())

        # 5. Collect Award dates
        dates.extend(self.get_award_dates())

        return dates

    def get_album_dates(self):
        """Extract dates from Album table."""
        dates = []
        albums = self.controller.get.get_all_entities("Album")

        for album in albums:
            # Release date
            if album.release_year:
                dates.append(
                    {
                        "year": album.release_year,
                        "month": album.release_month
                        if hasattr(album, "release_month")
                        else None,
                        "day": album.release_day
                        if hasattr(album, "release_day")
                        else None,
                        "type": "album_release",
                        "entity": "Album",
                        "entity_id": album.album_id,
                        "entity_name": album.album_name,
                        "description": f"Album released: {album.album_name}",
                    }
                )

        return dates

    def get_track_dates(self):
        """Extract dates from Track table."""
        dates = []
        tracks = self.controller.get.get_all_entities("Track")

        for track in tracks:
            # Recorded date
            if track.recorded_year:
                dates.append(
                    {
                        "year": track.recorded_year,
                        "month": track.recorded_month
                        if hasattr(track, "recorded_month")
                        else None,
                        "day": track.recorded_day
                        if hasattr(track, "recorded_day")
                        else None,
                        "type": "track_recorded",
                        "entity": "Track",
                        "entity_id": track.track_id,
                        "entity_name": track.track_name,
                        "description": f"Track recorded: {track.track_name}",
                    }
                )

            # Composed date
            if track.composed_year:
                dates.append(
                    {
                        "year": track.composed_year,
                        "month": track.composed_month
                        if hasattr(track, "composed_month")
                        else None,
                        "day": track.composed_day
                        if hasattr(track, "composed_day")
                        else None,
                        "type": "track_composed",
                        "entity": "Track",
                        "entity_id": track.track_id,
                        "entity_name": track.track_name,
                        "description": f"Track composed: {track.track_name}",
                    }
                )

            # First performed date
            if track.first_performed_year:
                dates.append(
                    {
                        "year": track.first_performed_year,
                        "month": None,  # Not in schema
                        "day": None,  # Not in schema
                        "type": "track_first_performed",
                        "entity": "Track",
                        "entity_id": track.track_id,
                        "entity_name": track.track_name,
                        "description": f"Track first performed: {track.track_name}",
                    }
                )

            # Remaster date
            if track.remaster_year:
                dates.append(
                    {
                        "year": track.remaster_year,
                        "month": None,  # Not in schema
                        "day": None,  # Not in schema
                        "type": "track_remastered",
                        "entity": "Track",
                        "entity_id": track.track_id,
                        "entity_name": track.track_name,
                        "description": f"Track remastered: {track.track_name}",
                    }
                )

        return dates

    def get_artist_dates(self):
        """Extract dates from Artist table."""
        dates = []
        artists = self.controller.get.get_all_entities("Artist")

        for artist in artists:
            # Begin date (birth/formation)
            if artist.begin_year:
                dates.append(
                    {
                        "year": artist.begin_year,
                        "month": artist.begin_month
                        if hasattr(artist, "begin_month")
                        else None,
                        "day": artist.begin_day
                        if hasattr(artist, "begin_day")
                        else None,
                        "type": "artist_begin",
                        "entity": "Artist",
                        "entity_id": artist.artist_id,
                        "entity_name": artist.artist_name,
                        "description": f"Artist started: {artist.artist_name}",
                    }
                )

            # End date (death/disbandment)
            if artist.end_year:
                dates.append(
                    {
                        "year": artist.end_year,
                        "month": artist.end_month
                        if hasattr(artist, "end_month")
                        else None,
                        "day": artist.end_day if hasattr(artist, "end_day") else None,
                        "type": "artist_end",
                        "entity": "Artist",
                        "entity_id": artist.artist_id,
                        "entity_name": artist.artist_name,
                        "description": f"Artist ended: {artist.artist_name}",
                    }
                )

        return dates

    def get_publisher_dates(self):
        """Extract dates from Publisher table."""
        dates = []
        publishers = self.controller.get.get_all_entities("Publisher")

        for publisher in publishers:
            # Begin date
            if publisher.begin_year:
                dates.append(
                    {
                        "year": publisher.begin_year,
                        "month": None,  # Not in schema
                        "day": None,  # Not in schema
                        "type": "publisher_begin",
                        "entity": "Publisher",
                        "entity_id": publisher.publisher_id,
                        "entity_name": publisher.publisher_name,
                        "description": f"Publisher started: {publisher.publisher_name}",
                    }
                )

            # End date
            if publisher.end_year:
                dates.append(
                    {
                        "year": publisher.end_year,
                        "month": None,  # Not in schema
                        "day": None,  # Not in schema
                        "type": "publisher_end",
                        "entity": "Publisher",
                        "entity_id": publisher.publisher_id,
                        "entity_name": publisher.publisher_name,
                        "description": f"Publisher ended: {publisher.publisher_name}",
                    }
                )

        return dates

    def get_award_dates(self):
        """Extract dates from Award table."""
        dates = []
        awards = self.controller.get.get_all_entities("Award")

        for award in awards:
            if award.award_year:
                dates.append(
                    {
                        "year": award.award_year,
                        "month": None,  # Not in schema
                        "day": None,  # Not in schema
                        "type": "award",
                        "entity": "Award",
                        "entity_id": award.award_id,
                        "entity_name": award.award_name,
                        "description": f"Award given: {award.award_name}",
                    }
                )

        return dates

    def filter_dates_by_year(self, year):
        """Filter dates to show only those from the specified year."""
        if not year:
            filtered = self.all_dates
        else:
            filtered = [d for d in self.all_dates if d["year"] and d["year"] == year]

        logger.info(f"Filtered to {len(filtered)} dates from year {year}")
        return filtered

    def filter_dates_by_year_and_month(self, year, month):
        """Filter dates to show only those from the specified year and month."""
        filtered = []

        for d in self.all_dates:
            if d["year"] and d["year"] == year:
                if month and d["month"] and d["month"] == month:
                    filtered.append(d)
                elif not month:  # If month is None, return all for the year
                    filtered.append(d)

        logger.info(
            f"Filtered to {len(filtered)} dates from year {year}, month {month}"
        )
        return filtered

    def extract_unique_years(self):
        """Extract unique years from all dates."""
        years = set()
        for date_item in self.all_dates:
            if date_item["year"]:
                years.add(date_item["year"])
        return sorted(years)

    def extract_unique_months(self):
        """Extract unique months from all dates."""
        months = set()
        for date_item in self.all_dates:
            if date_item["month"] and 1 <= date_item["month"] <= 12:
                months.add(date_item["month"])
        return sorted(months)

    def get_date_statistics(self):
        """Get statistics about the collected dates."""
        stats = {
            "total_dates": len(self.all_dates),
            "years_count": len(self.extract_unique_years()),
            "entity_counts": {},
            "type_counts": {},
        }

        # Count by entity type
        for date_item in self.all_dates:
            entity = date_item["entity"]
            stats["entity_counts"][entity] = stats["entity_counts"].get(entity, 0) + 1

            date_type = date_item["type"]
            stats["type_counts"][date_type] = stats["type_counts"].get(date_type, 0) + 1

        return stats


class DateFormatter:
    """Utility class to format date integers into readable strings."""

    @staticmethod
    def format_date(date_dict):
        """Format a date dictionary into a readable string."""
        year = date_dict.get("year")
        month = date_dict.get("month")
        day = date_dict.get("day")

        if not year:
            return "Unknown date"

        parts = []
        if day:
            parts.append(str(day))
        if month:
            # Convert month number to name
            month_names = [
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
            if 1 <= month <= 12:
                parts.append(month_names[month - 1])

        parts.append(str(year))

        # Join with spaces
        return " ".join(parts)

    @staticmethod
    def format_date_compact(date_dict):
        """Format date as YYYY-MM-DD or partial."""
        year = date_dict.get("year")
        month = date_dict.get("month")
        day = date_dict.get("day")

        if not year:
            return "????"

        result = str(year)
        if month:
            result = f"{month:02d}-{result}"
            if day:
                result = f"{day:02d}-{result}"

        return result
