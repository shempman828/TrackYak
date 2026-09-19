"""
artist_view_actions.py

CRUD/action handlers for ArtistView: create/edit/convert/split/merge/
delete an artist, group membership, awards, places, influences, and
profile picture management. Each follows the same
try/except-dialog-then-reload pattern.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.artist.artist_edit import ArtistEditor
from src.artist.artist_group_dialog import AddGroupDialog, AddMemberDialog
from src.artist.artist_image_manager import move_to_artist_images_dir
from src.artist.artist_place import PlaceSelectionDialog
from src.award.award_new import AddAwardDialog
from src.common.base_merge_dialog import MergeDBDialog
from src.common.base_split_dialog import SplitDBDialog
from src.foundation.status_utility import show_status_message
from src.influences.influences_dialog import AddInfluenceDialog
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)


class ArtistActionsMixin:
    """
    Expects the host class to provide: self.controller, self.artist_list,
    self.load_artists(), self._on_artist_selected(), and to be a QWidget
    subclass.
    """

    def add_new_artist(self):
        """Open the ArtistEditor dialog to add a new individual artist."""
        try:
            dialog = ArtistEditor(self.controller, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to open artist editor: {e}")

    def add_new_group(self):
        """Open the AddGroupDialog to create a new group."""
        try:
            dialog = AddGroupDialog(self.controller, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to open group dialog: {e}")

    def _edit_artist(self, artist):
        """Open the ArtistEditor dialog for an existing artist."""
        try:
            dialog = ArtistEditor(self.controller, artist=artist, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
                self._on_artist_selected()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to open artist editor: {e}")

    def _add_member(self, group_artist):
        """Open the AddMemberDialog to add a member to a group."""
        try:
            dialog = AddMemberDialog(self.controller, group_artist, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                self._on_artist_selected()
        except SQLAlchemyError as e:
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
                show_status_message(self, "No groups exist yet.")
                return

            name, ok = QInputDialog.getItem(
                self, "Add to Group", "Select a group:", group_names, editable=False
            )
            if ok and name:
                group = next((g for g in groups if g.artist_name == name), None)
                if group:
                    self.controller.add.add_entity(
                        "GroupMembership", group_id=group.artist_id, member_id=artist.artist_id
                    )
                    self._on_artist_selected()
        except SQLAlchemyError as e:
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
                self.controller.update.update_entity("Artist", artist.artist_id, isgroup=1)
                self.load_artists()
            except SQLAlchemyError as e:
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
                self.controller.update.update_entity("Artist", artist.artist_id, isgroup=0)
                self.load_artists()
            except SQLAlchemyError as e:
                QMessageBox.critical(self, "Error", f"Failed to convert: {e}")

    def _split_artist(self, artist):
        """Split this artist record into two separate artists."""
        if not artist:
            return

        dialog = SplitDBDialog(
            self.controller.split, "Artist", artist, self, get_helper=self.controller.get
        )
        if dialog.exec_() == QDialog.Accepted:
            self.load_artists()

    def _merge_artist(self, artist):
        """Open the merge dialog with this artist pre-selected as the source."""
        try:
            dialog = MergeDBDialog(self.controller, "Artist", self)

            # Pre-populate the source side so the user only needs to pick a target.
            # We set the entity directly then call the dialog's own update methods
            # to reflect the selection in its UI (info label, highlights, buttons).
            dialog.source_entity = artist
            dialog.source_info.setText(dialog._build_entity_info(artist, "source"))
            dialog.source_search.setText(artist.artist_name)
            dialog._update_list(artist.artist_name, "source")
            dialog._highlight_selected_entities()
            dialog.target_find_similar_btn.setEnabled(True)
            dialog._auto_suggest_similar(artist, "target")
            dialog._update_action_buttons()

            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to open merge dialog: {e}")

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
                self.controller.delete.delete_entity("Artist", artist_id=artist.artist_id)
                self.load_artists()
            except SQLAlchemyError as e:
                QMessageBox.critical(self, "Error", f"Failed to delete artist: {e}")

    def _add_award(self, artist):
        """Open the Add Award dialog for an artist."""
        try:
            dialog = AddAwardDialog(self.controller, "Artist", artist.artist_id, self)
            if dialog.exec_() == QDialog.Accepted:
                self._on_artist_selected()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to add award: {e}")

    def _add_place(self, artist):
        """Open the Place Selection dialog for an artist."""
        try:
            dialog = PlaceSelectionDialog(self.controller, self)
            if dialog.exec_() == QDialog.Accepted:
                place_id = dialog.selected_place_id
                if place_id:
                    known_types = fetch_association_types(self.controller)
                    assoc_type = find_or_create_association_type(
                        self.controller, dialog.selected_association_type, known_types
                    )
                    self.controller.add.add_entity(
                        "PlaceAssociation",
                        place_id=place_id,
                        entity_id=artist.artist_id,
                        entity_type="Artist",
                        association_type_id=assoc_type.association_type_id if assoc_type else None,
                    )
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to add place: {e}")

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

        artist = self.controller.get.get_entity_object("Artist", artist_id=artist_id)
        managed_path = move_to_artist_images_dir(
            artist_id, artist.artist_name if artist else "", file_path
        )

        success = self.controller.update.update_entity(
            "Artist", artist_id, profile_pic_path=managed_path
        )
        if success:
            show_status_message(self, "Profile picture updated.")
        else:
            QMessageBox.warning(self, "Error", "Failed to update profile picture.")
