# file: artist_detail_places.py
"""
PlacesWidget for displaying places associated with an artist.
"""

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from logger_config import logger


class PlaceCard(QFrame):
    """Widget for displaying information about a place"""

    place_clicked = Signal(dict)  # Signal emitted when place is clicked

    def __init__(self, place_data: dict):
        """
        Initialize a place card.

        Args:
            place_data: Dictionary containing place information with keys:
                - place_id: Place ID
                - place_name: Name of the place
                - place_type: Type of place (e.g., "City", "Country", "Venue")
                - place_description: Description of the place
                - association_type: How the artist is associated with this place
                - place_latitude: Latitude coordinate (optional)
                - place_longitude: Longitude coordinate (optional)
        """
        super().__init__()
        self.place_data = place_data

        self.setObjectName("PlaceCard")
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setLineWidth(1)
        self.setMinimumWidth(200)
        self.setMaximumWidth(300)
        self.setCursor(Qt.PointingHandCursor)

        # Set background color based on place type
        palette = self.palette()
        place_type = place_data.get("place_type", "").lower()

        if any(
            city_type in place_type for city_type in ["city", "town", "municipality"]
        ):
            palette.setColor(QPalette.Window, QColor(240, 248, 255))  # Light blue
            palette.setColor(QPalette.WindowText, QColor(25, 25, 112))  # MidnightBlue
        elif any(
            country_type in place_type
            for country_type in ["country", "nation", "state"]
        ):
            palette.setColor(QPalette.Window, QColor(255, 250, 240))  # FloralWhite
            palette.setColor(QPalette.WindowText, QColor(139, 0, 0))  # DarkRed
        elif any(
            venue_type in place_type
            for venue_type in ["venue", "studio", "hall", "theater"]
        ):
            palette.setColor(QPalette.Window, QColor(245, 245, 220))  # Beige
            palette.setColor(QPalette.WindowText, QColor(85, 107, 47))  # DarkOliveGreen
        else:
            # Default color for other place types
            palette.setColor(QPalette.Window, QColor(248, 248, 255))  # GhostWhite
            palette.setColor(QPalette.WindowText, QColor(47, 79, 79))  # DarkSlateGray

        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.init_ui()

    def init_ui(self):
        """Initialize the place card UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Place name - prominently displayed
        place_name = self.place_data.get("place_name", "Unknown Place")
        name_label = QLabel(place_name)
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(11)
        name_label.setFont(name_font)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setObjectName("PlaceName")
        layout.addWidget(name_label)

        # Place type
        place_type = self.place_data.get("place_type")
        if place_type:
            type_label = QLabel(f"📍 {place_type}")
            type_font = QFont()
            type_font.setPointSize(9)
            type_font.setItalic(True)
            type_label.setFont(type_font)
            type_label.setAlignment(Qt.AlignCenter)
            type_label.setObjectName("PlaceType")
            layout.addWidget(type_label)

        # Association type (how the artist is related to this place)
        association_type = self.place_data.get("association_type")
        if association_type:
            assoc_label = QLabel(f"🔗 {association_type}")
            assoc_font = QFont()
            assoc_font.setPointSize(9)
            assoc_label.setFont(assoc_font)
            assoc_label.setAlignment(Qt.AlignCenter)
            assoc_label.setObjectName("PlaceAssociation")
            layout.addWidget(assoc_label)

        # Place description (if available)
        description = self.place_data.get("place_description")
        if description:
            desc_label = QLabel(description)
            desc_font = QFont()
            desc_font.setPointSize(8)
            desc_label.setFont(desc_font)
            desc_label.setAlignment(Qt.AlignLeft)
            desc_label.setWordWrap(True)
            desc_label.setObjectName("PlaceDescription")
            layout.addWidget(desc_label)

        # Coordinates (if available)
        lat = self.place_data.get("place_latitude")
        lon = self.place_data.get("place_longitude")
        if lat is not None and lon is not None:
            coord_label = QLabel(f"🌐 {lat:.4f}, {lon:.4f}")
            coord_font = QFont()
            coord_font.setPointSize(8)
            coord_font.setFamily("Monospace")
            coord_label.setFont(coord_font)
            coord_label.setAlignment(Qt.AlignCenter)
            coord_label.setObjectName("PlaceCoordinates")
            layout.addWidget(coord_label)

    def mousePressEvent(self, event):
        """Handle click events on the place card"""
        if event.button() == Qt.LeftButton:
            # Emit signal with place data
            self.place_clicked.emit(self.place_data)

            # Visual feedback
            original_palette = self.palette()
            highlighted_palette = self.palette()
            highlighted_palette.setColor(QPalette.Window, QColor(220, 220, 220))
            self.setPalette(highlighted_palette)

            # Schedule a reset
            from PySide6.QtCore import QTimer

            QTimer.singleShot(200, lambda: self.setPalette(original_palette))

        super().mousePressEvent(event)


class PlacesWidget(QWidget):
    """Widget for displaying places associated with an artist"""

    add_place_requested = Signal()  # Signal to request adding a new place
    edit_place_requested = Signal(dict)  # Signal to edit a specific place
    view_place_requested = Signal(dict)  # Signal to view place details

    def __init__(self, artist: Any):
        super().__init__()
        self.artist = artist
        self.controller = None
        self.places_data = []

        # Try to get controller from artist if available
        if hasattr(artist, "controller"):
            self.controller = artist.controller

        self.init_ui()

    def set_controller(self, controller):
        """Set the controller for database access"""
        self.controller = controller
        if self.controller:
            self.load_places()

    def init_ui(self):
        """Initialize the widget UI"""
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)

        # Title and actions header
        header_layout = QHBoxLayout()

        # Title
        self.title_label = QLabel("Associated Places")
        self.title_label.setObjectName("SectionTitle")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(12)
        self.title_label.setFont(title_font)
        header_layout.addWidget(self.title_label)

        # Spacer
        header_layout.addStretch()

        # Add place button
        self.add_button = QToolButton()
        self.add_button.setText("+ Add Place")
        self.add_button.setToolTip("Add a new place association")
        self.add_button.clicked.connect(self.on_add_place)
        header_layout.addWidget(self.add_button)

        # Refresh button
        self.refresh_button = QToolButton()
        self.refresh_button.setText("↻")
        self.refresh_button.setToolTip("Refresh places")
        self.refresh_button.clicked.connect(self.load_places)
        header_layout.addWidget(self.refresh_button)

        self.layout.addLayout(header_layout)

        # Places container (initially hidden)
        self.places_container = QWidget()
        self.places_layout = QVBoxLayout(self.places_container)
        self.places_layout.setContentsMargins(0, 5, 0, 5)
        self.places_layout.setSpacing(10)
        self.places_layout.setAlignment(Qt.AlignTop)

        # Wrap in scroll area for many places
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.places_container)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setMaximumHeight(400)

        self.layout.addWidget(scroll_area)

        # No places label (shown when no places exist)
        self.no_places_label = QLabel("No places associated with this artist.")
        self.no_places_label.setAlignment(Qt.AlignCenter)
        self.no_places_label.setObjectName("NoPlacesLabel")
        no_places_font = QFont()
        no_places_font.setItalic(True)
        self.no_places_label.setFont(no_places_font)
        self.layout.addWidget(self.no_places_label)

        # Initially hide/show based on whether we have places
        self.update_visibility()

    def load_places(self):
        """Load places data from the database"""
        if not self.controller:
            logger.error("No controller available for PlacesWidget")
            return

        try:
            # Clear existing data
            self.places_data.clear()

            # Get place associations for this artist
            place_associations = self.controller.get.get_all_entities(
                "PlaceAssociation",
                entity_id=self.artist.artist_id,
                entity_type="Artist",
            )

            if not place_associations:
                # No places found
                self.places_data = []
                self.update_visibility()
                return

            # Process place associations data
            for association in place_associations:
                if hasattr(association, "place") and association.place:
                    place = association.place

                    place_data = {
                        "place_id": getattr(place, "place_id", None),
                        "place_name": getattr(place, "place_name", "Unknown Place"),
                        "place_type": getattr(place, "place_type", None),
                        "place_description": getattr(place, "place_description", None),
                        "place_latitude": getattr(place, "place_latitude", None),
                        "place_longitude": getattr(place, "place_longitude", None),
                        "association_id": getattr(association, "association_id", None),
                        "association_type": getattr(
                            association, "association_type", None
                        ),
                        "entity_id": getattr(association, "entity_id", None),
                        "entity_type": getattr(association, "entity_type", None),
                    }

                    self.places_data.append(place_data)

            # Sort by place type, then name
            self.places_data.sort(
                key=lambda x: (x.get("place_type", ""), x.get("place_name", ""))
            )

            # Display places
            self.display_places()

            # Update visibility
            self.update_visibility()

        except Exception as e:
            logger.error(f"Error loading places: {e}")
            self.places_data = []
            self.update_visibility()

    def display_places(self):
        """Display places in the layout"""
        if not self.places_data:
            return

        # Clear existing place cards
        while self.places_layout.count():
            item = self.places_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Group places by type
        places_by_type = {}
        for place in self.places_data:
            place_type = place.get("place_type", "Other")
            if place_type not in places_by_type:
                places_by_type[place_type] = []
            places_by_type[place_type].append(place)

        # Display places grouped by type
        for place_type, places in sorted(places_by_type.items()):
            # Add type header
            if place_type:
                type_header = QLabel(f"📌 {place_type}")
                type_font = QFont()
                type_font.setBold(True)
                type_font.setPointSize(11)
                type_header.setFont(type_font)
                type_header.setObjectName("PlaceTypeHeader")
                self.places_layout.addWidget(type_header)

            # Add place cards
            for place_data in places:
                place_card = PlaceCard(place_data)
                place_card.place_clicked.connect(self.on_place_clicked)
                self.places_layout.addWidget(place_card)

        # Update container size
        self.places_container.adjustSize()

    def update_visibility(self):
        """Update widget visibility based on whether we have places"""
        has_places = bool(self.places_data)

        # Show/hide the no places label
        self.no_places_label.setVisible(not has_places)

        # Show/hide the places container
        self.places_container.setVisible(has_places)

        # Show the entire widget only if we have places or actions are available
        self.setVisible(has_places or self.controller is not None)

    def get_places_summary(self) -> str:
        """Get a text summary of the associated places"""
        if not self.places_data:
            return "No associated places"

        # Count by place type
        type_counts = {}
        for place in self.places_data:
            place_type = place.get("place_type", "Other")
            type_counts[place_type] = type_counts.get(place_type, 0) + 1

        # Build summary string
        summary_parts = []
        for place_type, count in sorted(type_counts.items()):
            if place_type:
                summary_parts.append(f"{count} {place_type.lower()}")
            else:
                summary_parts.append(f"{count} places")

        return f"Associated with {', '.join(summary_parts)}"

    def on_place_clicked(self, place_data: dict):
        """Handle click on a place card"""
        # Create context menu for place actions
        menu = QMenu(self)

        # View details action
        view_action = QAction("🔍 View Details", self)
        view_action.triggered.connect(lambda: self.on_view_place(place_data))
        menu.addAction(view_action)

        # Edit place action
        edit_action = QAction("✏️ Edit Association", self)
        edit_action.triggered.connect(lambda: self.on_edit_place(place_data))
        menu.addAction(edit_action)

        # Remove association action
        remove_action = QAction("🗑️ Remove Association", self)
        remove_action.triggered.connect(lambda: self.on_remove_place(place_data))
        menu.addAction(remove_action)

        # Show menu at cursor position
        menu.exec(self.mapFromGlobal(self.cursor().pos()))

    def on_view_place(self, place_data: dict):
        """Handle view place request"""
        self.view_place_requested.emit(place_data)

    def on_edit_place(self, place_data: dict):
        """Handle edit place request"""
        self.edit_place_requested.emit(place_data)

    def on_remove_place(self, place_data: dict):
        """Handle remove place association request"""
        if not self.controller:
            logger.error("No controller available for removing place association")
            return

        try:
            # Remove the place association
            association_id = place_data.get("association_id")
            if association_id:
                self.controller.delete.delete_entity("PlaceAssociation", association_id)
                logger.info(f"Removed place association {association_id}")

                # Reload places
                self.load_places()

        except Exception as e:
            logger.error(f"Error removing place association: {e}")

    def on_add_place(self):
        """Handle add place request"""
        self.add_place_requested.emit()
