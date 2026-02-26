# file name: artist_detail_influences.py
"""
Influences widget showing artists influenced by and influencing the current artist.
"""

from typing import Any, List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from src.db_tables import Artist
from src.logger_config import logger


class InfluencesWidget(QWidget):
    """Widget displaying artist influences and influenced artists."""

    # Signal emitted when an artist name is clicked
    artist_clicked = Signal(int, str)  # artist_id, artist_name

    def __init__(self, artist: Artist, controller: Any = None):
        super().__init__()
        self.artist = artist
        self.controller = controller
        self.init_ui()

    def init_ui(self):
        """Initialize the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(15)

        # Influencers section (artists who influenced this artist)
        influencers_section = self.create_section("Influenced By")
        self.influencers_layout = QGridLayout()
        self.influencers_layout.setSpacing(5)
        influencers_section.layout().addLayout(self.influencers_layout)
        main_layout.addWidget(influencers_section)

        # Influenced section (artists influenced by this artist)
        influenced_section = self.create_section("Has Influenced")
        self.influenced_layout = QGridLayout()
        self.influenced_layout.setSpacing(5)
        influenced_section.layout().addLayout(self.influenced_layout)
        main_layout.addWidget(influenced_section)

        # Load the data
        self.load_influences_data()

    def create_section(self, title: str) -> QFrame:
        """Create a titled section frame."""
        section = QFrame()
        section.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        section.setLineWidth(1)

        layout = QVBoxLayout(section)
        layout.setContentsMargins(10, 10, 10, 10)

        title_label = QLabel(f"<b>{title}</b>")
        title_label.setStyleSheet("font-size: 12pt; margin-bottom: 5px;")
        layout.addWidget(title_label)

        return section

    def load_influences_data(self):
        """Load and display influences data."""
        try:
            # Clear existing content
            self.clear_layout(self.influencers_layout)
            self.clear_layout(self.influenced_layout)

            # Get influencers (artists who influenced this artist)
            influencers = self.artist.influenced_relations
            self.display_artist_list(
                self.influencers_layout,
                influencers,
                "influencer",
                "No artists listed as influences.",
            )

            # Get influenced artists (artists influenced by this artist)
            influenced = self.artist.influencer_relations
            self.display_artist_list(
                self.influenced_layout,
                influenced,
                "influenced",
                "This artist hasn't been listed as an influence for any artists.",
            )

        except Exception as e:
            logger.error(f"Error loading influences data: {e}")

    def display_artist_list(
        self,
        layout: QGridLayout,
        relations: List[Any],
        relation_type: str,
        empty_message: str,
    ):
        """Display a list of artists in a grid layout."""
        if not relations:
            empty_label = QLabel(f"<i>{empty_message}</i>")
            layout.addWidget(empty_label, 0, 0)
            return

        for i, relation in enumerate(relations):
            # Get the artist object based on relation type
            if relation_type == "influencer":
                artist = relation.influencer
                description = relation.description
            else:  # "influenced"
                artist = relation.influenced
                description = relation.description

            if not artist:
                continue

            # Create artist button
            artist_label = QLabel(artist.artist_name)
            layout.addWidget(artist_label)

            # Add description if available
            if description:
                desc_label = QLabel(f"<small><i>{description}</i></small>")
                desc_label.setWordWrap(True)
                layout.addWidget(desc_label)

    def clear_layout(self, layout):
        """Clear all widgets from a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def on_artist_clicked(self, artist: Artist):
        """Handle artist name click."""
        self.artist_clicked.emit(artist.artist_id, artist.artist_name)

    def refresh_data(self, artist: Artist = None):
        """Refresh the widget with new artist data."""
        if artist:
            self.artist = artist
        self.load_influences_data()
