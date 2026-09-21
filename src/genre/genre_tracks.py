from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.track.view.base_track_view import BaseTrackView


class GenreTracksWindow(QDialog):
    """Window to display tracks for a genre with recursive toggle."""

    def __init__(self, controller, genre, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.genre = genre
        self.show_recursive_tracks = False
        self.tracks = []
        self.setup_ui()
        self.load_tracks()

    def setup_ui(self):
        """Initialize the tracks view UI."""
        self.setWindowTitle(f"Tracks for: {self.genre.genre_name}")
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)

        # Controls row
        controls_layout = QHBoxLayout()

        # Recursive toggle button
        self.recursive_toggle = QPushButton("Show Recursive Tracks: OFF")
        self.recursive_toggle.setCheckable(True)
        self.recursive_toggle.clicked.connect(self.toggle_recursive)
        controls_layout.addWidget(self.recursive_toggle)

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.load_tracks)
        controls_layout.addWidget(self.refresh_button)

        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        # Track count label
        self.track_count_label = QLabel()
        layout.addWidget(self.track_count_label)

        # Create BaseTrackView
        self.base_track_view = BaseTrackView(controller=self.controller, tracks=self.tracks, title="")
        layout.addWidget(self.base_track_view)

    def toggle_recursive(self):
        """Toggle recursive track display."""
        self.show_recursive_tracks = not self.show_recursive_tracks
        if self.show_recursive_tracks:
            self.recursive_toggle.setText("Show Recursive Tracks: ON")
        else:
            self.recursive_toggle.setText("Show Recursive Tracks: OFF")
        self.load_tracks()

    def load_tracks(self):
        """Load and display tracks for the genre."""
        try:
            if self.show_recursive_tracks:
                genre_ids = self._get_all_descendant_genre_ids(self.genre.genre_id)
                mode_text = " (including all sub-genres)"
            else:
                genre_ids = [self.genre.genre_id]
                mode_text = ""

            track_genres = self.controller.get.get_all_entities("TrackGenre", genre_id__in=genre_ids)
            track_ids = list({tg.track_id for tg in track_genres})
            tracks = self.controller.get.get_all_entities("Track", track_id__in=track_ids) if track_ids else []

            # Update the BaseTrackView with the loaded tracks
            self.tracks = tracks
            self.base_track_view.load_data(tracks)

            # Update track count
            result_text = f"Found {len(tracks)} tracks{mode_text}"
            self.track_count_label.setText(result_text)

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading tracks: {e!s}")
            self.track_count_label.setText("Error loading tracks")

    def _get_all_descendant_genre_ids(self, genre_id, _visited=None):
        """Return `genre_id` plus every descendant genre ID, recursively."""
        _visited = _visited or set()
        if genre_id in _visited:
            # Cyclic parent_id chain (shouldn't happen); stop descending
            # instead of recursing forever.
            return []
        _visited = _visited | {genre_id}

        genre_ids = [genre_id]
        child_genres = self.controller.get.get_all_entities("Genre", parent_id=genre_id)
        for child in child_genres:
            genre_ids.extend(self._get_all_descendant_genre_ids(child.genre_id, _visited))
        return genre_ids

    def closeEvent(self, event):
        """Handle window close event."""
        # Close the base track view properly
        if hasattr(self, "base_track_view"):
            self.base_track_view.close()
        super().closeEvent(event)
