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

from src.base_track_view import BaseTrackView
from src.logger_config import logger


class RoleDetailTab(QWidget):
    """Detailed view for a specific role's artist relationships."""

    def __init__(self, controller, role_id, role_type="Album"):
        super().__init__()
        self.controller = controller
        self.role_id = role_id
        self.role_type = role_type
        self.artist_data_map = {}
        self._setup_ui()
        self._load_data()
        logger.info(f"Detail tab created for {role_type} role {role_id}")

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
        """Get all tracks for an artist in the current role."""
        tracks = []

        try:
            if self.role_type == "Album":
                # For album roles: Get albums where artist has this role, then get tracks from those albums
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
                        track = self.controller.get.get_entity_object(
                            "Track", track_id=album_track.track_id
                        )
                        if track:
                            tracks.append(track)

            else:  # Track role
                # For track roles: Directly get tracks where artist has this role
                track_links = (
                    self.controller.get.get_all_entities(
                        "TrackArtistRole", role_id=self.role_id, artist_id=artist_id
                    )
                    or []
                )

                for link in track_links:
                    track = self.controller.get.get_entity_object(
                        "Track", track_id=link.track_id
                    )
                    if track:
                        tracks.append(track)

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
        """Load and display artist data based on role type."""
        self.artist_list.clear()
        self.artist_data_map.clear()

        try:
            if self.role_type == "Album":
                links = (
                    self.controller.get.get_all_entities(
                        "AlbumRoleAssociation", role_id=self.role_id
                    )
                    or []
                )
                entity_type = "albums"
                link_entity_id_attr = "album_id"
            else:  # Track role
                links = (
                    self.controller.get.get_all_entities(
                        "TrackArtistRole", role_id=self.role_id
                    )
                    or []
                )
                entity_type = "tracks"
                link_entity_id_attr = "track_id"  # noqa: F841

            if not links:
                self.artist_list.addItem(f"No {entity_type} found for this role.")
                return

            # Group by artist and count appearances
            artist_counts = defaultdict(int)
            artist_entities = {}  # Store artist objects for display

            for link in links:
                artist_id = link.artist_id
                artist_counts[artist_id] += 1
                if artist_id not in artist_entities:
                    artist = self.controller.get.get_entity_object(
                        "Artist", artist_id=artist_id
                    )
                    if artist:
                        artist_entities[artist_id] = artist

            # Prepare display data
            artists_display = []
            for artist_id, count in artist_counts.items():
                artist = artist_entities.get(artist_id)
                if artist:
                    artists_display.append(
                        {"artist": artist, "count": count, "artist_id": artist_id}
                    )

            # Sort by count descending
            sorted_artists = sorted(
                artists_display, key=lambda x: x["count"], reverse=True
            )

            # Display with appropriate icon
            for data in sorted_artists:
                artist = data["artist"]
                name = getattr(artist, "artist_name", "Unknown Artist")
                item_text = f"{name} ({data['count']} {entity_type})"

                # Create list item with artist ID stored as data
                item = QListWidgetItem(item_text)
                item.setData(
                    Qt.UserRole, data["artist_id"]
                )  # Store artist ID for context menu
                self.artist_list.addItem(item)

                # Also store in map for quick access if needed
                self.artist_data_map[data["artist_id"]] = data

            logger.info(
                f"Loaded {len(sorted_artists)} artists for {self.role_type.lower()} role {self.role_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to load artist data for {self.role_type.lower()} role {self.role_id}: {e}",
                exc_info=True,
            )
            self.artist_list.addItem("Error loading data.")
