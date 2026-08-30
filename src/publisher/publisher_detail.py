import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.core.asset_paths import icon
from src.track.base_track_view import BaseTrackView
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.publisher.publisher_albums import PublisherAlbumsWindow
from src.publisher.publisher_hierarchy import get_publisher_albums


class PublisherDetailTab(QWidget):
    """Modern detail view with card-based layout."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_publisher = None
        self.init_ui()
        self.show_empty_state()

    def init_ui(self):
        """Initialize modern card-based UI."""
        layout = QVBoxLayout(self)

        # Main scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        # Empty state widget
        self.empty_state = QLabel("Select a publisher to view details")
        self.empty_state.setAlignment(Qt.AlignCenter)

        self.scroll_layout.addWidget(self.empty_state)

        self.info_card = self.create_info_card()
        self.places_card = self.create_places_card()

        # Initially hide detail cards
        self.info_card.hide()
        self.places_card.hide()

    def create_info_card(self):
        """Create publisher information card."""
        card = QFrame()
        card.setFrameStyle(QFrame.StyledPanel)

        layout = QVBoxLayout(card)

        # Header with logo and basic info
        header_layout = QHBoxLayout()

        # Logo
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(120, 120)

        header_layout.addWidget(self.logo_label)

        # Basic info
        info_layout = QVBoxLayout()
        self.name_label = QLabel()

        info_layout.addWidget(self.name_label)

        # Status and years
        status_layout = QHBoxLayout()
        self.status_label = QLabel()
        self.years_label = QLabel()
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(QLabel("•"))
        status_layout.addWidget(self.years_label)
        status_layout.addStretch()
        info_layout.addLayout(status_layout)

        # Track count
        self.tracks_label = QLabel()
        info_layout.addWidget(self.tracks_label)

        info_layout.addStretch()
        header_layout.addLayout(info_layout)
        layout.addLayout(header_layout)

        # Description
        layout.addWidget(QLabel("Description:"))
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)

        layout.addWidget(self.description_label)

        # Action buttons
        button_layout = QHBoxLayout()
        self.associations_btn = QPushButton("View Albums")
        self.associations_btn.clicked.connect(self._open_albums_window)
        self.tracks_button = QPushButton("View Tracks")
        self.tracks_button.clicked.connect(self.show_tracks)

        button_layout.addWidget(self.associations_btn)
        button_layout.addWidget(self.tracks_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        return card

    def create_places_card(self):
        """Create places association card."""
        card = QFrame()
        card.setFrameStyle(QFrame.StyledPanel)

        layout = QVBoxLayout(card)
        layout.addWidget(QLabel("Associated Places"))

        self.places_list = QListWidget()
        layout.addWidget(self.places_list)

        return card

    def show_empty_state(self):
        """Show empty state when no publisher is selected."""
        self.empty_state.show()

        # Remove cards from layout if they exist
        for card in [self.info_card, self.places_card]:
            if card.parent() == self.scroll_content:
                self.scroll_layout.removeWidget(card)
                card.hide()

    def show_detail_cards(self):
        """Show all detail cards in the correct order."""
        self.empty_state.hide()

        # Clear existing widgets (except empty state)
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget and widget != self.empty_state:
                self.scroll_layout.removeWidget(widget)
                widget.hide()

        # Add cards in order
        self.scroll_layout.addWidget(self.info_card)
        self.info_card.show()

        self.scroll_layout.addWidget(self.places_card)
        self.places_card.show()

        # Add stretch to push content to top
        self.scroll_layout.addStretch()

    def load_publisher_data(self, publisher_id):
        """Load and display publisher data."""
        try:
            publisher = self.controller.get.get_entity_object(
                "Publisher", publisher_id=publisher_id
            )
            if not publisher:
                self.show_empty_state()
                return

            self.current_publisher = publisher
            self.show_detail_cards()

            # Update info card
            self._display_publisher_info(publisher)

            # Load places
            self._load_publisher_places(publisher_id)

        except SQLAlchemyError as e:
            logger.error(f"Error loading publisher data: {str(e)}")
            self.show_empty_state()

    def _open_albums_window(self):
        """Open a separate window showing all albums for this publisher."""
        if not self.current_publisher:
            return
        self._albums_window = PublisherAlbumsWindow(
            self.controller, self.current_publisher, self
        )
        self._albums_window.show()

    def _display_publisher_info(self, publisher):
        """Update publisher information display."""
        name = html.escape(publisher.publisher_name)
        if publisher.second_pass:
            self.name_label.setText(f'{name} <span style="color:#43a047;">✓</span>')
        elif publisher.first_pass:
            self.name_label.setText(f'{name} <span style="color:#8599ea;">✓</span>')
        else:
            self.name_label.setText(publisher.publisher_name)

        # Status and years
        status = "Active" if publisher.is_active == 1 else "Inactive"
        self.status_label.setText(f"Status: {status}")

        years_text = ""
        if publisher.begin_year:
            years_text += str(publisher.begin_year)
        if publisher.end_year:
            years_text += f" - {publisher.end_year}"
        self.years_label.setText(years_text or "Years not specified")

        albums = get_publisher_albums(self.controller, publisher.publisher_id)
        album_count = len(albums)
        self.tracks_label.setText(f"Albums: {album_count}")
        self.associations_btn.setText(f"View Albums ({album_count})")

        # Description
        desc = publisher.description or "No description available"
        self.description_label.setText(desc)

        # Logo
        self._display_logo(publisher.logo_path)

    def _display_logo(self, logo_path):
        """Display publisher logo."""
        if logo_path and Path(logo_path).exists():
            pixmap = QPixmap(logo_path)
        else:
            pixmap = icon("default_logo.svg").pixmap(120, 120)

        scaled_pixmap = pixmap.scaled(
            120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.logo_label.setPixmap(scaled_pixmap)

    def _load_publisher_places(self, publisher_id):
        """Load and display associated places."""
        self.places_list.clear()
        try:
            # CORRECT: Get PlaceAssociation entities with proper filtering
            publisher_places = self.controller.get.get_all_entities(
                "PlaceAssociation", entity_type="Publisher", entity_id=publisher_id
            )

            if not publisher_places:
                self.places_list.addItem("No places associated")
                return

            for place_assoc in publisher_places:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=place_assoc.place_id
                )
                if place:
                    item = QListWidgetItem(place.place_name)
                    self.places_list.addItem(item)

        except SQLAlchemyError as e:
            logger.error(f"Error loading places: {str(e)}")
            self.places_list.addItem("Error loading places")

    def show_tracks(self):
        """Show all tracks associated with this publisher using BaseTrackView."""
        if not self.current_publisher:
            return

        try:
            # Get all tracks associated with this publisher
            tracks = self._get_publisher_tracks()

            if not tracks:
                show_status_message(
                    self,
                    f"No tracks found for publisher: {self.current_publisher.publisher_name}",
                )
                return

            # Create and show the track view dialog
            track_view = BaseTrackView(
                controller=self.controller,
                tracks=tracks,
                title=f"Tracks - {self.current_publisher.publisher_name}",
                enable_drag=True,
                enable_drop=False,
            )

            # Set modal so user must close it before returning to main window
            track_view.setModal(True)

            # Adjust size if needed
            track_view.resize(1000, 700)

            # Show the dialog
            track_view.exec_()

        except SQLAlchemyError as e:
            logger.error(f"Error showing publisher tracks: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to load tracks:\n{str(e)}")

    def _get_publisher_tracks(self):
        """Get all tracks associated with the current publisher."""
        if not self.current_publisher:
            return []

        tracks = []
        try:
            # Get all albums associated with this publisher (including child publishers)
            albums = get_publisher_albums(
                self.controller, self.current_publisher.publisher_id
            )

            for album in albums:
                # Get all tracks for this album using direct relationship
                # Tracks have a foreign key to album_id
                album_tracks = self.controller.get.get_all_entities(
                    "Track", album_id=album.album_id
                )

                for track in album_tracks:
                    # Get the full track object with relationships
                    track_full = self.controller.get.get_entity_object(
                        "Track", track_id=track.track_id
                    )
                    if track_full:
                        tracks.append(track_full)

            # Remove duplicates (in case tracks appear in multiple albums)
            # Create a dictionary with track_id as key to remove duplicates
            unique_tracks = {}
            for track in tracks:
                if track.track_id not in unique_tracks:
                    unique_tracks[track.track_id] = track

            return list(unique_tracks.values())

        except SQLAlchemyError as e:
            logger.error(f"Error fetching publisher tracks: {str(e)}")
            return []
