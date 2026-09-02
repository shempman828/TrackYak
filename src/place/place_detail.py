from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.place.place_edit import PlaceEditDialog


class PlaceDetailView(QDialog):
    """Dialog to display detailed information about a place. Show description and associations organized by type."""

    def __init__(self, controller, place, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.place = place
        self.setWindowTitle(f"Details for {place.place_name}")
        self.setModal(True)
        self.setMinimumSize(600, 500)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Place header
        header_label = QLabel(f"<h2>{self.place.place_name}</h2>")
        header_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(header_label)

        # Place type
        type_label = QLabel(
            f"<h3 style='color: #8599ea; text-align: center;'>{self.place.place_type}</h3>"
        )
        type_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(type_label)

        # Description card (only if not empty)
        if self.place.place_description:
            desc_group = QGroupBox("Description")
            desc_layout = QVBoxLayout()
            desc_label = QLabel(self.place.place_description)
            desc_label.setWordWrap(True)
            desc_layout.addWidget(desc_label)
            desc_group.setLayout(desc_layout)
            layout.addWidget(desc_group)

        # Parent place card (only if exists)
        if self.place.parent_id:
            parent_group = QGroupBox("Parent Place")
            parent_layout = QVBoxLayout()
            parent_place = self.controller.get.get_entity_object(
                "Place", place_id=self.place.parent_id
            )
            if parent_place:
                parent_text = f"{parent_place.place_name} ({parent_place.place_type})"
                parent_label = QLabel(parent_text)
                parent_layout.addWidget(parent_label)
            parent_group.setLayout(parent_layout)
            layout.addWidget(parent_group)

        # Coordinates information
        coords_group = QGroupBox("Location")
        coords_layout = QFormLayout()

        if self.place.place_latitude and self.place.place_longitude:
            coords_text = f"{self.place.place_latitude:.4f}, {self.place.place_longitude:.4f}"
        else:
            coords_text = "Not specified"

        coords_layout.addRow("Coordinates:", QLabel(coords_text))
        coords_group.setLayout(coords_layout)
        layout.addWidget(coords_group)

        # Action buttons
        button_layout = QHBoxLayout()

        edit_place_button = QPushButton("Edit Place")
        edit_place_button.clicked.connect(self.edit_place)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        button_layout.addWidget(edit_place_button)
        button_layout.addStretch()
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

    def get_entity_details(self, entity_type, entity_id):
        """Get the actual entity object based on type and ID."""
        try:
            if entity_type == "artist":
                return self.controller.get.get_entity_object("Artist", artist_id=entity_id)
            if entity_type == "track":
                return self.controller.get.get_entity_object("Track", track_id=entity_id)
            if entity_type == "album":
                return self.controller.get.get_entity_object("Album", album_id=entity_id)
            if entity_type == "publisher":
                return self.controller.get.get_entity_object("Publisher", publisher_id=entity_id)
            if entity_type == "playlist":
                return self.controller.get.get_entity_object("Playlist", playlist_id=entity_id)
        except SQLAlchemyError as e:
            logger.error(f"Error getting entity details: {e!s}")
            return None
        return None

    def get_entity_display_name(self, entity, entity_type):
        """Get display name for different entity types."""
        if entity_type == "artist" and hasattr(entity, "artist_name"):
            return entity.artist_name
        if entity_type == "track" and hasattr(entity, "track_name"):
            return entity.track_name
        if entity_type == "album" and hasattr(entity, "album_name"):
            return entity.album_name
        if entity_type == "publisher" and hasattr(entity, "publisher_name"):
            return entity.publisher_name
        if entity_type == "playlist" and hasattr(entity, "playlist_name"):
            return entity.playlist_name
        return f"Unknown {entity_type}"

    def edit_place(self):
        """Open the place edit dialog."""
        dialog = PlaceEditDialog(self.controller, self, self.place)
        if dialog.exec_() == QDialog.Accepted:
            # Refresh the dialog to show updated information
            self.accept()  # Close current dialog
            # The parent view should refresh itself through the existing mechanisms
