"""
mood_view_actions.py

Context-menu and CRUD/view-tracks action handlers for MoodView: create,
edit, delete (single and multi), and open the tracks window for one or
more selected moods.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.hierarchy_tree_style import handle_insert_as_new_relative
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.mood.mood_dialog import MoodDialog
from src.mood.mood_tracks import MoodTracksWindow
from src.track.view.base_track_view import BaseTrackView


class MoodActionsMixin:
    """
    Expects the host class to provide: self.controller, self.mood_tree,
    self.moods_data, self.current_mood_id, self.load_moods(), and the
    mood_created/mood_updated/mood_deleted signals.
    """

    def show_context_menu(self, position):
        """Show context menu for mood tree"""
        item = self.mood_tree.itemAt(position)
        menu = QMenu(self)

        # Always show "New Mood" option
        new_action = QAction("New Mood", self)
        new_action.triggered.connect(self.show_new_mood_dialog)
        menu.addAction(new_action)

        # A right-click on an item that's part of a multi-selection keeps the
        # whole selection (standard Qt behavior); otherwise it's just this item.
        selected_items = self.mood_tree.selectedItems()
        if item and item not in selected_items:
            selected_items = [item]
        real_selected = [it for it in selected_items if it.data(0, Qt.UserRole) is not None]

        if len(real_selected) > 1:
            menu.addSeparator()
            view_tracks_action = QAction(f"View Tracks ({len(real_selected)} moods)", self)
            view_tracks_action.triggered.connect(
                lambda: self.view_tracks_for_selected_moods(real_selected)
            )
            menu.addAction(view_tracks_action)

            menu.addSeparator()

            delete_action = QAction(f"Delete {len(real_selected)} Moods", self)
            delete_action.triggered.connect(lambda: self.delete_selected_moods(real_selected))
            menu.addAction(delete_action)
        # Only show edit/delete if we have a single real mood selected
        elif item and item.data(0, Qt.UserRole) is not None:
            menu.addSeparator()

            # Item-specific actions
            view_tracks_action = QAction("View Tracks", self)
            view_tracks_action.triggered.connect(lambda: self.view_tracks_for_selected_mood())
            menu.addAction(view_tracks_action)

            edit_action = QAction("Edit Mood", self)
            edit_action.triggered.connect(lambda: self.edit_selected_mood())
            menu.addAction(edit_action)

            menu.addSeparator()

            new_parent_action = QAction("New Parent Mood", self)
            new_parent_action.triggered.connect(lambda: self.create_new_parent_mood())
            menu.addAction(new_parent_action)

            new_child_action = QAction("New Child Mood", self)
            new_child_action.triggered.connect(lambda: self.create_new_child_mood())
            menu.addAction(new_child_action)

            menu.addSeparator()

            delete_action = QAction("Delete Mood", self)
            delete_action.triggered.connect(self.delete_selected_mood)
            menu.addAction(delete_action)

        menu.exec_(self.mood_tree.viewport().mapToGlobal(position))

    def show_new_mood_dialog(self):
        """Show dialog to create new mood"""
        dialog = MoodDialog(controller=self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            mood_data = dialog.get_mood_data()
            try:
                new_mood = self.controller.add.add_entity("Mood", **mood_data)
                self.mood_created.emit(new_mood)
                self.load_moods()  # Reload to reflect changes
            except SQLAlchemyError as e:
                logger.error(f"Error creating mood: {e}")
                QMessageBox.critical(self, "Error", f"Failed to create mood: {e!s}")

    def view_tracks_for_selected_mood(self):
        """Open tracks view window for selected mood."""
        if not self.current_mood_id:
            show_status_message(self, "Please select a mood first.")
            return

        mood = next((m for m in self.moods_data if m.mood_id == self.current_mood_id), None)
        if mood:
            tracks_window = MoodTracksWindow(self.controller, mood, self)
            tracks_window.show()

    def view_tracks_for_selected_moods(self, items):
        """Open a combined, deduplicated tracks view for multiple selected moods."""
        mood_ids = [it.data(0, Qt.UserRole) for it in items]

        try:
            associations = self.controller.get.get_all_entities(
                "MoodTrackAssociation", mood_id__in=mood_ids
            )
            track_ids = list({a.track_id for a in associations})
            tracks = (
                self.controller.get.get_all_entities("Track", track_id__in=track_ids)
                if track_ids
                else []
            )
        except SQLAlchemyError as e:
            logger.error(f"Error loading tracks for selected moods: {e}")
            QMessageBox.critical(self, "Error", "Failed to load tracks for moods")
            return

        names = ", ".join(it.text(0) for it in items)
        tracks_window = BaseTrackView(
            controller=self.controller,
            tracks=tracks,
            title=f"Tracks in {len(mood_ids)} moods: {names}",
        )
        tracks_window.exec_()

    def edit_selected_mood(self):
        """Edit the currently selected mood"""
        if not self.current_mood_id:
            return

        mood = next((m for m in self.moods_data if m.mood_id == self.current_mood_id), None)
        if not mood:
            return

        dialog = MoodDialog(mood_data=mood, controller=self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            mood_data = dialog.get_mood_data()
            try:
                self.controller.update.update_entity("Mood", self.current_mood_id, **mood_data)
                self.mood_updated.emit(self.current_mood_id, mood_data)
                self.load_moods()  # Reload to reflect changes
            except SQLAlchemyError as e:
                logger.error(f"Error updating mood: {e}")
                QMessageBox.critical(self, "Error", f"Failed to update mood: {e!s}")

    def create_new_parent_mood(self):
        """Create a new mood and insert it as the parent of the current mood."""
        # The new mood takes over the mood's old parent slot (preserving the
        # grandparent chain), and the current mood becomes a child of the new mood.
        if not self.current_mood_id:
            return

        mood = next((m for m in self.moods_data if m.mood_id == self.current_mood_id), None)
        if not mood:
            show_status_message(self, "The selected mood no longer exists.")
            return

        dialog = MoodDialog(controller=self.controller, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return

        mood_data = dialog.get_mood_data()
        try:
            new_mood = self.controller.add.add_entity("Mood", **mood_data)
            if not new_mood:
                raise ValueError("Failed to create new mood")
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error creating new parent mood: {e}")
            QMessageBox.critical(self, "Error", f"Failed to create new parent mood: {e!s}")
            return

        handle_insert_as_new_relative(
            self.controller,
            self,
            entity_type="Mood",
            id_attr="mood_id",
            name_attr="mood_name",
            is_parent=True,
            entity=mood,
            new_entity=new_mood,
            reload_fn=self.load_moods,
            emit_fn=self.mood_created.emit,
            status_fn=lambda msg: show_status_message(self, msg),
            exception_types=(SQLAlchemyError, ValueError),
            include_error_detail=True,
        )

    def create_new_child_mood(self):
        """Create a new mood and set it as a child of the current mood."""
        if not self.current_mood_id:
            return

        mood = next((m for m in self.moods_data if m.mood_id == self.current_mood_id), None)
        if not mood:
            show_status_message(self, "The selected mood no longer exists.")
            return

        dialog = MoodDialog(controller=self.controller, parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return

        mood_data = dialog.get_mood_data()
        try:
            new_mood = self.controller.add.add_entity("Mood", **mood_data)
            if not new_mood:
                raise ValueError("Failed to create new mood")
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error creating new child mood: {e}")
            QMessageBox.critical(self, "Error", f"Failed to create new child mood: {e!s}")
            return

        handle_insert_as_new_relative(
            self.controller,
            self,
            entity_type="Mood",
            id_attr="mood_id",
            name_attr="mood_name",
            is_parent=False,
            entity=mood,
            new_entity=new_mood,
            reload_fn=self.load_moods,
            emit_fn=self.mood_created.emit,
            status_fn=lambda msg: show_status_message(self, msg),
            exception_types=(SQLAlchemyError, ValueError),
            include_error_detail=True,
        )

    def delete_selected_mood(self):
        """Delete the currently selected mood"""
        if not self.current_mood_id:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this mood and all its associations?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            try:
                self.controller.delete.delete_entity("Mood", self.current_mood_id)
                self.mood_deleted.emit(self.current_mood_id)
                self.load_moods()  # Reload the list
                show_status_message(self, "Mood deleted successfully.")
            except SQLAlchemyError as e:
                logger.error(f"Error deleting mood: {e}")
                QMessageBox.critical(self, "Error", f"Failed to delete mood: {e!s}")

    def delete_selected_moods(self, items):
        """Delete several selected moods and all their associations."""
        mood_ids = [it.data(0, Qt.UserRole) for it in items if it.data(0, Qt.UserRole) is not None]
        if not mood_ids:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete {len(mood_ids)} moods and all their associations?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        deleted = 0
        for mood_id in mood_ids:
            try:
                if self.controller.delete.delete_entity("Mood", mood_id):
                    self.mood_deleted.emit(mood_id)
                    deleted += 1
            except SQLAlchemyError as e:
                logger.error(f"Error deleting mood {mood_id}: {e}")

        self.load_moods()  # Reload the list
        if deleted == len(mood_ids):
            show_status_message(self, f"Deleted {deleted} moods successfully.")
        else:
            show_status_message(
                self, f"Deleted {deleted} of {len(mood_ids)} moods; see log for details."
            )
