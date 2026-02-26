from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from base_track_view import BaseTrackView
from logger_config import logger


class PlaylistTracksWindow(QMainWindow):
    """Independent window for managing playlist tracks using BaseTrackView."""

    def __init__(self, playlist_id: int, controller: Any, parent=None):
        super().__init__(parent)
        self.playlist_id = playlist_id
        self.controller = controller

        # Check if this is a smart playlist
        playlist = self.controller.get.get_entity_object(
            "Playlist", playlist_id=playlist_id
        )
        self.is_smart_playlist = getattr(playlist, "is_smart", False)
        self.playlist_name = getattr(
            playlist, "playlist_name", f"Playlist {playlist_id}"
        )

        # === Window setup ===
        window_title = f"Playlist Editor — {self.playlist_name}"
        if self.is_smart_playlist:
            window_title = f"🔍 Smart Playlist — {self.playlist_name}"
        self.setWindowTitle(window_title)

        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        self.resize(900, 700)
        self.setMinimumSize(600, 400)

        # === Central widget ===
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)

        # Add refresh button toolbar at the top
        self.create_toolbar()

        # Create BaseTrackView with drag/drop enabled based on playlist type
        enable_drag = not self.is_smart_playlist  # Allow dragging from smart playlists?
        enable_drop = (
            not self.is_smart_playlist
        )  # Only allow dropping on regular playlists

        self.tracks_view = BaseTrackView(
            controller=controller,
            tracks=[],  # Will load tracks in load_playlist_tracks()
            title=f"Tracks in {self.playlist_name}",
            enable_drag=enable_drag,
            enable_drop=enable_drop,
        )
        self.tracks_view.context_menu.addSeparator()
        self.remove_from_playlist_action = QAction("Remove from playlist", self)
        self.remove_from_playlist_action.triggered.connect(self.remove_selected_tracks)
        self.tracks_view.context_menu.addAction(self.remove_from_playlist_action)

        # Add refresh action to context menu
        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.triggered.connect(self.load_playlist_tracks)
        self.tracks_view.context_menu.addAction(self.refresh_action)

        # Override dropEvent for playlist-specific behavior
        if enable_drop:
            self.tracks_view.dropEvent = self.handle_drop

        self.main_layout.addWidget(self.tracks_view)

        # === Reference management ===
        if parent and hasattr(parent, "open_playlist_windows"):
            parent.open_playlist_windows[playlist_id] = self

        self.destroyed.connect(lambda: self.cleanup(parent))

        # Load tracks - force fresh load from database
        self.load_playlist_tracks()

        # Optional: restore last geometry
        if hasattr(controller, "settings"):
            geom = controller.settings.value(f"playlist_window_{playlist_id}_geometry")
            if geom:
                self.restoreGeometry(geom)

    def create_toolbar(self):
        """Create a simple toolbar with refresh button."""
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 10)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.load_playlist_tracks)
        self.refresh_button.setMaximumWidth(100)

        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addStretch()

        self.main_layout.addLayout(toolbar_layout)

    def load_playlist_tracks(self):
        """Load tracks for the current playlist - always fresh from database."""
        try:
            logger.debug(f"Loading fresh tracks for playlist {self.playlist_id}")

            # Don't clear the model directly - let load_data() handle it
            # Just ensure we have a clean state by calling load_data with empty list first
            # or let BaseTrackView handle the refresh

            # Get playlist track relationships with positions - fresh query
            playlist_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id=self.playlist_id
            )

            # Sort by position
            playlist_tracks.sort(key=lambda x: getattr(x, "position", 0))

            # Extract track objects
            tracks = []
            for playlist_track in playlist_tracks:
                if hasattr(playlist_track, "track") and playlist_track.track:
                    # Add position as a temporary attribute for display
                    track = playlist_track.track
                    track.position = getattr(playlist_track, "position", 0)
                    tracks.append(track)

            # Update the BaseTrackView with fresh data
            self.tracks_view.load_data(tracks)

            # Update info label
            self.tracks_view.info_label.setText(
                f"Showing {len(tracks)} tracks in playlist (Last updated: {datetime.now().strftime('%H:%M:%S')})"
            )

            logger.info(
                f"Loaded {len(tracks)} tracks for playlist {self.playlist_name}"
            )

        except Exception as e:
            logger.error(f"Error loading playlist tracks: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to load tracks: {str(e)}")

    def handle_drop(self, event):
        """Handle drop event to add tracks to playlist."""
        try:
            if event.mimeData().hasFormat("application/x-track-id"):
                # Get the comma-separated track IDs
                track_ids_data = (
                    event.mimeData().data("application/x-track-id").data().decode()
                )
                track_ids = [
                    int(tid.strip()) for tid in track_ids_data.split(",") if tid.strip()
                ]

                if not track_ids:
                    event.ignore()
                    return

                success_count = 0
                existing_tracks = 0

                # Get current tracks to determine next positions
                playlist_tracks = self.controller.get.get_all_entities(
                    "PlaylistTracks", playlist_id=self.playlist_id
                )
                current_positions = [
                    getattr(pt, "position", 0) for pt in playlist_tracks
                ]
                next_position = max(current_positions) + 1 if current_positions else 1

                for track_id in track_ids:
                    # Check if track already exists in playlist
                    existing = self.controller.get.get_all_entities(
                        "PlaylistTracks",
                        playlist_id=self.playlist_id,
                        track_id=track_id,
                    )

                    if existing:
                        existing_tracks += 1
                        continue

                    # Add track to playlist
                    success = self.controller.add.add_entity_link(
                        "PlaylistTracks",
                        playlist_id=self.playlist_id,
                        track_id=track_id,
                        position=next_position,
                        date_added=datetime.now(),
                    )

                    if success:
                        success_count += 1
                        next_position += 1

                # Refresh the view if any tracks were added
                if success_count > 0:
                    self.load_playlist_tracks()

                # Show feedback
                logger.info(
                    f"Added {success_count} tracks to playlist, {existing_tracks} already existed"
                )

                event.acceptProposedAction()
            else:
                event.ignore()

        except Exception as e:
            logger.error(f"Error handling drop in playlist: {str(e)}")
            event.ignore()

    def remove_selected_tracks(self):
        """Remove selected tracks from playlist - auto-saves immediately."""
        if self.is_smart_playlist:
            QMessageBox.information(
                self,
                "Smart Playlist",
                "Cannot remove tracks from smart playlists. Tracks are automatically managed based on criteria.",
            )
            return

        selected_tracks = self.tracks_view.get_selected_tracks()
        if not selected_tracks:
            return

        try:
            removed_count = 0

            for track in selected_tracks:
                success = self.controller.delete.delete_entity(
                    "PlaylistTracks",
                    playlist_id=self.playlist_id,
                    track_id=track.track_id,
                )
                if success:
                    removed_count += 1

            # Refresh the view - changes are automatically saved
            self.load_playlist_tracks()
            logger.info(f"Removed {removed_count} tracks from playlist")

        except Exception as e:
            logger.error(f"Error removing tracks: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to remove tracks: {str(e)}")

    def cleanup(self, parent):
        """Clean up references when window is closed."""
        if parent and hasattr(parent, "open_playlist_windows"):
            parent.open_playlist_windows.pop(self.playlist_id, None)
        if hasattr(self.controller, "settings"):
            self.controller.settings.setValue(
                f"playlist_window_{self.playlist_id}_geometry", self.saveGeometry()
            )

    # Optional: remember manual move/resize
    def moveEvent(self, event):
        if hasattr(self.controller, "settings"):
            self.controller.settings.setValue(
                f"playlist_window_{self.playlist_id}_pos", self.pos()
            )
        super().moveEvent(event)

    def resizeEvent(self, event):
        if hasattr(self.controller, "settings"):
            self.controller.settings.setValue(
                f"playlist_window_{self.playlist_id}_size", self.size()
            )
        super().resizeEvent(event)
