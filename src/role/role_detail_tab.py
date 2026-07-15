from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from src.track.base_track_view import BaseTrackView
from src.core.logger_config import logger


class RoleDetailTab(QWidget):
    """Detailed view for a specific role's artist relationships.

    Roles can be assigned to albums (AlbumRoleAssociation) and/or tracks
    (TrackArtistRole) independently, so both are always loaded and merged
    per-artist rather than picking one based on a "role type".
    """

    def __init__(self, controller, role_id):
        super().__init__()
        self.controller = controller
        self.role_id = role_id
        self.artist_data_map = {}
        self._setup_ui()
        self._load_data()
        logger.info(f"Detail tab created for role {role_id}")

    def _setup_ui(self):
        """Initialize UI components for the detail view."""
        layout = QVBoxLayout(self)

        # Add role type indicator
        self.type_label = QLabel("Role Details")
        self.type_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self.type_label)

        self.artist_list = QListWidget()
        # Enable context menu
        self.artist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artist_list.customContextMenuRequested.connect(
            self.show_artist_context_menu
        )

        layout.addWidget(self.artist_list)

    def show_artist_context_menu(self, position):
        """Show context menu for artist list items."""
        item = self.artist_list.itemAt(position)
        if not item:
            return

        # Get the artist ID from the item's data
        artist_id = item.data(Qt.UserRole)
        if not artist_id:
            return

        # Create context menu
        menu = QMenu(self)

        # Add "View Tracks" action
        view_tracks_action = QAction("View Tracks", self)
        view_tracks_action.triggered.connect(lambda: self.view_artist_tracks(artist_id))
        menu.addAction(view_tracks_action)

        # Show menu
        menu.exec_(self.artist_list.mapToGlobal(position))

    def view_artist_tracks(self, artist_id):
        """Show tracks for the selected artist in this role."""
        try:
            tracks = self._get_artist_tracks_for_role(artist_id)
            if not tracks:
                logger.info(
                    f"No tracks found for artist {artist_id} in role {self.role_id}"
                )
                return

            # Get artist name for window title
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
            artist_name = artist.artist_name if artist else f"Artist {artist_id}"
            role_name = self._get_role_name()

            # Create and show the BaseTrackView
            track_view = BaseTrackView(
                controller=self.controller,
                tracks=tracks,
                title=f"Tracks by {artist_name} as {role_name}",
                enable_drag=True,
                enable_drop=False,
            )
            track_view.exec_()

            logger.info(
                f"Showing {len(tracks)} tracks for artist {artist_name} in role {role_name}"
            )

        except Exception as e:
            logger.error(
                f"Error showing tracks for artist {artist_id}: {e}", exc_info=True
            )

    def _get_artist_tracks_for_role(self, artist_id):
        """Get all tracks for an artist in the current role, from both album and track assignments."""
        tracks = []
        seen_track_ids = set()

        try:
            # Album roles: get albums where artist has this role, then get tracks from those albums
            album_links = (
                self.controller.get.get_all_entities(
                    "AlbumRoleAssociation",
                    role_id=self.role_id,
                    artist_id=artist_id,
                )
                or []
            )

            for link in album_links:
                album_tracks = (
                    self.controller.get.get_entity_links(
                        "AlbumTracks", album_id=link.album_id
                    )
                    or []
                )

                for album_track in album_tracks:
                    if album_track.track_id in seen_track_ids:
                        continue
                    track = self.controller.get.get_entity_object(
                        "Track", track_id=album_track.track_id
                    )
                    if track:
                        tracks.append(track)
                        seen_track_ids.add(album_track.track_id)

            # Track roles: directly get tracks where artist has this role
            track_links = (
                self.controller.get.get_all_entities(
                    "TrackArtistRole", role_id=self.role_id, artist_id=artist_id
                )
                or []
            )

            for link in track_links:
                if link.track_id in seen_track_ids:
                    continue
                track = self.controller.get.get_entity_object(
                    "Track", track_id=link.track_id
                )
                if track:
                    tracks.append(track)
                    seen_track_ids.add(link.track_id)

        except Exception as e:
            logger.error(f"Error getting tracks for artist {artist_id}: {e}")

        return tracks

    def _get_role_name(self):
        """Get the name of the current role."""
        try:
            role = self.controller.get.get_entity_object("Role", role_id=self.role_id)
            return role.role_name if role else f"Role {self.role_id}"
        except Exception as e:
            logger.error(f"Error getting role name: {e}")
            return f"Role {self.role_id}"

    def _load_data(self):
        """Load and display artist data from both album and track role assignments."""
        self.artist_list.clear()
        self.artist_data_map.clear()

        try:
            album_links = (
                self.controller.get.get_all_entities(
                    "AlbumRoleAssociation", role_id=self.role_id
                )
                or []
            )
            track_links = (
                self.controller.get.get_all_entities(
                    "TrackArtistRole", role_id=self.role_id
                )
                or []
            )

            if not album_links and not track_links:
                self.artist_list.addItem("No albums or tracks found for this role.")
                return

            # Group by artist, counting album and track appearances separately
            album_counts = defaultdict(int)
            track_counts = defaultdict(int)
            artist_entities = {}  # Store artist objects for display

            def _note_artist(artist_id):
                if artist_id not in artist_entities:
                    artist = self.controller.get.get_entity_object(
                        "Artist", artist_id=artist_id
                    )
                    if artist:
                        artist_entities[artist_id] = artist

            for link in album_links:
                album_counts[link.artist_id] += 1
                _note_artist(link.artist_id)

            for link in track_links:
                track_counts[link.artist_id] += 1
                _note_artist(link.artist_id)

            # Prepare display data
            artist_ids = set(album_counts) | set(track_counts)
            artists_display = []
            for artist_id in artist_ids:
                artist = artist_entities.get(artist_id)
                if artist:
                    album_count = album_counts.get(artist_id, 0)
                    track_count = track_counts.get(artist_id, 0)
                    artists_display.append(
                        {
                            "artist": artist,
                            "artist_id": artist_id,
                            "album_count": album_count,
                            "track_count": track_count,
                            "total": album_count + track_count,
                        }
                    )

            # Sort by total appearances descending
            sorted_artists = sorted(
                artists_display, key=lambda x: x["total"], reverse=True
            )

            # Display
            for data in sorted_artists:
                artist = data["artist"]
                name = getattr(artist, "artist_name", "Unknown Artist")

                parts = []
                if data["track_count"] > 0:
                    parts.append(f"{data['track_count']} tracks")
                if data["album_count"] > 0:
                    parts.append(f"{data['album_count']} albums")
                item_text = f"{name} ({', '.join(parts)})"

                # Create list item with artist ID stored as data
                item = QListWidgetItem(item_text)
                item.setData(
                    Qt.UserRole, data["artist_id"]
                )  # Store artist ID for context menu
                self.artist_list.addItem(item)

                # Also store in map for quick access if needed
                self.artist_data_map[data["artist_id"]] = data

            logger.info(
                f"Loaded {len(sorted_artists)} artists for role {self.role_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to load artist data for role {self.role_id}: {e}",
                exc_info=True,
            )
            self.artist_list.addItem("Error loading data.")
