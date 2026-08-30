from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.track.base_track_view import BaseTrackView


class MoodTracksWindow(QDialog):
    """Window to display tracks for a mood with recursive toggle."""

    def __init__(self, controller, mood, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mood = mood
        self.show_recursive_tracks = False
        self.tracks = []
        self.setup_ui()
        self.load_tracks()

    def setup_ui(self):
        """Initialize the tracks view UI."""
        self.setWindowTitle(f"Tracks for: {self.mood.mood_name}")
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
        """Load and display tracks for the mood."""
        try:
            if self.show_recursive_tracks:
                mood_ids = self._get_all_descendant_mood_ids(self.mood.mood_id)
                mode_text = " (including all sub-moods)"
            else:
                mood_ids = [self.mood.mood_id]
                mode_text = ""

            associations = self.controller.get.get_all_entities("MoodTrackAssociation")
            matching_associations = [a for a in associations if a.mood_id in mood_ids]

            track_ids = list({a.track_id for a in matching_associations})

            tracks = []
            for track_id in track_ids:
                track = self.controller.get.get_entity_object("Track", track_id=track_id)
                if track:
                    tracks.append(track)

            self.tracks = tracks
            self.base_track_view.load_data(tracks)

            result_text = f"Found {len(tracks)} tracks{mode_text}"
            self.track_count_label.setText(result_text)

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading tracks for mood: {e!s}")
            self.track_count_label.setText("Error loading tracks")

    def _get_all_descendant_mood_ids(self, mood_id):
        """Helper method to get a mood ID plus all of its descendant mood IDs."""
        mood_ids = [mood_id]

        child_moods = self.controller.get.get_all_entities("Mood", parent_id=mood_id)
        for child in child_moods:
            mood_ids.extend(self._get_all_descendant_mood_ids(child.mood_id))

        return mood_ids

    def closeEvent(self, event):
        """Handle window close event."""
        if hasattr(self, "base_track_view"):
            self.base_track_view.close()
        super().closeEvent(event)
