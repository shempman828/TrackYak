import os

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.base_track_view import BaseTrackView


class MissingTracks:
    def __init__(self, controller, parent=None):
        self.controller = controller
        self.parent = parent

        missing = self.find_missing_tracks()
        self.show_missing_tracks(missing)

    def find_missing_tracks(self):
        """
        Scans all Track entities in the library and returns those whose
        track_file_path does not exist on disk.

        Args:
            controller: The main application controller.

        Returns:
            List of Track objects with missing file paths.
        """
        all_tracks = self.controller.get.get_all_entities("Track")
        missing = [
            track
            for track in all_tracks
            if not track.track_file_path or not os.path.exists(track.track_file_path)
        ]
        return missing

    def show_missing_tracks(self, missing):
        """
        Finds all tracks with missing file paths and opens a BaseTrackView dialog
        to display them.

        Args:
            controller: The main application controller.
            parent: Optional parent widget for the dialog.

        Returns:
            The BaseTrackView dialog instance, or None if no missing tracks were found.
        """

        if not missing:
            logger.info("No missing tracks found")
            if self.parent is not None:
                show_status_message(self.parent, "No missing tracks found.")
            return None

        view = BaseTrackView(
            controller=self.controller, tracks=missing, title=f"Missing Tracks ({len(missing)})"
        )
        view.exec()
        return view
