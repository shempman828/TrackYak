from pathlib import Path

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.view.base_track_view import BaseTrackView


class MissingTracks:
    """Scans the library for tracks whose file no longer exists on disk and shows them in a BaseTrackView dialog."""

    def __init__(self, controller, parent=None):
        self.controller = controller
        self.parent = parent

        missing = self.find_missing_tracks()
        self.show_missing_tracks(missing)

    def find_missing_tracks(self):
        """Return every Track entity whose track_file_path doesn't exist on disk."""
        all_tracks = self.controller.get.get_all_entities("Track")
        return [track for track in all_tracks if not track.track_file_path or not Path(track.track_file_path).exists()]

    def show_missing_tracks(self, missing):
        """Open a BaseTrackView dialog listing `missing`, or show a status message if there are none."""
        if not missing:
            logger.info("No missing tracks found")
            if self.parent is not None:
                show_status_message(self.parent, "No missing tracks found.")
            return None

        view = BaseTrackView(controller=self.controller, tracks=missing, title=f"Missing Tracks ({len(missing)})")
        view.exec()
        return view
