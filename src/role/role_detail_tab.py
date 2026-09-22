from collections import defaultdict
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.detail_card import DetailCard
from src.common.widgets.layout_utils import clear_layout
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.view.base_track_view import BaseTrackView


class RoleDetailTab(QWidget):
    """Article-style detail view for a role: an overview card (name, parent
    link, description, totals), a sub-roles card that only appears when the
    role has children, and an artists card listing everyone assigned the
    role across both album and track credits."""

    # Emitted when the user clicks the parent-role link or a sub-role row,
    # so the owning RoleView can select that role in the tree.
    role_link_activated = Signal(int)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.role_id: int | None = None
        self.artist_data_map = {}
        self._init_ui()
        self.show_empty_state()

    def _init_ui(self):
        """Initialize the card-based UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        self.scroll_layout.setSpacing(14)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.empty_state = QLabel("Select a role to view details")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.scroll_layout.addWidget(self.empty_state)

        self.info_card = self._build_info_card()
        self.scroll_layout.addWidget(self.info_card)
        self.info_card.hide()

        self.subroles_card = self._build_subroles_card()
        self.scroll_layout.addWidget(self.subroles_card)
        self.subroles_card.hide()

        self.artists_card = self._build_artists_card()
        self.scroll_layout.addWidget(self.artists_card)
        self.artists_card.hide()

        self.scroll_layout.addStretch()

    def _build_info_card(self):
        """Create the role overview card: name, parent link, description,
        totals, and actions."""
        card = DetailCard("Overview")

        self.name_label = QLabel()
        self.name_label.setObjectName("RoleName")
        self.name_label.setWordWrap(True)
        card.body.addWidget(self.name_label)

        self.parent_button = QPushButton()
        self.parent_button.setObjectName("RoleParentLink")
        self.parent_button.setFlat(True)
        self.parent_button.setCursor(Qt.PointingHandCursor)
        self.parent_button.clicked.connect(self._on_parent_clicked)
        card.body.addWidget(self.parent_button, alignment=Qt.AlignLeft)
        self.parent_button.hide()

        self.description_label = QLabel()
        self.description_label.setObjectName("RoleDescription")
        self.description_label.setWordWrap(True)
        card.body.addWidget(self.description_label)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("RoleMeta")
        card.body.addWidget(self.meta_label, alignment=Qt.AlignLeft)

        button_layout = QHBoxLayout()
        self.tracks_button = QPushButton("View Tracks")
        self.tracks_button.clicked.connect(self.show_tracks)
        button_layout.addWidget(self.tracks_button)
        button_layout.addStretch()
        card.body.addLayout(button_layout)

        return card

    def _build_subroles_card(self):
        """Create the sub-roles card. Populated (and shown/hidden) by
        `_load_subroles`."""
        card = DetailCard("Sub-Roles")
        self.subroles_layout = card.body
        return card

    def _build_artists_card(self):
        """Create the artists card: a filter field plus the artist list."""
        card = DetailCard("Artists")

        self.artist_count_label = QLabel()
        self.artist_count_label.setObjectName("RoleMeta")
        card.body.addWidget(self.artist_count_label)

        self.artist_filter = QLineEdit()
        self.artist_filter.setPlaceholderText("Filter artists...")
        self.artist_filter.setClearButtonEnabled(True)
        self.artist_filter.textChanged.connect(self._filter_artists)
        card.body.addWidget(self.artist_filter)

        self.artist_list = QListWidget()
        self.artist_list.setObjectName("RoleArtistList")
        self.artist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artist_list.customContextMenuRequested.connect(self.show_artist_context_menu)
        card.body.addWidget(self.artist_list)

        return card

    # -----------------------------------------------------------------------
    # Empty / detail state
    # -----------------------------------------------------------------------

    def show_empty_state(self):
        """Show empty state when no role is selected."""
        self.empty_state.show()
        self.info_card.hide()
        self.subroles_card.hide()
        self.artists_card.hide()

    def show_detail_cards(self):
        """Show the overview and artists cards. The sub-roles card's
        visibility is decided by `_load_subroles` based on whether the role
        has children."""
        self.empty_state.hide()
        self.info_card.show()
        self.artists_card.show()

    # -----------------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------------

    def load_role_data(self, role_id):
        """Load and display data for the given role."""
        try:
            role = self.controller.get.get_entity_object("Role", role_id=role_id)
            if not role:
                self.show_empty_state()
                return

            self.role_id = role_id
            self.show_detail_cards()

            self._display_role_info(role)
            self._load_subroles(role_id)
            self._load_artists(role_id)

        except SQLAlchemyError as e:
            logger.error(f"Error loading role data: {e!s}")
            self.show_empty_state()

    def _display_role_info(self, role):
        """Update the overview card's name, parent link, and description."""
        self.name_label.setText(html.escape(role.role_name))

        if role.parent_id:
            parent = self.controller.get.get_entity_object("Role", role_id=role.parent_id)
            if parent:
                self.parent_button.setText(f"↑ {parent.role_name}")
                self._parent_role_id = parent.role_id
                self.parent_button.show()
            else:
                self.parent_button.hide()
        else:
            self.parent_button.hide()

        self.description_label.setText(role.role_description or "No description available")

    def _on_parent_clicked(self):
        parent_id = getattr(self, "_parent_role_id", None)
        if parent_id is not None:
            self.role_link_activated.emit(parent_id)

    def _load_subroles(self, role_id):
        """Load direct child roles, each with its own assignment count.
        Hides the card entirely when the role has no children."""
        clear_layout(self.subroles_layout)
        try:
            children = self.controller.get.get_all_entities("Role", parent_id=role_id) or []
        except SQLAlchemyError as e:
            logger.error(f"Error loading sub-roles for role {role_id}: {e!s}")
            children = []

        children = sorted(children, key=lambda r: r.role_name.lower())

        for child in children:
            count = self._count_role_assignments(child.role_id)
            button = QPushButton(f"{child.role_name} — {count} assignment{'s' if count != 1 else ''}")
            button.setObjectName("RoleSubroleLink")
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, cid=child.role_id: self.role_link_activated.emit(cid))
            self.subroles_layout.addWidget(button, alignment=Qt.AlignLeft)

        self.subroles_card.setVisible(bool(children))

    def _count_role_assignments(self, role_id):
        """Own-role (non-recursive) count of album + track assignments."""
        try:
            album_links = self.controller.get.get_all_entities("AlbumRoleAssociation", role_id=role_id) or []
            track_links = self.controller.get.get_all_entities("TrackArtistRole", role_id=role_id) or []
            return len(album_links) + len(track_links)
        except SQLAlchemyError as e:
            logger.error(f"Error counting assignments for role {role_id}: {e!s}")
            return 0

    # -----------------------------------------------------------------------
    # Artists list
    # -----------------------------------------------------------------------

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
                logger.info(f"No tracks found for artist {artist_id} in role {self.role_id}")
                show_status_message(self, "No tracks found for this artist in this role.")
                return

            # Get artist name for window title
            artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
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

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error showing tracks for artist {artist_id}: {e}", exc_info=True)

    def show_tracks(self):
        """Show all tracks assigned this role, across every artist."""
        if self.role_id is None:
            return
        try:
            tracks = []
            seen_track_ids = set()
            for artist_id in self.artist_data_map:
                for track in self._get_artist_tracks_for_role(artist_id):
                    if track.track_id not in seen_track_ids:
                        tracks.append(track)
                        seen_track_ids.add(track.track_id)

            if not tracks:
                show_status_message(self, "No tracks found for this role.")
                return

            role_name = self._get_role_name()
            track_view = BaseTrackView(
                controller=self.controller,
                tracks=tracks,
                title=f"Tracks - {role_name}",
                enable_drag=True,
                enable_drop=False,
            )
            track_view.exec_()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error showing tracks for role {self.role_id}: {e}", exc_info=True)

    def _get_artist_tracks_for_role(self, artist_id):
        """Get all tracks for an artist in this role, from both album and track assignments."""
        tracks = []
        seen_track_ids = set()

        try:
            # Album roles: get albums where artist has this role, then get tracks from those albums
            album_links = (
                self.controller.get.get_all_entities(
                    "AlbumRoleAssociation", role_id=self.role_id, artist_id=artist_id
                )
                or []
            )

            for link in album_links:
                album_tracks = (
                    self.controller.get.get_entity_links("AlbumTracks", album_id=link.album_id)
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
                track = self.controller.get.get_entity_object("Track", track_id=link.track_id)
                if track:
                    tracks.append(track)
                    seen_track_ids.add(link.track_id)

        except SQLAlchemyError as e:
            logger.error(f"Error getting tracks for artist {artist_id}: {e}")

        return tracks

    def _get_role_name(self):
        """Get the name of the current role."""
        try:
            role = self.controller.get.get_entity_object("Role", role_id=self.role_id)
            return role.role_name if role else f"Role {self.role_id}"
        except SQLAlchemyError as e:
            logger.error(f"Error getting role name: {e}")
            return f"Role {self.role_id}"

    def _filter_artists(self, text):
        """Hide artist rows whose text doesn't contain `text` (case-insensitive)."""
        text = text.lower()
        for i in range(self.artist_list.count()):
            item = self.artist_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _load_artists(self, role_id):
        """Load and display artist data from both album and track role assignments."""
        self.artist_list.clear()
        self.artist_data_map.clear()
        self.artist_filter.clear()

        try:
            album_links = (
                self.controller.get.get_all_entities("AlbumRoleAssociation", role_id=role_id) or []
            )
            track_links = (
                self.controller.get.get_all_entities("TrackArtistRole", role_id=role_id) or []
            )

            album_count = len(album_links)
            track_count = len(track_links)

            if not album_links and not track_links:
                self.artist_count_label.setText("No artists assigned this role.")
                self.meta_label.setText("0 artists · 0 tracks · 0 albums")
                return

            # Group by artist, counting album and track appearances separately
            album_counts = defaultdict(int)
            track_counts = defaultdict(int)
            artist_entities = {}  # Store artist objects for display

            def _note_artist(artist_id):
                if artist_id not in artist_entities:
                    artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
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
                    a_count = album_counts.get(artist_id, 0)
                    t_count = track_counts.get(artist_id, 0)
                    artists_display.append(
                        {
                            "artist": artist,
                            "artist_id": artist_id,
                            "album_count": a_count,
                            "track_count": t_count,
                            "total": a_count + t_count,
                        }
                    )

            # Sort by total appearances descending
            sorted_artists = sorted(artists_display, key=lambda x: x["total"], reverse=True)

            # Display
            for data in sorted_artists:
                artist = data["artist"]
                name = getattr(artist, "artist_name", "Unknown Artist")

                parts = []
                if data["track_count"] > 0:
                    parts.append(f"{data['track_count']} track{'s' if data['track_count'] != 1 else ''}")
                if data["album_count"] > 0:
                    parts.append(f"{data['album_count']} album{'s' if data['album_count'] != 1 else ''}")
                item_text = f"{name} ({', '.join(parts)})"

                # Create list item with artist ID stored as data
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, data["artist_id"])  # Store artist ID for context menu
                self.artist_list.addItem(item)

                # Also store in map for quick access if needed
                self.artist_data_map[data["artist_id"]] = data

            self.artist_count_label.setText(
                f"{len(sorted_artists)} artist{'s' if len(sorted_artists) != 1 else ''}"
            )
            self.meta_label.setText(
                f"{len(sorted_artists)} artists · {track_count} tracks · {album_count} albums"
            )

            logger.info(f"Loaded {len(sorted_artists)} artists for role {role_id}")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Failed to load artist data for role {role_id}: {e}", exc_info=True)
            self.artist_count_label.setText("Error loading data.")
