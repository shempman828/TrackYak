from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.hierarchy_tree_style import hierarchy_descendant_ids
from src.db.db_tables import MoodTrackAssociation, Track
from src.foundation.logger_config import logger
from src.track.view.base_track_view import BaseTrackView


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
        """Load and display tracks for the mood."""
        try:
            mood_ids = {self.mood.mood_id}
            mode_text = ""
            if self.show_recursive_tracks:
                # One Mood query + an in-memory BFS, not one query per child mood.
                all_moods = self.controller.get.get_all_entities("Mood")
                mood_ids |= hierarchy_descendant_ids(mood_ids, all_moods, id_attr="mood_id")
                mode_text = " (including all sub-moods)"

            # Filter in SQL with a subquery: one Track query instead of loading
            # every association plus one query per track, and no per-track
            # bound parameters to hit SQLite's variable limit on huge moods.
            tracks = self.controller.get.get_all_entities("Track", filter_expression=Track.track_id.in_(select(MoodTrackAssociation.track_id).where(MoodTrackAssociation.mood_id.in_(mood_ids))))

            self.tracks = tracks
            self.base_track_view.load_data(tracks)

            result_text = f"Found {len(tracks)} tracks{mode_text}"
            self.track_count_label.setText(result_text)

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading tracks for mood: {e!s}")
            self.track_count_label.setText("Error loading tracks")

    def closeEvent(self, event):
        """Handle window close event."""
        if hasattr(self, "base_track_view"):
            self.base_track_view.close()
        super().closeEvent(event)
