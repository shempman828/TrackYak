# file: artist_detail_awards.py
"""
AwardsWidget for displaying artist awards in a badge-style format.
"""

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from logger_config import logger


class AwardBadge(QFrame):
    """Individual award badge widget with year, award name, and category"""

    def __init__(self, award_data: dict):
        """
        Initialize an award badge.

        Args:
            award_data: Dictionary containing award information with keys:
                - name: Award name
                - category: Award category (optional)
                - year: Year awarded
                - is_winner: Whether artist won (True) or was just nominated (False)
                - award_id: Award ID for linking (optional)
        """
        super().__init__()
        self.award_data = award_data

        self.setObjectName("AwardBadge")
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setLineWidth(1)
        self.setMinimumWidth(180)
        self.setMaximumWidth(250)
        self.setCursor(Qt.PointingHandCursor)

        # Set background color based on win status
        palette = self.palette()
        if award_data.get("is_winner", True):
            # Winner - goldish background
            palette.setColor(QPalette.Window, QColor(255, 248, 220))  # Light gold
            palette.setColor(QPalette.WindowText, QColor(139, 69, 19))  # SaddleBrown
        else:
            # Nominee - light blue background
            palette.setColor(QPalette.Window, QColor(240, 248, 255))  # AliceBlue
            palette.setColor(QPalette.WindowText, QColor(70, 130, 180))  # SteelBlue
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.init_ui()

    def init_ui(self):
        """Initialize the badge UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Year - prominently displayed
        year = self.award_data.get("year")
        if year:
            year_label = QLabel(str(year))
            year_font = QFont()
            year_font.setBold(True)
            year_font.setPointSize(11)
            year_label.setFont(year_font)
            year_label.setAlignment(Qt.AlignCenter)
            year_label.setObjectName("AwardYear")
            layout.addWidget(year_label)

        # Award name - the main text
        award_name = self.award_data.get("name", "Unknown Award")
        name_label = QLabel(award_name)
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(10)
        name_label.setFont(name_font)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setObjectName("AwardName")
        layout.addWidget(name_label)

        # Category - if available
        category = self.award_data.get("category")
        if category:
            category_label = QLabel(category)
            category_font = QFont()
            category_font.setPointSize(9)
            category_font.setItalic(True)
            category_label.setFont(category_font)
            category_label.setAlignment(Qt.AlignCenter)
            category_label.setWordWrap(True)
            category_label.setObjectName("AwardCategory")
            layout.addWidget(category_label)

        # Win/Nominee indicator
        status = "Winner" if self.award_data.get("is_winner", True) else "Nominee"
        status_label = QLabel(status)
        status_font = QFont()
        status_font.setPointSize(8)
        status_font.setBold(True)
        status_label.setFont(status_font)
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setObjectName("AwardStatus")
        layout.addWidget(status_label)

    def mousePressEvent(self, event):
        """Handle click events - could be used for showing award details"""
        if event.button() == Qt.LeftButton:
            # Emit a signal or trigger action to show award details
            # For now, just change appearance briefly
            original_palette = self.palette()
            highlighted_palette = self.palette()
            highlighted_palette.setColor(QPalette.Window, QColor(220, 220, 220))
            self.setPalette(highlighted_palette)

            # Schedule a reset
            from PySide6.QtCore import QTimer

            QTimer.singleShot(200, lambda: self.setPalette(original_palette))

        super().mousePressEvent(event)


class AwardsWidget(QWidget):
    """Widget for displaying artist awards in a grid of badges"""

    def __init__(self, artist: Any):
        super().__init__()
        self.artist = artist
        self.controller = None
        self.awards_data = []

        # Try to get controller from artist if available
        if hasattr(artist, "controller"):
            self.controller = artist.controller

        self.init_ui()

    def set_controller(self, controller):
        """Set the controller for database access"""
        self.controller = controller
        if self.controller:
            self.load_awards()

    def init_ui(self):
        """Initialize the widget UI"""
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)

        # Title
        self.title_label = QLabel("Awards & Recognition")
        self.title_label.setObjectName("SectionTitle")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(12)
        self.title_label.setFont(title_font)
        self.layout.addWidget(self.title_label)

        # Awards container (initially hidden)
        self.awards_container = QWidget()
        self.awards_layout = QGridLayout(self.awards_container)
        self.awards_layout.setContentsMargins(0, 0, 0, 0)
        self.awards_layout.setSpacing(10)
        self.awards_layout.setAlignment(Qt.AlignTop)

        # Wrap in scroll area for many awards
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.awards_container)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setMaximumHeight(300)

        self.layout.addWidget(scroll_area)

        # Initially hide the entire widget
        self.setVisible(False)

    def load_awards(self):
        """Load awards data from the database"""
        if not self.controller:
            logger.error("No controller available for AwardsWidget")
            return

        try:
            # Get awards associated with this artist
            award_associations = self.controller.get.get_all_entities(
                "AwardAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )

            if not award_associations:
                # No awards found
                self.setVisible(False)
                return

            # Process awards data
            for association in award_associations:
                if hasattr(association, "award") and association.award:
                    award = association.award

                    award_data = {
                        "award_id": getattr(award, "award_id", None),
                        "name": getattr(award, "award_name", "Unknown Award"),
                        "category": getattr(award, "category", None),
                        "year": getattr(award, "year", None),
                        "is_winner": getattr(association, "is_winner", True),
                        "description": getattr(award, "description", None),
                    }

                    self.awards_data.append(award_data)

            # Sort by year (chronologically)
            self.awards_data.sort(
                key=lambda x: (x.get("year") or 9999, x.get("name", ""))
            )

            # Display awards
            self.display_awards()

            # Show widget since we have awards
            if self.awards_data:
                self.setVisible(True)

        except Exception as e:
            logger.error(f"Error loading awards: {e}")
            self.setVisible(False)

    def display_awards(self):
        """Display awards in a grid layout"""
        if not self.awards_data:
            return

        # Clear existing awards
        while self.awards_layout.count():
            item = self.awards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Determine grid columns based on available width
        # We'll use 3 columns as default, but adjust dynamically if needed
        columns = 3

        # Group awards by year if there are many
        years_with_awards = {}
        for award in self.awards_data:
            year = award.get("year", "Unknown Year")
            if year not in years_with_awards:
                years_with_awards[year] = []
            years_with_awards[year].append(award)

        # Sort years
        sorted_years = sorted(years_with_awards.keys())

        current_row = 0

        for year in sorted_years:
            awards_in_year = years_with_awards[year]

            # Add year header if we have multiple years or more than 3 awards total
            if len(self.awards_data) > 3 or len(sorted_years) > 1:
                year_label = QLabel(f"🏆 {year}")
                year_font = QFont()
                year_font.setBold(True)
                year_font.setPointSize(11)
                year_label.setFont(year_font)
                year_label.setObjectName("YearHeader")

                # Span across all columns
                self.awards_layout.addWidget(year_label, current_row, 0, 1, columns)
                current_row += 1

            # Add awards for this year
            for i, award in enumerate(awards_in_year):
                badge = AwardBadge(award)
                col = i % columns
                row = current_row + (i // columns)

                # If this row doesn't exist yet, add it
                if row >= self.awards_layout.rowCount():
                    # Add spacing between rows
                    self.awards_layout.setRowMinimumHeight(row, 10)

                self.awards_layout.addWidget(badge, row, col, Qt.AlignTop)

            # Move to next row group, leaving a gap between years
            current_row += (len(awards_in_year) + columns - 1) // columns + 1

        # Update container size
        self.awards_container.adjustSize()

    def get_awards_summary(self) -> str:
        """Get a text summary of the awards"""
        if not self.awards_data:
            return "No awards"

        # Count wins vs nominations
        wins = sum(1 for award in self.awards_data if award.get("is_winner", True))
        nominations = len(self.awards_data) - wins

        years = set(
            award.get("year") for award in self.awards_data if award.get("year")
        )
        year_range = f"{min(years)}-{max(years)}" if years else ""

        if wins and nominations:
            return f"{wins} wins, {nominations} nominations ({year_range})"
        elif wins:
            return f"{wins} awards won ({year_range})"
        else:
            return f"{nominations} nominations ({year_range})"
