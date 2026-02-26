# file: artist_detail_credits.py
from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logger_config import logger


class RoleSection(QGroupBox):
    """Collapsible section for a specific role with lazy-loaded table"""

    def __init__(self, role_name: str, count: int, artist_id: int, controller: Any):
        super().__init__()
        self.role_name = role_name
        self.count = count
        self.artist_id = artist_id
        self.controller = controller
        self.is_loaded = False

        self.init_ui()

    def init_ui(self):
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Header with toggle button and count
        header_layout = QHBoxLayout()

        # Toggle button
        self.toggle_btn = QPushButton("▶")
        self.toggle_btn.setFixedSize(20, 20)
        self.toggle_btn.setObjectName("RoleToggle")
        self.toggle_btn.clicked.connect(self.toggle_section)

        # Role name and count
        title_label = QLabel(f"{self.role_name} ({self.count})")
        title_label.setObjectName("RoleTitle")

        header_layout.addWidget(self.toggle_btn)
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Container for the table (initially hidden)
        self.table_container = QWidget()
        self.table_container.setVisible(False)
        table_layout = QVBoxLayout(self.table_container)
        table_layout.setContentsMargins(20, 8, 0, 8)

        # Placeholder for the table
        self.table_widget = None

        layout.addWidget(self.table_container)

    def toggle_section(self):
        """Expand or collapse the section, loading data if needed"""
        if not self.table_container.isVisible():
            # Expand
            self.toggle_btn.setText("▼")
            self.table_container.setVisible(True)

            # Load data if not already loaded
            if not self.is_loaded:
                self.load_data()
        else:
            # Collapse
            self.toggle_btn.setText("▶")
            self.table_container.setVisible(False)

    def load_data(self):
        """Lazy load the credits data for this role"""
        try:
            # Fetch track roles for this artist and role
            track_roles = self.controller.get.get_all_entities(
                "TrackArtistRole", artist_id=self.artist_id
            )

            # Fetch album roles for this artist and role
            album_roles = self.controller.get.get_all_entities(
                "AlbumRoleAssociation", artist_id=self.artist_id
            )

            # Filter by role name and prepare data
            credits_data = []

            # Process track roles
            for tr in track_roles:
                if hasattr(tr, "role") and tr.role.role_name == self.role_name:
                    if hasattr(tr, "track") and tr.track:
                        track_name = getattr(tr.track, "track_name", "Unknown Track")
                        album_name = "Unknown Album"
                        year = None

                        # Get album info from track
                        if hasattr(tr.track, "album") and tr.track.album:
                            album_name = getattr(
                                tr.track.album, "album_name", "Unknown Album"
                            )
                            year = getattr(tr.track.album, "year", None)

                        credits_data.append(
                            {
                                "type": "track",
                                "track": track_name,
                                "album": album_name,
                                "year": year,
                                "sort_key": (year or 9999, album_name, track_name),
                            }
                        )

            # Process album roles
            for ar in album_roles:
                if hasattr(ar, "role") and ar.role.role_name == self.role_name:
                    if hasattr(ar, "album") and ar.album:
                        album_name = getattr(ar.album, "album_name", "Unknown Album")
                        year = getattr(ar.album, "year", None)

                        credits_data.append(
                            {
                                "type": "album",
                                "track": "",  # Empty for album roles
                                "album": album_name,
                                "year": year,
                                "sort_key": (year or 9999, album_name, ""),
                            }
                        )

            # Sort chronologically
            credits_data.sort(key=lambda x: x["sort_key"])

            # Create table if we have data
            if credits_data:
                self.create_table(credits_data)
            else:
                # No credits found for this role (shouldn't happen but just in case)
                no_data_label = QLabel("No credits found for this role.")
                no_data_label.setObjectName("NoCreditsLabel")
                self.table_container.layout().addWidget(no_data_label)

            self.is_loaded = True

        except Exception as e:
            logger.error(f"Error loading credits for role {self.role_name}: {e}")
            error_label = QLabel(f"Error loading credits: {str(e)}")
            error_label.setObjectName("ErrorLabel")
            self.table_container.layout().addWidget(error_label)

    def create_table(self, credits_data: List[Dict]):
        """Create and populate the table widget"""
        # Create table
        self.table_widget = QTableWidget()
        self.table_widget.setObjectName("CreditsTable")
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels(["Track", "Album", "Year"])

        # Set table properties
        self.table_widget.setRowCount(len(credits_data))
        self.table_widget.setSortingEnabled(True)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setAlternatingRowColors(True)

        # Populate table
        for row, credit in enumerate(credits_data):
            # Track column
            track_item = QTableWidgetItem(credit["track"])
            track_item.setFlags(track_item.flags() & ~Qt.ItemIsEditable)
            self.table_widget.setItem(row, 0, track_item)

            # Album column
            album_item = QTableWidgetItem(credit["album"])
            album_item.setFlags(album_item.flags() & ~Qt.ItemIsEditable)
            self.table_widget.setItem(row, 1, album_item)

            # Year column
            year_text = str(credit["year"]) if credit["year"] else ""
            year_item = QTableWidgetItem(year_text)
            year_item.setFlags(year_item.flags() & ~Qt.ItemIsEditable)
            # Set data for proper sorting
            if credit["year"]:
                year_item.setData(Qt.EditRole, credit["year"])
            self.table_widget.setItem(row, 2, year_item)

        # Adjust column widths
        self.table_widget.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.table_widget.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.table_widget.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )

        # Add table to container
        self.table_container.layout().addWidget(self.table_widget)


class CreditsWidget(QWidget):
    """Credits section widget displaying all roles an artist is associated with"""

    def __init__(self, artist):
        super().__init__()
        self.artist = artist
        self.controller = None  # Will be set by parent
        self.role_sections = {}  # role_name -> RoleSection

        # Try to get controller from artist if available
        if hasattr(artist, "controller"):
            self.controller = artist.controller

        self.init_ui()

    def set_controller(self, controller):
        """Set the controller for database access"""
        self.controller = controller
        if self.controller:
            self.load_role_counts()

    def init_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)

        # Title
        title_label = QLabel("Credits")
        title_label.setObjectName("SectionTitle")
        self.layout.addWidget(title_label)

        # Container for role sections
        self.sections_container = QWidget()
        self.sections_layout = QVBoxLayout(self.sections_container)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(4)

        # Add to scroll area for many roles
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.sections_container)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setMaximumHeight(400)  # Limit height

        self.layout.addWidget(scroll_area)

        # Initially hide the entire widget
        self.setVisible(False)

    def load_role_counts(self):
        """Load role counts for the artist"""
        if not self.controller:
            logger.error("No controller available for CreditsWidget")
            return

        try:
            # Get all roles with counts for this artist
            # We'll query track_roles and album_roles separately and combine

            # Get track roles
            track_roles = self.controller.get.get_all_entities(
                "TrackArtistRole", artist_id=self.artist.artist_id
            )

            # Get album roles
            album_roles = self.controller.get.get_all_entities(
                "AlbumRoleAssociation", artist_id=self.artist.artist_id
            )

            # Count roles
            role_counts = {}

            # Count track roles
            for tr in track_roles:
                if hasattr(tr, "role"):
                    role_name = tr.role.role_name
                    role_counts[role_name] = role_counts.get(role_name, 0) + 1

            # Count album roles
            for ar in album_roles:
                if hasattr(ar, "role"):
                    role_name = ar.role.role_name
                    role_counts[role_name] = role_counts.get(role_name, 0) + 1

            # Create role sections sorted by count (highest first)
            if role_counts:
                sorted_roles = sorted(
                    role_counts.items(), key=lambda x: x[1], reverse=True
                )

                for role_name, count in sorted_roles:
                    section = RoleSection(
                        role_name, count, self.artist.artist_id, self.controller
                    )
                    self.sections_layout.addWidget(section)
                    self.role_sections[role_name] = section

                # Show the widget since we have credits
                self.setVisible(True)
            else:
                # No credits, keep widget hidden
                self.setVisible(False)

        except Exception as e:
            logger.error(f"Error loading role counts: {e}")
            self.setVisible(False)
