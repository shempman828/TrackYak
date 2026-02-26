"""Artist management view handling both individuals and groups."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from artist_detail import ArtistDetailTab
from artist_detail_wiki import WikipediaImportDialog
from artist_edit import ArtistEditor
from artist_fuzzy_match import FuzzyMatchDialog
from artist_group_dialog import AddGroupDialog, AddMemberDialog
from award_new import AddAwardDialog
from base_split_dialog import SplitDBDialog
from base_track_view import BaseTrackView
from influences_dialog import AddInfluenceDialog
from logger_config import logger


# -------------------------
# Main Artist View Widget
# -------------------------
class ArtistView(QWidget):
    """Unified artist management view handling both individuals and groups."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.all_artists = []
        self.current_mode = "all"
        self._setup_ui()
        self.load_artists()

    # ----------------------------
    # UI Setup
    # ----------------------------

    def _setup_ui(self):
        """Build the main layout with filter bar, artist list, and detail tabs."""
        layout = QVBoxLayout(self)

        # --- Filter / mode bar ---
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Show:"))

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["All Artists", "Individuals", "Groups"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        filter_bar.addWidget(self.mode_combo)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter artists…")
        self.search_box.textChanged.connect(self._filter_list)
        filter_bar.addWidget(self.search_box)

        layout.addLayout(filter_bar)

        # --- Splitter: list on the left, detail tabs on the right ---
        splitter = QSplitter(Qt.Horizontal)

        self.artist_list = QListWidget()
        self.artist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artist_list.customContextMenuRequested.connect(self._show_context_menu)
        self.artist_list.currentItemChanged.connect(self._on_artist_selected)
        splitter.addWidget(self.artist_list)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        splitter.addWidget(self.tab_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

    # ----------------------------
    # Data Loading
    # ----------------------------

    def load_artists(self):
        """Load artists filtered by current mode."""
        try:
            all_artists = sorted(
                self.controller.get.get_all_entities("Artist"),
                key=lambda a: a.artist_name.lower(),
            )

            if self.current_mode == "individuals":
                artists = [a for a in all_artists if not getattr(a, "isgroup", 0)]
            elif self.current_mode == "groups":
                artists = [a for a in all_artists if getattr(a, "isgroup", 0)]
            else:
                artists = all_artists

            self.all_artists = artists
            self._populate_list(artists)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load artists: {e}")

    def _populate_list(self, artists):
        """Fill the list widget from a list of artist objects."""
        self.artist_list.clear()
        filter_text = (
            self.search_box.text().lower() if hasattr(self, "search_box") else ""
        )

        for artist in artists:
            if filter_text and filter_text not in artist.artist_name.lower():
                continue

            display_name = artist.artist_name
            if getattr(artist, "isgroup", 0):
                display_name = f"👥 {display_name}"

            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, artist.artist_id)
            self.artist_list.addItem(item)

    # ----------------------------
    # Event Handlers
    # ----------------------------

    def _on_mode_changed(self, mode_text: str):
        """Handle mode changes between all/individuals/groups."""
        mode_map = {
            "All Artists": "all",
            "Individuals": "individuals",
            "Groups": "groups",
        }
        self.current_mode = mode_map.get(mode_text, "all")
        self.load_artists()

    def _filter_list(self, text: str):
        """Re-populate the list applying the current search filter."""
        self._populate_list(self.all_artists)

    def _on_artist_selected(self):
        """Display selected artist/group detail tab."""
        selected = self.artist_list.currentItem()
        if not selected:
            return

        artist_id = selected.data(Qt.UserRole)
        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        if not artist:
            QMessageBox.warning(
                self, "Not Found", f"No artist found with ID {artist_id}"
            )
            return

        self.tab_widget.clear()
        detail_tab = ArtistDetailTab(artist, self.controller)
        self.tab_widget.addTab(detail_tab, artist.artist_name)

    def _close_tab(self, index: int):
        """Close the tab at the given index."""
        self.tab_widget.removeTab(index)

    # ----------------------------
    # Context Menu
    # ----------------------------

    def _show_context_menu(self, position):
        """Enhanced context menu with group-specific actions."""
        menu = QMenu(self)
        selected = self.artist_list.currentItem()

        if selected:
            artist_id = selected.data(Qt.UserRole)
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
            if not artist:
                return

            is_group = getattr(artist, "isgroup", 0)

            # ---- Track browsing ----
            view_tracks_action = menu.addAction("🎵 View Artist Tracks")
            view_tracks_action.triggered.connect(
                lambda: self._view_artist_tracks(artist)
            )

            menu.addSeparator()

            # ---- Common artist actions ----
            edit_action = menu.addAction("✏️ Edit Artist")
            edit_action.triggered.connect(lambda: self._edit_artist(artist))

            merge_action = menu.addAction("🔄 Merge Artist")
            merge_action.triggered.connect(lambda: self._merge_artist(artist))

            split_action = menu.addAction("🔀 Split Artist")
            split_action.triggered.connect(lambda: self._split_artist(artist))

            menu.addSeparator()

            # ---- Group-specific actions ----
            if is_group:
                add_member_action = menu.addAction("➕ Add Member")
                add_member_action.triggered.connect(lambda: self._add_member(artist))
            else:
                add_to_group_action = menu.addAction("👥 Add to Group")
                add_to_group_action.triggered.connect(
                    lambda: self._add_to_group(artist)
                )

            # ---- Common extras ----
            add_award_action = menu.addAction("🏆 Add Award")
            add_award_action.triggered.connect(lambda: self._add_award(artist))

            add_place_action = menu.addAction("📍 Add Place")
            add_place_action.triggered.connect(lambda: self._add_place(artist))

            menu.addSeparator()

            # ---- Convert group status ----
            if is_group:
                convert_action = menu.addAction("👤 Convert to Individual")
                convert_action.triggered.connect(
                    lambda: self._convert_to_individual(artist)
                )
            else:
                convert_action = menu.addAction("👥 Convert to Group")
                convert_action.triggered.connect(lambda: self._convert_to_group(artist))

            wiki_action = menu.addAction("🌐 Wikipedia Search")
            wiki_action.triggered.connect(self.search_wikipedia)

            influences_action = menu.addAction("🔗 Edit Influences")
            influences_action.triggered.connect(self.edit_influences)

            pic_action = menu.addAction("🖼️ Add Artist Image")
            pic_action.triggered.connect(self.add_profile_picture)

            menu.addSeparator()

            delete_action = menu.addAction("🗑️ Delete Artist")
            delete_action.triggered.connect(lambda: self._delete_artist(artist))

        # ---- Always-visible add actions ----
        menu.addSeparator()
        add_action = menu.addAction("➕ Add Artist")
        add_action.triggered.connect(self.add_new_artist)

        add_group_action = menu.addAction("👥 Add Group")
        add_group_action.triggered.connect(self.add_new_group)

        menu.exec_(self.artist_list.mapToGlobal(position))

    # ----------------------------
    # View Artist Tracks
    # ----------------------------

    def _view_artist_tracks(self, artist):
        """Load all tracks associated with an artist and display them in a BaseTrackView."""
        try:
            tracks = self._get_all_artist_tracks(artist.artist_id)

            if not tracks:
                QMessageBox.information(
                    self,
                    "No Tracks Found",
                    f"No tracks found for '{artist.artist_name}'.",
                )
                return

            track_view = BaseTrackView(
                controller=self.controller,
                tracks=tracks,
                title=f"Tracks — {artist.artist_name} ({len(tracks)} tracks)",
                enable_drag=True,
                enable_drop=False,
            )
            track_view.exec_()

            logger.info(
                f"Displayed {len(tracks)} tracks for artist '{artist.artist_name}' "
                f"(id={artist.artist_id})"
            )

        except Exception as e:
            logger.error(
                f"Error displaying tracks for artist {artist.artist_id}: {e}",
                exc_info=True,
            )
            QMessageBox.critical(
                self, "Error", f"Failed to load tracks for '{artist.artist_name}':\n{e}"
            )

    def _get_all_artist_tracks(self, artist_id: int) -> list:
        """
        Return a deduplicated list of Track objects associated with an artist
        via both TrackArtistRole (track-level credits) and AlbumRoleAssociation
        (album-level credits, e.g. album artist).
        """
        track_map: dict[int, object] = {}

        # --- Track-level roles ---
        try:
            track_roles = (
                self.controller.get.get_all_entities(
                    "TrackArtistRole", artist_id=artist_id
                )
                or []
            )
            for tr in track_roles:
                track = self.controller.get.get_entity_object(
                    "Track", track_id=tr.track_id
                )
                if track and track.track_id not in track_map:
                    track_map[track.track_id] = track
        except Exception as e:
            logger.warning(f"Error fetching track roles for artist {artist_id}: {e}")

        # --- Album-level roles (e.g. album artist) ---
        try:
            album_roles = (
                self.controller.get.get_all_entities(
                    "AlbumRoleAssociation", artist_id=artist_id
                )
                or []
            )
            for ar in album_roles:
                album_tracks = (
                    self.controller.get.get_entity_links(
                        "AlbumTracks", album_id=ar.album_id
                    )
                    or []
                )
                for at in album_tracks:
                    if at.track_id not in track_map:
                        track = self.controller.get.get_entity_object(
                            "Track", track_id=at.track_id
                        )
                        if track:
                            track_map[track.track_id] = track
        except Exception as e:
            logger.warning(f"Error fetching album roles for artist {artist_id}: {e}")

        return list(track_map.values())

    # ----------------------------
    # Group / Member Actions
    # ----------------------------

    def _add_member(self, group):
        """Add a member to a group."""
        dialog = AddMemberDialog(self.controller, group, self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _add_to_group(self, artist):
        """Add an individual artist to an existing group."""
        groups = [a for a in self.all_artists if getattr(a, "isgroup", 0)]
        if not groups:
            QMessageBox.information(self, "No Groups", "No groups exist yet.")
            return

        group_names = [g.artist_name for g in groups]
        choice, ok = QInputDialog.getItem(
            self, "Add to Group", "Select group:", group_names, 0, False
        )
        if not ok:
            return

        group = next((g for g in groups if g.artist_name == choice), None)
        if group:
            try:
                self.controller.add.add_entity(
                    "GroupMembership",
                    group_id=group.artist_id,
                    member_id=artist.artist_id,
                )
                QMessageBox.information(
                    self,
                    "Success",
                    f"'{artist.artist_name}' added to '{group.artist_name}'.",
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to add to group: {e}")

    def _convert_to_group(self, artist):
        """Convert an individual artist to a group."""
        try:
            self.controller.update.update_entity("Artist", artist.artist_id, isgroup=1)
            self.load_artists()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    def _convert_to_individual(self, group):
        """Convert a group to an individual artist."""
        reply = QMessageBox.question(
            self,
            "Convert to Individual",
            f"Convert '{group.artist_name}' to an individual artist?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.controller.update.update_entity(
                    "Artist", group.artist_id, isgroup=0
                )
                self.load_artists()
                QMessageBox.information(
                    self, "Success", "Group converted to individual."
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    # ----------------------------
    # Add / Edit / Delete Actions
    # ----------------------------

    def add_new_artist(self):
        """Prompt for a name and add a new artist."""
        name, ok = QInputDialog.getText(self, "Add Artist", "Artist name:")
        if ok and name.strip():
            try:
                self.controller.add.add_entity("Artist", artist_name=name.strip())
                self.load_artists()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to add artist: {e}")

    def add_new_group(self):
        """Open the Add Group dialog."""
        dialog = AddGroupDialog(self.controller, self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _edit_artist(self, artist=None):
        """Open the artist editor for the given or currently selected artist."""
        if artist is None:
            selected = self.artist_list.currentItem()
            if not selected:
                logger.error("No artist selected for editing.")
                return
            artist_id = selected.data(Qt.UserRole)
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
            if not artist:
                logger.warning(f"Artist with id {artist_id} not found.")
                return

        dialog = ArtistEditor(self.controller, artist, self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _split_artist(self, artist=None):
        """Open the split dialog for the given or currently selected artist."""
        if artist is None:
            selected = self.artist_list.currentItem()
            if not selected:
                QMessageBox.warning(
                    self, "Select Artist", "Please select an artist first."
                )
                return
            artist_id = selected.data(Qt.UserRole)
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
            if not artist:
                QMessageBox.warning(self, "Not Found", "Artist not found.")
                return

        dialog = SplitDBDialog(self.controller.split, "Artist", artist, self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _merge_artist(self, artist):
        """Merge this artist into another (placeholder — implement via merge controller)."""
        QMessageBox.information(
            self,
            "Merge Artist",
            "Use the Merge feature from the artist detail panel to perform merges.",
        )

    def _delete_artist(self, artist):
        """Delete an artist after confirmation."""
        reply = QMessageBox.question(
            self,
            "Delete Artist",
            f"Permanently delete '{artist.artist_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.controller.delete.delete_entity(
                    "Artist", artist_id=artist.artist_id
                )
                self.load_artists()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete artist: {e}")

    def _add_award(self, artist):
        """Open the Add Award dialog for an artist."""
        try:
            dialog = AddAwardDialog(self.controller, "Artist", artist.artist_id, self)
            if dialog.exec_() == QDialog.Accepted:
                self._on_artist_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add award: {e}")

    def _add_place(self, artist):
        """Open the Place Selection dialog for an artist."""
        try:
            dialog = PlaceSelectionDialog(self.controller, self)
            if dialog.exec_() == QDialog.Accepted:
                place_id = dialog.selected_place_id
                association_type = dialog.selected_association_type
                if place_id:
                    self.controller.add.add_entity(
                        "PlaceAssociation",
                        place_id=place_id,
                        entity_id=artist.artist_id,
                        entity_type="Artist",
                        association_type=association_type or "associated",
                    )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add place: {e}")

    # ----------------------------
    # Wikipedia / Influences / Image
    # ----------------------------

    def search_wikipedia(self):
        """Search Wikipedia for the currently selected artist."""
        selected = self.artist_list.currentItem()
        if not selected:
            return
        artist_id = selected.data(Qt.UserRole)
        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        if artist:
            dialog = WikipediaImportDialog(self.controller, artist, self)
            dialog.exec_()

    def edit_influences(self):
        """Open the influence editor for the currently selected artist."""
        artists = self.controller.get.get_all_entities("Artist")
        all_artists = [(a.artist_id, a.artist_name) for a in artists]
        dialog = AddInfluenceDialog(self.controller, all_artists)
        dialog.exec()

    def add_profile_picture(self):
        """Add or replace the profile picture for the selected artist."""
        selected = self.artist_list.currentItem()
        if not selected:
            return
        artist_id = selected.data(Qt.UserRole)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Profile Picture",
            "",
            "Image Files (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
            options=QFileDialog.DontUseNativeDialog,
        )
        if not file_path:
            return

        success = self.controller.update.update_entity(
            "Artist", artist_id, profile_pic_path=file_path
        )
        if success:
            QMessageBox.information(self, "Success", "Profile picture updated.")
        else:
            QMessageBox.warning(self, "Error", "Failed to update profile picture.")

    def find_fuzzy_matches(self):
        """Open the fuzzy match dialog for duplicate detection."""
        dialog = FuzzyMatchDialog(self.controller, self)
        dialog.exec_()


# -------------------------
# Place Selection Dialog
# -------------------------
class PlaceSelectionDialog(QDialog):
    """Dialog for selecting a place to associate with an artist."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.selected_place_id = None
        self.selected_association_type = "associated"
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("Add Place Association")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a place:"))

        self.place_list = QListWidget()
        try:
            places = self.controller.get.get_all_entities("Place")
            for place in sorted(places, key=lambda p: p.place_name.lower()):
                item = QListWidgetItem(place.place_name)
                item.setData(Qt.UserRole, place.place_id)
                self.place_list.addItem(item)
        except Exception as e:
            logger.error(f"Error loading places: {e}")

        layout.addWidget(self.place_list)

        layout.addWidget(QLabel("Association type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["associated", "birth", "death", "residence"])
        layout.addWidget(self.type_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        item = self.place_list.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a place.")
            return
        self.selected_place_id = item.data(Qt.UserRole)
        self.selected_association_type = self.type_combo.currentText()
        self.accept()
