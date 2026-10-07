"""Stand-alone window that lists and edits one playlist's tracks."""

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMessageBox, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.db.db_tables import PlaylistTracks
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.view.base_track_view import BaseTrackView


class PlaylistTracksWindow(QMainWindow):
    """Independent window for managing playlist tracks using BaseTrackView."""

    # Emitted with the playlist_id after tracks are added or removed.
    tracks_changed = Signal(int)

    def __init__(self, playlist_id: int, controller: Any, parent=None):
        """Build the window for `playlist_id` and load its tracks."""
        super().__init__(parent)
        self.playlist_id = playlist_id
        self.controller = controller
        # Free the window on close; the owning view drops its reference on destroyed.
        self.setAttribute(Qt.WA_DeleteOnClose)

        # Check if this is a smart playlist
        playlist = self.controller.get.get_entity_object("Playlist", playlist_id=playlist_id)
        if playlist is None:
            logger.warning(f"Playlist {playlist_id} not found while opening its track window")
        self.is_smart_playlist = bool(getattr(playlist, "is_smart", False))
        self.playlist_name = getattr(playlist, "playlist_name", f"Playlist {playlist_id}")

        # === Window setup ===
        window_title = f"Playlist Editor — {self.playlist_name}"
        if self.is_smart_playlist:
            window_title = f"🔍 Smart Playlist — {self.playlist_name}"
        self.setWindowTitle(window_title)

        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(900, 700)
        self.setMinimumSize(600, 400)

        # === Central widget ===
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)

        # Smart playlist membership comes from its criteria, so its tracks
        # can't be dragged in or out by hand.
        editable = not self.is_smart_playlist

        self.tracks_view = BaseTrackView(controller=controller, tracks=[], title=f"Tracks in {self.playlist_name}", enable_drag=editable, enable_drop=editable)
        self.tracks_view.context_menu.addSeparator()
        if editable:
            self.remove_from_playlist_action = QAction("Remove from playlist", self)
            self.remove_from_playlist_action.triggered.connect(self.remove_selected_tracks)
            self.tracks_view.context_menu.addAction(self.remove_from_playlist_action)

        # Add refresh action to context menu
        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.triggered.connect(self.load_playlist_tracks)
        self.tracks_view.context_menu.addAction(self.refresh_action)

        # Override dropEvent for playlist-specific behavior
        if editable:
            self.tracks_view.dropEvent = self.handle_drop

        self.main_layout.addWidget(self.tracks_view)

        self.load_playlist_tracks()

        if hasattr(controller, "settings"):
            geom = controller.settings.value(self._geometry_key())
            if geom:
                self.restoreGeometry(geom)

    def _geometry_key(self) -> str:
        """Return the QSettings key that stores this window's geometry."""
        return f"playlist_window_{self.playlist_id}_geometry"

    def load_playlist_tracks(self):
        """Load the playlist's tracks from the database, in position order."""
        try:
            logger.debug(f"Loading fresh tracks for playlist {self.playlist_id}")

            # Eager-load each row's Track instead of one lazy query per row.
            playlist_tracks = self.controller.get.get_all_entities("PlaylistTracks", load_options=[selectinload(PlaylistTracks.track)], playlist_id=self.playlist_id)
            playlist_tracks.sort(key=lambda x: getattr(x, "position", 0) or 0)

            tracks = []
            for playlist_track in playlist_tracks:
                track = getattr(playlist_track, "track", None)
                if track:
                    # Add position as a temporary attribute for display
                    track.position = getattr(playlist_track, "position", 0)
                    tracks.append(track)

            self.tracks_view.load_data(tracks)

            last_updated = datetime.now().strftime("%H:%M:%S")
            self.tracks_view.info_label.setText(f"Showing {len(tracks)} tracks in playlist (Last updated: {last_updated})")

            logger.info(f"Loaded {len(tracks)} tracks for playlist {self.playlist_name}")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading playlist tracks: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to load tracks: {e!s}")

    def handle_drop(self, event):
        """Append dropped tracks to the end of the playlist, skipping ones already in it."""
        try:
            if not event.mimeData().hasFormat("application/x-track-id"):
                event.ignore()
                return

            # Get the comma-separated track IDs
            track_ids_data = event.mimeData().data("application/x-track-id").data().decode()
            track_ids = [int(tid.strip()) for tid in track_ids_data.split(",") if tid.strip()]

            if not track_ids:
                event.ignore()
                return

            success_count = 0
            existing_tracks = 0

            # Get current tracks once -- both to determine the next
            # position and to check for duplicates without a query per
            # dropped track.
            playlist_tracks = self.controller.get.get_all_entities("PlaylistTracks", playlist_id=self.playlist_id)
            current_positions = [getattr(pt, "position", 0) or 0 for pt in playlist_tracks]
            next_position = max(current_positions, default=0) + 1
            existing_track_ids = {pt.track_id for pt in playlist_tracks}

            for track_id in track_ids:
                if track_id in existing_track_ids:
                    existing_tracks += 1
                    continue

                success = self.controller.add.add_entity_link("PlaylistTracks", playlist_id=self.playlist_id, track_id=track_id, position=next_position, date_added=datetime.now())

                if success:
                    success_count += 1
                    next_position += 1
                    existing_track_ids.add(track_id)

            if success_count > 0:
                self.load_playlist_tracks()
                self.tracks_changed.emit(self.playlist_id)

            logger.info(f"Added {success_count} tracks to playlist, {existing_tracks} already existed")
            message = f"Added {success_count} track(s)."
            if existing_tracks:
                message += f" {existing_tracks} already in playlist."
            show_status_message(self, message)

            event.acceptProposedAction()

        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error handling drop in playlist: {e!s}")
            event.ignore()

    def remove_selected_tracks(self):
        """Remove the selected tracks from the playlist; changes save immediately."""
        if self.is_smart_playlist:
            # The action is not offered for smart playlists; guard direct calls.
            return

        selected_tracks = self.tracks_view.get_selected_tracks()
        if not selected_tracks:
            return

        try:
            removed_count = 0

            for track in selected_tracks:
                success = self.controller.delete.delete_entity("PlaylistTracks", playlist_id=self.playlist_id, track_id=track.track_id)
                if success:
                    removed_count += 1

            self.load_playlist_tracks()
            if removed_count:
                self.tracks_changed.emit(self.playlist_id)
            logger.info(f"Removed {removed_count} tracks from playlist")

        except SQLAlchemyError as e:
            logger.error(f"Error removing tracks: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to remove tracks: {e!s}")

    def closeEvent(self, event):
        """Save the window geometry before the window closes."""
        if hasattr(self.controller, "settings"):
            self.controller.settings.setValue(self._geometry_key(), self.saveGeometry())
        super().closeEvent(event)
