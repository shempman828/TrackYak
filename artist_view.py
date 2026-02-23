""" """

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
from thefuzz import fuzz

from artist_detail import ArtistDetailTab
from artist_detail_wiki import WikipediaImportDialog
from artist_fuzzy_match import FuzzyMatchDialog
from artist_group_dialog import AddGroupDialog, AddMemberDialog
from award_new import AddAwardDialog
from base_split_dialog import SplitDBDialog
from influences_dialog import AddInfluenceDialog
from wikipedia_seach import search_wikipedia


# -------------------------
# Main Artist View Widget
# -------------------------
class ArtistView(QWidget):
    """Unified artist management view handling both individuals and groups."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.current_mode = "all"  # "all", "individuals", "groups"
        self.all_artists = []
        self._init_ui()
        self.load_artists()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        # Left Panel with mode selector
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 6, 6, 6)

        # Mode selector
        mode_layout = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["All Artists", "Individuals", "Groups"])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(QLabel("View:"))
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addStretch()
        left_layout.addLayout(mode_layout)

        # Existing search box
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search artists...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_artists)
        left_layout.addWidget(self.search_box)

        # Artist list (now handles both individuals and groups)
        self.artist_list = QListWidget()
        self.artist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artist_list.customContextMenuRequested.connect(self._show_context_menu)
        self.artist_list.itemSelectionChanged.connect(self._on_artist_selected)
        left_layout.addWidget(self.artist_list, stretch=1)

        # Right Panel remains the same
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)

        splitter.addWidget(left_panel)
        splitter.addWidget(self.tab_widget)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter)

    def _on_mode_changed(self, mode_text):
        """Handle mode changes between all/individuals/groups"""
        mode_map = {
            "All Artists": "all",
            "Individuals": "individuals",
            "Groups": "groups",
        }
        self.current_mode = mode_map[mode_text]
        self.load_artists()

    def load_artists(self):
        """Load artists filtered by current mode"""
        try:
            all_artists = sorted(
                self.controller.get.get_all_entities("Artist"),
                key=lambda a: a.artist_name.lower(),
            )

            # Apply mode filter
            if self.current_mode == "individuals":
                artists = [a for a in all_artists if not getattr(a, "isgroup", 0)]
            elif self.current_mode == "groups":
                artists = [a for a in all_artists if getattr(a, "isgroup", 0)]
            else:  # "all"
                artists = all_artists

            self.all_artists = artists
            self.artist_list.clear()

            for artist in artists:
                # Add group indicator for groups
                display_name = artist.artist_name
                if getattr(artist, "isgroup", 0):
                    display_name = f"👥 {display_name}"

                item = QListWidgetItem(display_name)
                item.setData(Qt.UserRole, artist.artist_id)
                self.artist_list.addItem(item)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load artists: {e}")

    def _show_context_menu(self, position):
        """Enhanced context menu with group-specific actions"""
        menu = QMenu(self)
        selected = self.artist_list.currentItem()

        if selected:
            artist_id = selected.data(Qt.UserRole)
            artist = self.controller.get.get_entity_object(
                "Artist", artist_id=artist_id
            )
            is_group = getattr(artist, "isgroup", 0)

            # Common actions
            merge_action = menu.addAction("🔄 Merge Artist")
            merge_action.triggered.connect(lambda: self._merge_artist(artist))

            split_action = menu.addAction("🔀 Split Artist")
            split_action.triggered.connect(lambda: self._split_artist(artist))

            menu.addSeparator()

            # Group-specific actions
            if is_group:
                add_member_action = menu.addAction("➕ Add Member")
                add_member_action.triggered.connect(lambda: self._add_member(artist))
            else:
                add_to_group_action = menu.addAction("👥 Add to Group")
                add_to_group_action.triggered.connect(
                    lambda: self._add_to_group(artist)
                )

            # Common actions
            add_award_action = menu.addAction("🏆 Add Award")
            add_award_action.triggered.connect(lambda: self._add_award(artist))

            add_place_action = menu.addAction("📍 Add Place")
            add_place_action.triggered.connect(lambda: self._add_place(artist))

            menu.addSeparator()

            # Toggle group status
            if is_group:
                convert_action = menu.addAction("👤 Convert to Individual")
                convert_action.triggered.connect(
                    lambda: self._convert_to_individual(artist)
                )
            else:
                convert_action = menu.addAction("👥 Convert to Group")
                convert_action.triggered.connect(lambda: self._convert_to_group(artist))

            self.wiki_action = menu.addAction("Wikipedia Search")
            self.wiki_action.setToolTip("Search Wikipedia for artist information")
            self.wiki_action.triggered.connect(self.search_wikipedia)
            self.influences_action = menu.addAction("Edit Influences")
            self.influences_action.triggered.connect(self.edit_influences)
            self.pic_action = menu.addAction("Add Artist Image")
            self.pic_action.triggered.connect(self.add_profile_picture)
            delete_action = menu.addAction("🗑️ Delete Artist")
            delete_action.triggered.connect(lambda: self._delete_artist(artist))

        # Always show add artist action
        add_action = menu.addAction("➕ Add Artist")
        add_action.triggered.connect(self.add_new_artist)

        add_group_action = menu.addAction("👥 Add Group")
        add_group_action.triggered.connect(self.add_new_group)

        menu.exec_(self.artist_list.mapToGlobal(position))

    def _on_artist_selected(self):
        """Display selected artist/group detail tab"""
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

    def _add_member(self, group):
        """Add member to group"""

        dialog = AddMemberDialog(self.controller, group, self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _convert_to_group(self, artist):
        """Convert individual artist to group"""
        try:
            self.controller.update.update_entity("Artist", artist.artist_id, isgroup=1)
            self.load_artists()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    def _convert_to_individual(self, group):
        """Convert group to individual artist"""
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
                    self, "Success", "Group converted to individual"
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    def add_new_group(self):
        """Add new group using existing dialog"""

        dialog = AddGroupDialog(self.controller, self)
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _close_tab(self, index):
        """Close tab at the specified index"""
        self.tab_widget.removeTab(index)

    # ----------------------------
    # Dialog Operations
    # ----------------------------
    def add_new_artist(self):
        """Use an inline prompt instead of a dialog for a smoother flow."""
        name, ok = QInputDialog.getText(self, "Add Artist", "Artist name:")
        if ok and name.strip():
            try:
                self.controller.add.add_entity("Artist", artist_name=name.strip())
                self.load_artists()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to add artist: {e}")

    def _split_artist(self, artist=None):
        """Split artist inline."""
        # If artist is not provided, get it from selection
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

    def find_fuzzy_matches(self):
        """Quick fuzzy match check."""

        artists = self.controller.get.get_all_entities("Artist")
        matches = [
            (a, b, fuzz.token_sort_ratio(a.artist_name, b.artist_name))
            for i, a in enumerate(artists)
            for b in artists[i + 1 :]
            if fuzz.token_sort_ratio(a.artist_name, b.artist_name) >= 65
        ]

        if not matches:
            QMessageBox.information(self, "No Matches", "No fuzzy matches found.")
            return

        dialog = FuzzyMatchDialog(matches, self.controller, self)
        dialog.exec_()
        self.load_artists()

    def _filter_artists(self):
        """Filter artists based on search text and current mode"""
        search_text = self.search_box.text().lower()

        if not search_text:
            # Show all artists in current mode
            self.artist_list.clear()
            for artist in self.all_artists:
                display_name = artist.artist_name
                if getattr(artist, "isgroup", 0):
                    display_name = f"👥 {display_name}"

                item = QListWidgetItem(display_name)
                item.setData(Qt.UserRole, artist.artist_id)
                self.artist_list.addItem(item)
        else:
            # Filter artists based on search text
            self.artist_list.clear()
            for artist in self.all_artists:
                if search_text in artist.artist_name.lower():
                    display_name = artist.artist_name
                    if getattr(artist, "isgroup", 0):
                        display_name = f"👥 {display_name}"

                    item = QListWidgetItem(display_name)
                    item.setData(Qt.UserRole, artist.artist_id)
                    self.artist_list.addItem(item)

    def _add_award(self):
        dialog = AddAwardDialog(
            self.controller, "Artist", self.artist_list.currentItem().artist_id, self
        )

        if dialog.exec() == QDialog.Accepted:
            self.show_success("Award added successfully")

    def search_wikipedia(self):
        """Search Wikipedia for artist information"""
        if not self.controller:
            self.show_error("No controller available")
            return

        # Search Wikipedia
        title, summary, full_content, link, images = search_wikipedia(
            self.artist_list.currentItem().artist_name, self
        )

        if not link:  # User cancelled or error occurred
            return

        # Ask user what to do
        dialog = WikipediaImportDialog(title, summary, link, images, self)
        if dialog.exec() == QDialog.Accepted:
            updates = dialog.get_updates()

            # Apply updates
            if updates:
                success = self.controller.update.update_entity(
                    "Artist", self.artist_list.currentItem().artist_id, **updates
                )

                if success:
                    # Update local artist object
                    for key, value in updates.items():
                        setattr(self.artist_list.currentItem(), key, value)

                    self.show_success("Wikipedia information imported")
                    self.artist_updated.emit()
                else:
                    self.show_error("Failed to import Wikipedia information")

    def _add_place(self):
        """Add a place association to the artist"""
        # Open place selection dialog
        dialog = PlaceSelectionDialog(self.controller, self)
        if dialog.exec() == QDialog.Accepted:
            place_id, association_type = dialog.get_selection()

            if place_id:
                # Add place association
                new_assoc = self.controller.add.add_entity(
                    "PlaceAssociation",
                    place_id=place_id,
                    entity_id=self.artist_list.currentItem().artist_id,
                    entity_type="Artist",
                    association_type=association_type or "associated",
                )

                if new_assoc:
                    self.show_success("Place association added")

                    # Refresh places widget if available
                    if hasattr(self.places_widget, "refresh_places"):
                        self.places_widget.refresh_places()
                    self.artist_updated.emit()
                else:
                    self.show_error("Failed to add place association")

    def _merge_artist(self):
        """Merge this artist with another artist"""
        if not self.controller:
            self.show_error("No controller available")
            return

        # Open artist selection dialog for merge target
        dialog = ArtistSelectionDialog(
            self.controller,
            "Select Artist to Merge Into",
            exclude_id=self.artist_list.currentItem().artist_id,
            parent=self,
        )

        if dialog.exec() == QDialog.Accepted:
            target_artist = dialog.get_selected_artist()

            if target_artist:
                # Confirm merge
                reply = QMessageBox.question(
                    self,
                    "Confirm Merge",
                    f"Merge '{self.artist_list.currentItem().artist_name}' (ID: {self.artist_list.currentItem().artist_id}) "
                    f"into '{target_artist.artist_name}' (ID: {target_artist.artist_id})?\n\n"
                    f"This action cannot be undone!",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )

                if reply == QMessageBox.Yes:
                    # Perform merge
                    success = self.controller.merge.merge_entities(
                        "Artist",
                        self.artist_list.currentItem().artist_id,
                        target_artist.artist_id,
                    )

                    if success:
                        # Signal that artist was merged (parent should handle closing)
                        if self.artist_detail_tab:
                            # Emit signal or call method to close/refresh
                            if hasattr(self.artist_detail_tab, "artist_merged"):
                                self.artist_detail_tab.artist_merged.emit(
                                    self.artist.artist_id, target_artist.artist_id
                                )
                    else:
                        self.show_error("Failed to merge artists")

    def edit_influences(self):
        """Open dialog to edit influences"""
        artists = self.controller.get.get_all_entities("Artist")
        all_artists = [(artist.artist_id, artist.artist_name) for artist in artists]
        dialog = AddInfluenceDialog(self.controller, all_artists)
        if dialog.exec() == QDialog.Accepted:
            self.artist_updated.emit()

    def add_profile_picture(self):
        """Add or change artist profile picture"""
        # Use native dialog option for Wayland compatibility
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Profile Picture",
            "",
            "Image Files (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
            options=QFileDialog.DontUseNativeDialog,  # <--- Crucial for Wayland
        )

        if not file_path:
            return

        # Update artist profile picture path
        success = self.controller.update.update_entity(
            "Artist",
            self.artist_list.currentItem().artist_id,
            profile_pic_path=file_path,
        )

        if success:
            self.show_success("Profile picture updated")
            self.artist_list.currentItem().profile_pic_path = file_path
            self.artist_updated.emit()
        else:
            self.show_error("Failed to update profile picture")

    def _delete_artist(self, artist):
        try:
            self.controller.delete.delete_entity("Artist", artist_id=artist.artist_id)
            self.load_artists()
        except:  # noqa: E722
            pass


class PlaceSelectionDialog(QDialog):
    """Dialog for selecting a place to associate with artist"""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.selected_place_id = None
        self.selected_association_type = "associated"
        self.init_ui()

    def init_ui(self):
        """Initialize dialog UI"""
        self.setWindowTitle("Add Place Association")
        self.setModal(True)
        layout = QVBoxLayout(self)

        # Place selection
        layout.addWidget(QLabel("Select Place:"))

        # Get all places
        places = self.controller.get.get_all_entities("Place")

        self.place_combo = QComboBox()
        self.place_combo.addItem("-- Select a place --", None)

        for place in sorted(places, key=lambda p: p.place_name):
            self.place_combo.addItem(place.place_name, place.place_id)

        layout.addWidget(self.place_combo)

        # Association type
        layout.addWidget(QLabel("Association Type:"))

        self.type_combo = QComboBox()
        self.type_combo.addItems(
            [
                "associated",
                "born",
                "died",
                "worked",
                "lived",
                "formed",
                "based",
                "recorded",
            ]
        )

        layout.addWidget(self.type_combo)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept_selection)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept_selection(self):
        """Accept the current selection"""
        self.selected_place_id = self.place_combo.currentData()
        self.selected_association_type = self.type_combo.currentText()

        if not self.selected_place_id:
            QMessageBox.warning(self, "Selection Required", "Please select a place.")
            return

        self.accept()

    def get_selection(self):
        """Get the selected place and association type"""
        return self.selected_place_id, self.selected_association_type


class ArtistSelectionDialog(QDialog):
    """Dialog for selecting another artist"""

    def __init__(self, controller, title="Select Artist", exclude_id=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.dialog_title = title
        self.exclude_id = exclude_id
        self.selected_artist = None
        self.init_ui()

    def init_ui(self):
        """Initialize dialog UI"""
        self.setWindowTitle(self.dialog_title)
        self.setModal(True)
        layout = QVBoxLayout(self)

        # Artist selection
        layout.addWidget(QLabel("Select Artist:"))

        # Get all artists (excluding the current one if specified)
        all_artists = self.controller.get.get_all_entities("Artist")

        self.artist_combo = QComboBox()
        self.artist_combo.addItem("-- Select an artist --", None)

        for artist in sorted(all_artists, key=lambda a: a.artist_name):
            if self.exclude_id and artist.artist_id == self.exclude_id:
                continue

            self.artist_combo.addItem(artist.artist_name, artist)

        layout.addWidget(self.artist_combo)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept_selection)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept_selection(self):
        """Accept the current selection"""
        self.selected_artist = self.artist_combo.currentData()

        if not self.selected_artist:
            QMessageBox.warning(self, "Selection Required", "Please select an artist.")
            return

        self.accept()

    def get_selected_artist(self):
        """Get the selected artist"""
        return self.selected_artist
