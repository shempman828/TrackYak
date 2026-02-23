from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from base_track_view import BaseTrackView
from logger_config import logger


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
        self.base_track_view = BaseTrackView(
            controller=self.controller, tracks=self.tracks, title=""
        )
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
            logger.debug(
                f"=== Starting track load for genre: {self.genre.genre_name} (ID: {self.genre.genre_id}) ==="
            )
            logger.debug(f"Recursive mode: {self.show_recursive_tracks}")

            if self.show_recursive_tracks:
                # Get all descendant genre IDs including current genre
                genre_ids = self._get_all_descendant_genre_ids(self.genre.genre_id)
                logger.debug(f"Recursive genre IDs found: {genre_ids}")

                # Get track-genre associations for these genre IDs
                track_genres = self.controller.get.get_all_entities("TrackGenre")
                logger.debug(f"Total track-genre associations: {len(track_genres)}")

                # Filter to only associations with our target genres
                matching_associations = [
                    tg for tg in track_genres if tg.genre_id in genre_ids
                ]

                logger.debug(
                    f"Matching track-genre associations: {len(matching_associations)}"
                )

                track_ids = list(set([tg.track_id for tg in matching_associations]))
                logger.debug(f"Unique track IDs: {track_ids}")

                # Get the actual tracks - handle list parameter properly
                if track_ids:
                    # Query tracks one by one to avoid the list parameter issue
                    tracks = []
                    for track_id in track_ids:
                        track = self.controller.get.get_entity_object(
                            "Track", track_id=track_id
                        )
                        if track:
                            tracks.append(track)
                else:
                    tracks = []
                mode_text = " (including all sub-genres)"

            else:
                # Get track-genre associations for this specific genre
                track_genres = self.controller.get.get_all_entities("TrackGenre")
                logger.debug(f"Total track-genre associations: {len(track_genres)}")

                matching_associations = [
                    tg for tg in track_genres if tg.genre_id == self.genre.genre_id
                ]
                logger.debug(
                    f"Matching track-genre associations: {len(matching_associations)}"
                )

                track_ids = [tg.track_id for tg in matching_associations]
                logger.debug(f"Track IDs: {track_ids}")

                # Get the actual tracks - handle list parameter properly
                if track_ids:
                    # Query tracks one by one to avoid the list parameter issue
                    tracks = []
                    for track_id in track_ids:
                        track = self.controller.get.get_entity_object(
                            "Track", track_id=track_id
                        )
                        if track:
                            tracks.append(track)
                else:
                    tracks = []
                mode_text = ""

            logger.debug(f"Found {len(tracks)} tracks to display")

            # Update the BaseTrackView with the loaded tracks
            self.tracks = tracks
            self.base_track_view.load_data(tracks)

            # Update track count
            result_text = f"Found {len(tracks)} tracks{mode_text}"
            self.track_count_label.setText(result_text)
            logger.debug(f"=== Track load completed: {result_text} ===")

        except Exception as e:
            logger.error(f"Error loading tracks: {str(e)}")
            logger.exception("Full traceback:")
            self.track_count_label.setText("Error loading tracks")

    def _get_all_descendant_genre_ids(self, genre_id):
        """Helper method to get all descendant genre IDs recursively."""
        logger.debug(f"Getting descendants for genre ID: {genre_id}")
        genre_ids = [genre_id]

        # Get direct children
        child_genres = self.controller.get.get_all_entities("Genre", parent_id=genre_id)
        logger.debug(f"Found {len(child_genres)} direct children for genre {genre_id}")

        # Recursively get descendants
        for child in child_genres:
            logger.debug(f"Processing child: {child.genre_name} (ID: {child.genre_id})")
            child_descendants = self._get_all_descendant_genre_ids(child.genre_id)
            genre_ids.extend(child_descendants)

        logger.debug(f"Total descendants for genre {genre_id}: {genre_ids}")
        return genre_ids

    def closeEvent(self, event):
        """Handle window close event."""
        # Close the base track view properly
        if hasattr(self, "base_track_view"):
            self.base_track_view.close()
        super().closeEvent(event)
