import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.layout_utils import clear_layout
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.publisher.publisher_albums import PublisherAlbumsWindow
from src.publisher.publisher_hierarchy import get_publisher_albums
from src.track.view.base_track_view import BaseTrackView


class PublisherDetailTab(QWidget):
    """Article-style detail view: an overview card, plus a places card
    that only appears when the publisher actually has places to show."""

    # Emitted after an album is edited from the "View Albums" popup, so the
    # owning publisher tree (whose per-node album counts are otherwise left
    # stale) can refresh itself.
    albums_changed = Signal()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_publisher = None
        self.init_ui()
        self.show_empty_state()

    def init_ui(self):
        """Initialize the card-based UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        self.scroll_layout.setSpacing(14)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.empty_state = QLabel("Select a publisher to view details")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.scroll_layout.addWidget(self.empty_state)

        self.info_card = self._build_info_card()
        self.scroll_layout.addWidget(self.info_card)
        self.info_card.hide()

        self.places_card = self._build_places_card()
        self.scroll_layout.addWidget(self.places_card)
        self.places_card.hide()

        self.scroll_layout.addStretch()

    def _build_info_card(self):
        """Create the publisher overview card: name, years/status, album
        count, description, and actions."""
        card = DetailCard("Overview")

        self.name_label = QLabel()
        self.name_label.setObjectName("PublisherName")
        self.name_label.setWordWrap(True)
        card.body.addWidget(self.name_label)

        # Years and active/inactive status folded into one line rather than
        # a separate status field: "1996-Current" or "1996-2008".
        self.years_label = QLabel()
        self.years_label.setObjectName("PublisherYears")
        card.body.addWidget(self.years_label)

        self.tracks_label = QLabel()
        self.tracks_label.setObjectName("PublisherMeta")
        card.body.addWidget(self.tracks_label, alignment=Qt.AlignLeft)

        self.description_label = QLabel()
        self.description_label.setObjectName("PublisherDescription")
        self.description_label.setWordWrap(True)
        card.body.addWidget(self.description_label)

        button_layout = QHBoxLayout()
        self.associations_btn = QPushButton("View Albums")
        self.associations_btn.clicked.connect(self._open_albums_window)
        self.tracks_button = QPushButton("View Tracks")
        self.tracks_button.clicked.connect(self.show_tracks)
        button_layout.addWidget(self.associations_btn)
        button_layout.addWidget(self.tracks_button)
        button_layout.addStretch()
        card.body.addLayout(button_layout)

        return card

    def _build_places_card(self):
        """Create the places card. Populated (and shown/hidden) by
        `_load_publisher_places`."""
        card = DetailCard("Places")
        self.places_layout = card.body
        return card

    def show_empty_state(self):
        """Show empty state when no publisher is selected."""
        self.empty_state.show()
        self.info_card.hide()
        self.places_card.hide()

    def show_detail_cards(self):
        """Show the overview card. The places card's visibility is decided
        by `_load_publisher_places` based on whether there's data for it."""
        self.empty_state.hide()
        self.info_card.show()

    def load_publisher_data(self, publisher_id):
        """Load and display publisher data."""
        try:
            publisher = self.controller.get.get_entity_object("Publisher", publisher_id=publisher_id)
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
            logger.error(f"Error loading publisher data: {e!s}")
            self.show_empty_state()

    def _open_albums_window(self):
        """Open a separate window showing all albums for this publisher."""
        if not self.current_publisher:
            return
        self._albums_window = PublisherAlbumsWindow(self.controller, self.current_publisher, self)
        self._albums_window.albums_changed.connect(self._on_albums_changed)
        self._albums_window.show()

    def _on_albums_changed(self):
        """Refresh this panel's album count after an edit in the albums popup."""
        if self.current_publisher:
            self._display_publisher_info(self.current_publisher)
        self.albums_changed.emit()

    def _display_publisher_info(self, publisher):
        """Update publisher information display."""
        name = html.escape(publisher.publisher_name)
        if publisher.second_pass:
            self.name_label.setText(f'{name} <span style="color:#43a047;">✓</span>')
        elif publisher.first_pass:
            self.name_label.setText(f'{name} <span style="color:#8599ea;">✓</span>')
        else:
            self.name_label.setText(publisher.publisher_name)

        self.years_label.setText(self._format_years(publisher))

        albums = get_publisher_albums(self.controller, publisher.publisher_id)
        album_count = len(albums)
        self.tracks_label.setText(f"{album_count} album{'s' if album_count != 1 else ''}")
        self.associations_btn.setText(f"View Albums ({album_count})")

        # Description
        desc = publisher.description or "No description available"
        self.description_label.setText(desc)

    @staticmethod
    def _format_years(publisher):
        """Fold active/inactive status into the year range instead of
        giving status its own field: "1996-Current", "1996-2008",
        "1996-" (inactive, no end year), or plain status if no years."""
        if not publisher.begin_year:
            return "Active" if publisher.is_active == 1 else "Inactive"
        if publisher.is_active == 1:
            return f"{publisher.begin_year}–Current"  # noqa: RUF001 (en-dash range separator)
        if publisher.end_year:
            return f"{publisher.begin_year}–{publisher.end_year}"  # noqa: RUF001 (en-dash range separator)
        return f"{publisher.begin_year}–"  # noqa: RUF001 (en-dash range separator)

    def _load_publisher_places(self, publisher_id):
        """Load associated places, each labeled with its association type
        (e.g. "Headquartered In: Nashville, TN"). Hides the card entirely
        when there are none."""
        clear_layout(self.places_layout)
        rows = []
        try:
            publisher_places = self.controller.get.get_all_entities("PlaceAssociation", entity_type="Publisher", entity_id=publisher_id)

            for place_assoc in publisher_places or []:
                place = self.controller.get.get_entity_object("Place", place_id=place_assoc.place_id)
                if not place:
                    continue
                type_name = place_assoc.association_type.type_name if place_assoc.association_type else "Associated"
                rows.append((type_name, place.place_name))

        except SQLAlchemyError as e:
            logger.error(f"Error loading places: {e!s}")

        for type_name, place_name in rows:
            label = QLabel(f"{type_name}: {place_name}")
            label.setObjectName("PlaceLabel")
            label.setWordWrap(True)
            self.places_layout.addWidget(label)

        self.places_card.setVisible(bool(rows))

    def show_tracks(self):
        """Show all tracks associated with this publisher using BaseTrackView."""
        if not self.current_publisher:
            return

        try:
            # Get all tracks associated with this publisher
            tracks = self._get_publisher_tracks()

            if not tracks:
                show_status_message(self, f"No tracks found for publisher: {self.current_publisher.publisher_name}")
                return

            # Create and show the track view dialog
            track_view = BaseTrackView(controller=self.controller, tracks=tracks, title=f"Tracks - {self.current_publisher.publisher_name}", enable_drag=True, enable_drop=False)

            # Set modal so user must close it before returning to main window
            track_view.setModal(True)

            # Adjust size if needed
            track_view.resize(1000, 700)

            # Show the dialog
            track_view.exec_()

        except SQLAlchemyError as e:
            logger.error(f"Error showing publisher tracks: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to load tracks:\n{e!s}")

    def _get_publisher_tracks(self):
        """Get all tracks associated with the current publisher."""
        if not self.current_publisher:
            return []

        tracks = []
        try:
            # Get all albums associated with this publisher (including child publishers)
            albums = get_publisher_albums(self.controller, self.current_publisher.publisher_id)

            for album in albums:
                # Get all tracks for this album using direct relationship
                # Tracks have a foreign key to album_id
                album_tracks = self.controller.get.get_all_entities("Track", album_id=album.album_id)

                for track in album_tracks:
                    # Get the full track object with relationships
                    track_full = self.controller.get.get_entity_object("Track", track_id=track.track_id)
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
            logger.error(f"Error fetching publisher tracks: {e!s}")
            return []
