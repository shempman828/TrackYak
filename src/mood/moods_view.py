from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.hierarchy_tree_style import (
    collect_expanded_ids,
    configure_hierarchy_tree,
    create_colored_icon,
    filter_tree_widget,
    icon_for_depth,
    is_hierarchy_descendant,
    restore_expanded_ids_or_expand_all,
)
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.mood.mood_dialog import MoodDialog
from src.mood.mood_tracks import MoodTracksWindow


class MoodView(QWidget):
    """Class to show and manage moods hierarchy"""

    # Signals
    mood_selected = Signal(int)  # mood_id
    mood_created = Signal(object)  # mood_object
    mood_updated = Signal(int, dict)  # mood_id, mood_data
    mood_deleted = Signal(int)  # mood_id

    def __init__(self, controller: any):
        super().__init__()
        self.controller = controller
        self.current_mood_id = None
        self.moods_data = []
        self.setWindowTitle("Moods|Folksonomy")
        self.setGeometry(100, 100, 1000, 700)
        self.init_ui()
        self.load_moods()

    def init_ui(self):
        """Initialize the user interface"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Header with title and controls
        header_layout = QHBoxLayout()

        title = QLabel("Moods|Folksonomy")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Expand/Collapse all buttons
        btn_expand_all = QPushButton("Expand All")
        btn_expand_all.setToolTip("Expand all items in the tree")
        btn_expand_all.clicked.connect(lambda: self.mood_tree.expandAll())
        header_layout.addWidget(btn_expand_all)

        btn_collapse_all = QPushButton("Collapse All")
        btn_collapse_all.setToolTip("Collapse all items in the tree")
        btn_collapse_all.clicked.connect(lambda: self.mood_tree.collapseAll())
        header_layout.addWidget(btn_collapse_all)

        main_layout.addLayout(header_layout)

        # Search box
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search moods...")
        self.search_box.textChanged.connect(self.filter_moods)
        main_layout.addWidget(self.search_box)

        # Moods tree
        self.mood_tree = QTreeWidget()
        configure_hierarchy_tree(self.mood_tree)
        self.mood_tree.itemSelectionChanged.connect(self.on_mood_selected)
        self.mood_tree.customContextMenuRequested.connect(self.show_context_menu)

        # Override drop event to handle parent updates
        self.mood_tree.dropEvent = self.handle_drop_event

        main_layout.addWidget(self.mood_tree)

        # Statistics
        stats_group = QGroupBox("Statistics")
        stats_layout = QHBoxLayout()

        self.stats_label = QLabel("Total moods: 0")
        stats_layout.addWidget(self.stats_label)

        self.tracks_count_label = QLabel("Tracks with moods: 0")
        stats_layout.addWidget(self.tracks_count_label)

        stats_layout.addStretch()
        stats_group.setLayout(stats_layout)
        main_layout.addWidget(stats_group)

        self.setLayout(main_layout)

    def load_moods(self):
        """Load moods from database and build hierarchy"""
        try:
            self.moods_data = self.controller.get.get_all_entities("Mood")
            self.build_mood_tree()
            self.update_statistics()
        except (SQLAlchemyError, AttributeError) as e:
            logger.error(f"Error loading moods: {e}")
            QMessageBox.warning(self, "Error", f"Failed to load moods: {str(e)}")

    def handle_drop_event(self, event):
        """Handle drop event to update parent relationship in database"""
        try:
            # Get the dragged item
            dragged_item = self.mood_tree.currentItem()
            if not dragged_item:
                event.ignore()
                return

            dragged_mood_id = dragged_item.data(0, Qt.UserRole)
            if not dragged_mood_id:
                event.ignore()
                return

            # Get drop position
            drop_pos = event.pos()
            target_item = self.mood_tree.itemAt(drop_pos)

            # Determine new parent based on drop position
            if target_item:
                # Dropped on an item (becomes child)
                new_parent_id = target_item.data(0, Qt.UserRole)
            else:
                # Dropped in empty space (becomes root)
                new_parent_id = None

            # Prevent circular reference
            if new_parent_id and (
                dragged_mood_id == new_parent_id
                or is_hierarchy_descendant(
                    dragged_mood_id,
                    new_parent_id,
                    self.moods_data,
                    id_attr="mood_id",
                )
            ):
                show_status_message(
                    self, "Cannot make a mood a child of itself or its descendants."
                )
                event.ignore()
                return

            # Get current parent from database
            current_mood = next(
                (m for m in self.moods_data if m.mood_id == dragged_mood_id), None
            )
            if current_mood and current_mood.parent_id == new_parent_id:
                event.ignore()  # No change needed
                return

            # Update the database
            self.controller.update.update_entity(
                "Mood", dragged_mood_id, parent_id=new_parent_id
            )
            self.mood_updated.emit(dragged_mood_id, {"parent_id": new_parent_id})

            # Allow the default drop to handle visual update
            super(QTreeWidget, self.mood_tree).dropEvent(event)

            # Reload to ensure consistency
            self.load_moods()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error handling drop event: {e}")
            event.ignore()
            # Revert UI on error
            self.load_moods()

    def build_mood_tree(self):
        """Build hierarchical tree from flat moods list with color coding"""
        root = self.mood_tree.invisibleRootItem()
        had_items = root.childCount() > 0
        expanded_ids = collect_expanded_ids(self.mood_tree)
        self.mood_tree.clear()

        # Get track counts for all moods
        mood_track_counts = self.get_track_counts_for_all_moods()

        # If no moods exist, show a helpful message
        if not self.moods_data:
            item = QTreeWidgetItem(self.mood_tree, ["No moods found"])
            item.setData(0, Qt.UserRole, None)

            # Add instructional sub-items
            tip1 = QTreeWidgetItem(item, ["Right-click to create your first mood"])
            tip1.setData(0, Qt.UserRole, None)
            tip1.setIcon(
                0, create_colored_icon(QColor(100, 149, 237))
            )  # Cornflower blue

            tip2 = QTreeWidgetItem(item, ["Moods can be organized in a hierarchy"])
            tip2.setData(0, Qt.UserRole, None)
            tip2.setIcon(
                0, create_colored_icon(QColor(60, 179, 113))
            )  # Medium sea green

            tip3 = QTreeWidgetItem(item, ["Drag and drop to reorder moods"])
            tip3.setData(0, Qt.UserRole, None)
            tip3.setIcon(0, create_colored_icon(QColor(255, 165, 0)))  # Orange

            self.mood_tree.expandAll()
            return

        # Create a dictionary for quick lookup
        mood_dict = {mood.mood_id: mood for mood in self.moods_data}

        # Find root moods (no parent or parent not in current list)
        root_moods = sorted(
            [
                mood
                for mood in self.moods_data
                if not mood.parent_id or mood.parent_id not in mood_dict
            ],
            key=lambda m: m.mood_name.lower(),
        )

        # Recursively build tree with depth tracking
        def add_children(parent_item, parent_mood, depth):
            children = sorted(
                [m for m in self.moods_data if m.parent_id == parent_mood.mood_id],
                key=lambda m: m.mood_name.lower(),
            )
            for child in children:
                # Get track count for this mood
                track_count = mood_track_counts.get(child.mood_id, 0)
                display_name = (
                    f"{child.mood_name} ({track_count})"
                    if track_count > 0
                    else child.mood_name
                )

                child_item = QTreeWidgetItem(parent_item, [display_name])
                child_item.setData(0, Qt.UserRole, child.mood_id)
                child_item.setData(0, Qt.UserRole + 1, child)  # Store full mood object

                # Set color based on depth
                self._set_mood_item_style(child_item, depth, mood_track_counts)

                add_children(child_item, child, depth + 1)

        # Add root moods to tree
        for mood in root_moods:
            # Get track count for this mood
            track_count = mood_track_counts.get(mood.mood_id, 0)
            display_name = (
                f"{mood.mood_name} ({track_count})"
                if track_count > 0
                else mood.mood_name
            )

            item = QTreeWidgetItem(self.mood_tree, [display_name])
            item.setData(0, Qt.UserRole, mood.mood_id)
            item.setData(0, Qt.UserRole + 1, mood)  # Store full mood object

            # Set color for root items (depth 0)
            self._set_mood_item_style(item, 0, mood_track_counts)

            add_children(item, mood, 1)

        # Restore expansion state, or expand everything on first build
        restore_expanded_ids_or_expand_all(self.mood_tree, expanded_ids, not had_items)

    def get_track_counts_for_all_moods(self):
        """Get track counts for all moods in the database"""
        try:
            # Get all mood track associations
            all_associations = self.controller.get.get_all_entities(
                "MoodTrackAssociation"
            )

            # Count tracks per mood
            mood_track_counts = {}
            for association in all_associations:
                mood_id = None
                if hasattr(association, "mood_id"):
                    mood_id = association.mood_id
                elif hasattr(association, "mood") and association.mood:
                    mood_id = association.mood.mood_id

                if mood_id:
                    mood_track_counts[mood_id] = mood_track_counts.get(mood_id, 0) + 1

            return mood_track_counts
        except SQLAlchemyError as e:
            logger.error(f"Error getting track counts: {e}")
            return {}

    def _set_mood_item_style(self, item, depth, track_counts):
        """Set color and styling for mood tree items based on depth"""
        # Get the original mood name from stored data
        mood_obj = item.data(0, Qt.UserRole + 1)
        if not mood_obj:
            return

        # Remove any existing count and keep only the base name
        original_name = mood_obj.mood_name

        # Get track count for this mood
        track_count = track_counts.get(mood_obj.mood_id, 0)

        # Build display name with count
        display_name = (
            f"{original_name} ({track_count})" if track_count > 0 else original_name
        )

        item.setText(0, display_name)
        item.setIcon(0, icon_for_depth(depth))

        # Add tooltip with mood information including track count
        if mood_obj:
            tooltip = f"ID: {mood_obj.mood_id}"
            if hasattr(mood_obj, "description") and mood_obj.description:
                tooltip += f"\nDescription: {mood_obj.description}"
            if hasattr(mood_obj, "parent_id") and mood_obj.parent_id:
                parent_mood = next(
                    (m for m in self.moods_data if m.mood_id == mood_obj.parent_id),
                    None,
                )
                if parent_mood:
                    tooltip += f"\nParent: {parent_mood.mood_name}"

            # Add track count to tooltip
            tooltip += f"\nAssociated tracks: {track_count}"

            item.setToolTip(0, tooltip)

    def get_child_mood_ids(self, parent_mood_id):
        """Get all child mood IDs recursively for a given parent mood"""
        child_ids = []

        def collect_children(mood_id):
            children = [m.mood_id for m in self.moods_data if m.parent_id == mood_id]
            for child_id in children:
                child_ids.append(child_id)
                collect_children(child_id)

        collect_children(parent_mood_id)
        return child_ids

    def on_mood_selected(self):
        """Handle mood selection from tree"""
        selected_items = self.mood_tree.selectedItems()
        if not selected_items:
            self.current_mood_id = None
            return

        mood_item = selected_items[0]
        mood_id = mood_item.data(0, Qt.UserRole)
        self.current_mood_id = mood_id
        self.mood_selected.emit(mood_id)

    def show_context_menu(self, position):
        """Show context menu for mood tree"""
        item = self.mood_tree.itemAt(position)
        menu = QMenu(self)

        # Always show "New Mood" option
        new_action = QAction("New Mood", self)
        new_action.triggered.connect(self.show_new_mood_dialog)
        menu.addAction(new_action)

        # Only show edit/delete if we have a real mood selected
        if item and item.data(0, Qt.UserRole) is not None:
            menu.addSeparator()

            # Item-specific actions
            view_tracks_action = QAction("View Tracks", self)
            view_tracks_action.triggered.connect(
                lambda: self.view_tracks_for_selected_mood()
            )
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
                QMessageBox.critical(self, "Error", f"Failed to create mood: {str(e)}")

    def view_tracks_for_selected_mood(self):
        """Open tracks view window for selected mood."""
        if not self.current_mood_id:
            show_status_message(self, "Please select a mood first.")
            return

        mood = next(
            (m for m in self.moods_data if m.mood_id == self.current_mood_id), None
        )
        if mood:
            tracks_window = MoodTracksWindow(self.controller, mood, self)
            tracks_window.show()

    def edit_selected_mood(self):
        """Edit the currently selected mood"""
        if not self.current_mood_id:
            return

        mood = next(
            (m for m in self.moods_data if m.mood_id == self.current_mood_id), None
        )
        if not mood:
            return

        dialog = MoodDialog(mood_data=mood, controller=self.controller, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            mood_data = dialog.get_mood_data()
            try:
                self.controller.update.update_entity(
                    "Mood", self.current_mood_id, **mood_data
                )
                self.mood_updated.emit(self.current_mood_id, mood_data)
                self.load_moods()  # Reload to reflect changes
            except SQLAlchemyError as e:
                logger.error(f"Error updating mood: {e}")
                QMessageBox.critical(self, "Error", f"Failed to update mood: {str(e)}")

    def create_new_parent_mood(self):
        """Create a new mood and insert it as the parent of the current mood.

        The new mood takes over the mood's old parent slot (preserving the
        grandparent chain), and the current mood becomes a child of the new mood.
        """
        if not self.current_mood_id:
            return

        mood = next(
            (m for m in self.moods_data if m.mood_id == self.current_mood_id), None
        )
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

            self.controller.update.update_entity(
                "Mood", new_mood.mood_id, parent_id=mood.parent_id
            )
            self.controller.update.update_entity(
                "Mood", mood.mood_id, parent_id=new_mood.mood_id
            )
            self.mood_created.emit(new_mood)
            self.load_moods()
            show_status_message(
                self,
                f"Created '{new_mood.mood_name}' as parent of '{mood.mood_name}'",
            )
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error creating new parent mood: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to create new parent mood: {str(e)}"
            )

    def create_new_child_mood(self):
        """Create a new mood and set it as a child of the current mood."""
        if not self.current_mood_id:
            return

        mood = next(
            (m for m in self.moods_data if m.mood_id == self.current_mood_id), None
        )
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

            self.controller.update.update_entity(
                "Mood", new_mood.mood_id, parent_id=mood.mood_id
            )
            self.mood_created.emit(new_mood)
            self.load_moods()
            show_status_message(
                self,
                f"Created '{new_mood.mood_name}' as child of '{mood.mood_name}'",
            )
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Error creating new child mood: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to create new child mood: {str(e)}"
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
        )

        if reply == QMessageBox.Yes:
            try:
                self.controller.delete.delete_entity("Mood", self.current_mood_id)
                self.mood_deleted.emit(self.current_mood_id)
                self.load_moods()  # Reload the list
                show_status_message(self, "Mood deleted successfully.")
            except SQLAlchemyError as e:
                logger.error(f"Error deleting mood: {e}")
                QMessageBox.critical(self, "Error", f"Failed to delete mood: {str(e)}")

    def filter_moods(self):
        """Filter moods based on search text"""
        filter_tree_widget(self.mood_tree, self.search_box.text())

    def update_statistics(self):
        """Update the statistics display with track count data"""
        total_moods = len(self.moods_data)

        # Calculate total unique tracks with mood associations
        try:
            # Get all mood track associations
            all_associations = self.controller.get.get_all_entities(
                "MoodTrackAssociation"
            )

            # Count unique tracks
            unique_track_ids = set()
            for association in all_associations:
                if hasattr(association, "track_id"):
                    unique_track_ids.add(association.track_id)
                elif hasattr(association, "track") and association.track:
                    unique_track_ids.add(association.track.track_id)

            total_unique_tracks = len(unique_track_ids)

            self.stats_label.setText(f"Total moods: {total_moods}")
            self.tracks_count_label.setText(
                f"Tracks with mood associations: {total_unique_tracks}"
            )

        except SQLAlchemyError as e:
            logger.error(f"Error calculating statistics: {e}")
            self.stats_label.setText(f"Total moods: {total_moods}")
            self.tracks_count_label.setText("Tracks with moods: Unknown")

    def refresh_data(self):
        """Refresh all data from database"""
        self.load_moods()

    def get_tracks_for_mood(self, mood_id, include_children=None):
        """Get tracks associated with a mood, with optional recursive mode"""
        # If include_children is not specified, use the current recursive mode
        if include_children is None:
            include_children = (
                self.btn_recursive_mode.isChecked()
                if hasattr(self, "btn_recursive_mode")
                else False
            )

        try:
            if include_children:
                # Get all child mood IDs
                all_mood_ids = [mood_id] + self.get_child_mood_ids(mood_id)

                # Get associations for all these moods
                all_tracks = []
                for mid in all_mood_ids:
                    associations = self.controller.get.get_all_entities(
                        "MoodTrackAssociation", mood_id=mid
                    )
                    for association in associations:
                        if hasattr(association, "track"):
                            all_tracks.append(association.track)
                        else:
                            # Fallback: get track by ID
                            track = self.controller.get.get_entity_by_id(
                                "Track", association.track_id
                            )
                            if track and track not in all_tracks:
                                all_tracks.append(track)

                return all_tracks
            else:
                # Just get tracks for this specific mood
                associations = self.controller.get.get_all_entities(
                    "MoodTrackAssociation", mood_id=mood_id
                )
                tracks = []
                for association in associations:
                    if hasattr(association, "track"):
                        tracks.append(association.track)
                    else:
                        # Fallback: get track by ID
                        track = self.controller.get.get_entity_by_id(
                            "Track", association.track_id
                        )
                        if track:
                            tracks.append(track)
                return tracks

        except SQLAlchemyError as e:
            logger.error(f"Error getting tracks for mood: {e}")
            return []
