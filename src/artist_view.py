"""Artist management view handling both individuals and groups."""

import urllib.parse
import webbrowser

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
    QVBoxLayout,
    QWidget,
)

from src.artist_detail import ArtistDetailTab
from src.artist_detail_wiki import WikipediaImportDialog
from src.artist_edit import ArtistEditor
from src.artist_fuzzy_match import FuzzyMatchDialog
from src.artist_group_dialog import AddGroupDialog, AddMemberDialog
from src.award_new import AddAwardDialog
from src.base_split_dialog import SplitDBDialog
from src.base_track_view import BaseTrackView
from src.influences_dialog import AddInfluenceDialog
from src.logger_config import logger


# -------------------------
# Main Artist View Widget
# -------------------------
class ArtistView(QWidget):
    """Unified artist management view handling both individuals and groups."""

    # Sort options: (display label, sort key function)
    _SORT_OPTIONS = [
        ("Name (A–Z)", lambda a: a.artist_name.lower()),
        ("Name (Z–A)", lambda a: a.artist_name.lower()),  # reversed below
        ("Earliest First", lambda a: getattr(a, "begin_year", None) or 9999),
        ("Latest First", lambda a: getattr(a, "begin_year", None) or 9999),  # reversed
        ("Has Bio First", lambda a: 0 if getattr(a, "biography", None) else 1),
    ]
    _SORT_REVERSED = {
        "Name (Z–A)": True,
        "Latest First": True,
    }

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
        """Build the main layout with filter bar, artist list, and detail panel."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- Single compact filter row ---
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(4)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["All", "Individuals", "Groups"])
        self.mode_combo.setToolTip("Show all artists, individuals only, or groups only")
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        filter_bar.addWidget(self.mode_combo)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.search_box, stretch=1)

        self.sort_combo = QComboBox()
        for label, _ in self._SORT_OPTIONS:
            self.sort_combo.addItem(label)
        self.sort_combo.setToolTip("Sort order")
        self.sort_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.sort_combo)

        self.metadata_combo = QComboBox()
        self.metadata_combo.addItems(["Any Metadata", "Complete", "Incomplete"])
        self.metadata_combo.setToolTip("Filter by metadata completeness")
        self.metadata_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.metadata_combo)

        self.image_combo = QComboBox()
        self.image_combo.addItems(["Any Image", "Has Image", "No Image"])
        self.image_combo.setToolTip("Filter by profile image")
        self.image_combo.currentTextChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.image_combo)

        # Count label — shows "Showing X of Y"
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: grey; font-size: 11px;")
        filter_bar.addWidget(self.count_label)

        layout.addLayout(filter_bar)

        # --- Splitter: list on the left, detail panel on the right ---
        splitter = QSplitter(Qt.Horizontal)

        # Artist list with a minimum width so it never collapses too small,
        # but users can drag to make it wider.
        self.artist_list = QListWidget()
        self.artist_list.setMinimumWidth(180)
        self.artist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artist_list.customContextMenuRequested.connect(self._show_context_menu)
        self.artist_list.currentItemChanged.connect(self._on_artist_selected)
        splitter.addWidget(self.artist_list)

        # Detail panel: plain widget that swaps in an ArtistDetailTab directly —
        # no tab bar or tab names needed, since the detail header already shows
        # the artist name.
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel("Select an artist to view details")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: grey; font-style: italic;")
        self.detail_layout.addWidget(self._placeholder)

        self._current_detail = None  # track which widget is currently shown

        splitter.addWidget(self.detail_container)

        # Give the list ~1 part and the detail ~3 parts of available space.
        # The initial pixel sizes respect the minimum and give a sensible default.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 660])

        layout.addWidget(splitter, stretch=1)

    # ----------------------------
    # Data Loading
    # ----------------------------

    def load_artists(self):
        """Load all artists from DB, pre-filtered by individual/group mode."""
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
            self._apply_filters()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load artists: {e}")

    def _apply_filters(self):
        """Apply search text, metadata, image, and sort filters, then repopulate."""
        artists = list(self.all_artists)

        # --- Search text filter ---
        text = self.search_box.text().lower().strip()
        if text:
            artists = [a for a in artists if text in a.artist_name.lower()]

        # --- Metadata complete filter ---
        metadata_mode = self.metadata_combo.currentText()
        if metadata_mode == "Complete":
            artists = [a for a in artists if getattr(a, "is_fixed", 0)]
        elif metadata_mode == "Incomplete":
            artists = [a for a in artists if not getattr(a, "is_fixed", 0)]

        # --- Profile image filter ---
        image_mode = self.image_combo.currentText()
        if image_mode == "Has Image":
            artists = [a for a in artists if getattr(a, "profile_pic_path", None)]
        elif image_mode == "No Image":
            artists = [a for a in artists if not getattr(a, "profile_pic_path", None)]

        # --- Sort ---
        sort_label = self.sort_combo.currentText()
        sort_key = next(
            (key for label, key in self._SORT_OPTIONS if label == sort_label),
            lambda a: a.artist_name.lower(),
        )
        reverse = self._SORT_REVERSED.get(sort_label, False)
        try:
            artists = sorted(artists, key=sort_key, reverse=reverse)
        except Exception as e:
            logger.warning(f"Sort failed: {e}")

        self._populate_list(artists)

    def _populate_list(self, artists):
        """Fill the list widget from a filtered/sorted list of artist objects."""
        self.artist_list.clear()

        for artist in artists:
            display_name = artist.artist_name or "(no name)"
            if getattr(artist, "isgroup", 0):
                display_name = f"👥 {display_name}"

            # Small badge if metadata is flagged complete
            if getattr(artist, "is_fixed", 0):
                display_name = f"{display_name} ✓"

            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, artist.artist_id)
            self.artist_list.addItem(item)

        # Update count label
        total = len(self.all_artists)
        showing = len(artists)
        if showing == total:
            self.count_label.setText(f"{total} artist{'s' if total != 1 else ''}")
        else:
            self.count_label.setText(f"{showing} of {total} artists")

    # ----------------------------
    # Event Handlers
    # ----------------------------

    def _on_mode_changed(self, mode_text: str):
        """Handle mode changes between all/individuals/groups."""
        mode_map = {
            "All": "all",
            "Individuals": "individuals",
            "Groups": "groups",
        }
        self.current_mode = mode_map.get(mode_text, "all")
        self.load_artists()

    def _on_artist_selected(self):
        """Swap the detail panel to show the selected artist — no tabs needed."""
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

        # Remove whatever is currently in the detail panel
        if self._current_detail is not None:
            self.detail_layout.removeWidget(self._current_detail)
            self._current_detail.setParent(None)
            self._current_detail.deleteLater()
            self._current_detail = None

        # Hide the placeholder and insert the new detail widget
        self._placeholder.hide()
        detail = ArtistDetailTab(artist, self.controller)
        self.detail_layout.addWidget(detail)
        self._current_detail = detail

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

            menu.addSeparator()

            # Wikipedia: open stored link if available, always offer a search
            wiki_link = getattr(artist, "wikipedia_link", None)
            if wiki_link:
                open_wiki_action = menu.addAction("🌐 Open Wikipedia Page")
                open_wiki_action.triggered.connect(
                    lambda checked=False, url=wiki_link: webbrowser.open(url)
                )
            search_wiki_action = menu.addAction("🔍 Search Wikipedia…")
            search_wiki_action.triggered.connect(self.search_wikipedia)

            import_wiki_action = menu.addAction("⬇️ Import from Wikipedia…")
            import_wiki_action.triggered.connect(self.import_from_wikipedia)

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
        seen_ids = set()
        tracks = []

        try:
            track_roles = self.controller.get.get_all_entities(
                "TrackArtistRole", artist_id=artist_id
            )
            for role in track_roles:
                track = self.controller.get.get_entity_object(
                    "Track", track_id=role.track_id
                )
                if track and track.track_id not in seen_ids:
                    seen_ids.add(track.track_id)
                    tracks.append(track)
        except Exception as e:
            logger.warning(f"Error fetching track-level credits: {e}")

        try:
            album_roles = self.controller.get.get_all_entities(
                "AlbumRoleAssociation", artist_id=artist_id
            )
            for role in album_roles:
                album_tracks = self.controller.get.get_all_entities(
                    "Track", album_id=role.album_id
                )
                for track in album_tracks:
                    if track.track_id not in seen_ids:
                        seen_ids.add(track.track_id)
                        tracks.append(track)
        except Exception as e:
            logger.warning(f"Error fetching album-level credits: {e}")

        return tracks

    # ----------------------------
    # Artist CRUD / Actions
    # ----------------------------

    def add_new_artist(self):
        """Open the ArtistEditor dialog to add a new individual artist."""
        try:
            dialog = ArtistEditor(self.controller, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open artist editor: {e}")

    def add_new_group(self):
        """Open the AddGroupDialog to create a new group."""
        try:
            dialog = AddGroupDialog(self.controller, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open group dialog: {e}")

    def _edit_artist(self, artist):
        """Open the ArtistEditor dialog for an existing artist."""
        try:
            dialog = ArtistEditor(self.controller, artist=artist, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
                self._on_artist_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open artist editor: {e}")

    def _add_member(self, group_artist):
        """Open the AddMemberDialog to add a member to a group."""
        try:
            dialog = AddMemberDialog(self.controller, group_artist, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self._on_artist_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add member: {e}")

    def _add_to_group(self, artist):
        """Open an input dialog to add this individual to an existing group."""
        try:
            groups = [
                a
                for a in self.controller.get.get_all_entities("Artist")
                if getattr(a, "isgroup", 0)
            ]
            group_names = [g.artist_name for g in groups]
            if not group_names:
                QMessageBox.information(self, "No Groups", "No groups exist yet.")
                return

            name, ok = QInputDialog.getItem(
                self, "Add to Group", "Select a group:", group_names, editable=False
            )
            if ok and name:
                group = next((g for g in groups if g.artist_name == name), None)
                if group:
                    self.controller.add.add_entity(
                        "GroupMembership",
                        group_id=group.artist_id,
                        member_id=artist.artist_id,
                    )
                    self._on_artist_selected()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add to group: {e}")

    def _convert_to_group(self, artist):
        """Convert an individual artist to a group."""
        reply = QMessageBox.question(
            self,
            "Convert to Group",
            f"Convert '{artist.artist_name}' to a group?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.controller.update.update_entity(
                    "Artist", artist.artist_id, isgroup=1
                )
                self.load_artists()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    def _convert_to_individual(self, artist):
        """Convert a group to an individual artist."""
        reply = QMessageBox.question(
            self,
            "Convert to Individual",
            f"Convert '{artist.artist_name}' to an individual?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                self.controller.update.update_entity(
                    "Artist", artist.artist_id, isgroup=0
                )
                self.load_artists()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    def _split_artist(self, artist):
        """Split this artist record into two separate artists."""
        if not artist:
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
        """Open a Wikipedia search in the browser for the currently selected artist."""
        selected = self.artist_list.currentItem()
        if not selected:
            return
        artist_id = selected.data(Qt.UserRole)
        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        if artist:
            query = urllib.parse.quote_plus(artist.artist_name)
            webbrowser.open(f"https://en.wikipedia.org/w/index.php?search={query}")

    def import_from_wikipedia(self):
        """Open the Wikipedia import dialog for the currently selected artist."""
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
