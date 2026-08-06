from collections import defaultdict
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QDialog,
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

from src.common.base_split_dialog import SplitDBDialog
from src.common.hierarchy_tree_style import (
    collect_expanded_ids,
    configure_hierarchy_tree,
    filter_tree_widget,
    icon_for_depth,
    insert_as_new_child,
    insert_as_new_parent,
    is_hierarchy_descendant,
    restore_expanded_ids_or_expand_all,
)
from src.core.status_utility import show_status_message
from src.db.db_tables import TrackGenre
from src.genre.genre_edit import GenreEditDialog
from src.genre.genre_merge import GenreMergeDialog
from src.genre.genre_tracks import GenreTracksWindow
from src.core.logger_config import logger


class GenreView(QWidget):
    """Widget displaying genre hierarchy with CRUD operations and parent-child relationships."""

    genre_updated = Signal()

    def __init__(self, controller):
        super().__init__()
        self.current_genre_id: Optional[int] = None
        self.controller = controller
        self.show_recursive_tracks = False
        self.flat_view = False
        self.init_UI()
        self.load_genres()

    def init_UI(self):
        """Initialize UI components with modern styling and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Tree widget configuration (created early so top-row buttons can reference it)
        self.tree = QTreeWidget()
        configure_hierarchy_tree(self.tree)
        self.tree.itemChanged.connect(self.on_item_edited)
        self.tree.dropEvent = self.on_drop_event

        # Context menu signals
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        # Install event filter for keyboard shortcuts
        self.tree.installEventFilter(self)

        # Top row: Search bar + Refresh button
        top_row = QHBoxLayout()

        # Search bar with clear button
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search genres...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.filter_genres)
        top_row.addWidget(self.search_bar)

        # New Genre Button
        self.new_genre_button = QPushButton("New Genre")
        self.new_genre_button.clicked.connect(lambda: self.edit_genre(None))
        top_row.addWidget(self.new_genre_button)

        # Expand All / Collapse All buttons
        self.expand_all_button = QPushButton("Expand All")
        self.expand_all_button.clicked.connect(self.tree.expandAll)
        top_row.addWidget(self.expand_all_button)

        self.collapse_all_button = QPushButton("Collapse All")
        self.collapse_all_button.clicked.connect(self.tree.collapseAll)
        top_row.addWidget(self.collapse_all_button)

        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip(
            "Toggle between the hierarchical tree and a flat alphabetical list"
        )
        self.flat_view_button.clicked.connect(self.toggle_flat_view)
        top_row.addWidget(self.flat_view_button)

        # Add horizontal layout to the main vertical layout
        layout.addLayout(top_row)

        layout.addWidget(self.tree)

        # Status bar with temporary messages
        self.status_bar = QLabel()
        self.status_bar.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_bar)

    def eventFilter(self, obj, event):
        """Handle keyboard shortcuts."""
        if obj == self.tree and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Delete:
                self.delete_selected_genres()
                return True
        return super().eventFilter(obj, event)

    def _split_genre(self):
        """Open the split dialog for the selected genre."""
        try:
            # Get the currently selected item from the tree
            current_item = self.tree.currentItem()
            if not current_item:
                show_status_message(self, "Please select a genre to split.")
                return

            current_genre_id = current_item.data(0, Qt.UserRole)

            # Fetch the actual Genre ORM object from the database
            genre_obj = self.controller.get.get_entity_object(
                "Genre", genre_id=current_genre_id
            )
            if not genre_obj:
                show_status_message(self, "The selected genre no longer exists.")
                return

            # Create the split dialog with proper parameters
            split_dialog = SplitDBDialog(
                self.controller.split,  # split helper with session
                "Genre",  # entity_type
                genre_obj,  # entity object
                self,  # parent
                get_helper=self.controller.get,
            )

            # Run dialog and refresh if accepted
            if split_dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText("Genre split completed successfully")

        except SQLAlchemyError as e:
            logger.error(f"Error in _split_genre(): {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"An unexpected error occurred:\n{e}")

    def load_genres(self):
        """Load genres from the database using the controller."""
        try:
            # Save which genre IDs are currently expanded
            expanded_ids = collect_expanded_ids(self.tree)
            is_initial_load = self.tree.topLevelItemCount() == 0

            self.tree.clear()
            genres = self.controller.get.get_all_entities("Genre")

            # Get track counts for each genre with a single grouped COUNT
            # query instead of fetching every TrackGenre row as an ORM
            # object just to count them in Python (67k+ rows in a large
            # library — that instantiation cost alone was ~800ms).
            track_counts = dict(
                self.controller.get.session.execute(
                    select(TrackGenre.genre_id, func.count()).group_by(
                        TrackGenre.genre_id
                    )
                ).all()
            )

            # Build a mapping of genre_id to genre for quick lookup
            genre_map = {genre.genre_id: genre for genre in genres}

            # Build a parent-child mapping
            children_map = defaultdict(list)
            for genre in genres:
                parent_name = (
                    genre_map[genre.parent_id].genre_name
                    if genre.parent_id in genre_map
                    else "None"
                )
                logger.debug(
                    f"Genre: {genre.genre_name} (ID: {genre.genre_id}), Parent: "
                    f"{parent_name}"
                )

                children_map[genre.parent_id].append(genre)

            if self.flat_view:
                self._build_genre_flat(genres, track_counts)
            else:
                # Build the tree recursively starting from root nodes (parent_id=None)
                self._build_genre_tree(None, children_map, genre_map, track_counts, 0)

            restore_expanded_ids_or_expand_all(self.tree, expanded_ids, is_initial_load)
            logger.info(f"Loaded {len(genres)} genres with track counts")

            # Reapply any active search filter, since the tree was just
            # rebuilt from scratch.
            self.filter_genres(self.search_bar.text())

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading genres: {str(e)}")

    def toggle_flat_view(self):
        """Toggle between the nested hierarchy and a flat alphabetical list."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        self.expand_all_button.setEnabled(not self.flat_view)
        self.collapse_all_button.setEnabled(not self.flat_view)
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list.
        self.tree.setDragEnabled(not self.flat_view)
        self.load_genres()

    def _make_genre_item(self, genre, count, depth):
        """Build a single genre's tree item, shared by the tree and flat builders."""
        display_text = f"{genre.genre_name} ({count})"

        item = QTreeWidgetItem([display_text])
        item.setData(0, Qt.UserRole, genre.genre_id)
        item.setFlags(item.flags() | Qt.ItemIsEditable)

        # Store original genre name as tooltip data for editing
        item.setData(1, Qt.UserRole, genre.genre_name)

        item.setIcon(0, icon_for_depth(depth))

        # Update tooltip to include track count
        tooltip = f"ID: {genre.genre_id}\nTracks: {count}"
        if genre.description:
            tooltip += f"\nDescription: {genre.description}"
        if genre.parent:
            tooltip += f"\nParent: {genre.parent.genre_name}"
        item.setToolTip(0, tooltip)
        return item

    def _build_genre_tree(
        self, parent_item, children_map, genre_map, track_counts, depth
    ):
        """Recursively build the tree structure with visual hierarchy indicators."""
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None
        for genre in sorted(
            children_map.get(parent_id, []), key=lambda g: g.genre_name.lower()
        ):
            count = track_counts.get(genre.genre_id, 0)
            item = self._make_genre_item(genre, count, depth)

            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

            self._build_genre_tree(
                item, children_map, genre_map, track_counts, depth + 1
            )

    def _build_genre_flat(self, genres, track_counts):
        """Populate the tree as a single alphabetical list with no nesting."""
        for genre in sorted(genres, key=lambda g: g.genre_name.lower()):
            count = track_counts.get(genre.genre_id, 0)
            item = self._make_genre_item(genre, count, 0)
            self.tree.addTopLevelItem(item)

    def on_item_edited(self, item, column):
        """Handle genre name updates."""
        genre_id = item.data(0, Qt.UserRole)

        # Extract just the name part (remove track count)
        full_text = item.text(column).strip()
        # Remove the track count in parentheses at the end
        if " (" in full_text and full_text.endswith(")"):
            new_name = full_text.rsplit(" (", 1)[0].strip()
        else:
            new_name = full_text

        old_display_text = item.text(
            column
        )  # Store old display text in case we need to revert
        old_name = item.data(  # noqa: F841
            1, Qt.UserRole
        )  # Get original name from stored data  # noqa: F841

        try:
            if not new_name:
                raise ValueError("Genre name cannot be empty")

            # Check if name already exists (excluding current genre)
            existing = self.controller.get.get_entity_object(
                "Genre", genre_name=new_name
            )
            if existing and existing.genre_id != genre_id:
                raise ValueError("Genre name already exists")

            # Update the genre name
            self.controller.update.update_entity("Genre", genre_id, genre_name=new_name)

            # Update stored name
            item.setData(1, Qt.UserRole, new_name)

            # Refresh the display text with track count
            self._refresh_genre_display_text(item, genre_id, new_name)

            self.genre_updated.emit()
            self.status_bar.setText(f"Renamed to {new_name}")

        except ValueError as e:
            show_status_message(self, str(e))
            item.setText(0, old_display_text)  # Revert to old display text
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error renaming genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to rename genre")
            item.setText(0, old_display_text)  # Revert to old display text

    def filter_genres(self, text):
        """Simple text-based filtering."""
        filter_tree_widget(self.tree, text)

    def on_drop_event(self, event):
        """Handle parent changes through drag-and-drop."""
        # Get all selected items
        selected_items = self.tree.selectedItems()

        if not selected_items:
            event.ignore()
            return

        # Determine the drop target
        target_item = self.tree.itemAt(event.pos())
        target_id = target_item.data(0, Qt.UserRole) if target_item else None

        try:
            # Prevent circular reference: reject moving a genre onto itself
            # or onto one of its own descendants.
            if target_id is not None:
                all_genres = self.controller.get.get_all_entities("Genre")
                for item in selected_items:
                    child_id = item.data(0, Qt.UserRole)
                    if child_id == target_id or is_hierarchy_descendant(
                        child_id, target_id, all_genres, id_attr="genre_id"
                    ):
                        show_status_message(
                            self,
                            "Cannot make a genre a child of itself or its descendants.",
                        )
                        event.ignore()
                        return

            # Move all selected items to the new parent
            for item in selected_items:
                child_id = item.data(0, Qt.UserRole)
                logger.info(f"Moving {child_id} to {target_id}")
                self.controller.update.update_entity(
                    "Genre", child_id, parent_id=target_id
                )

            self.load_genres()  # Refresh tree
            self.genre_updated.emit()
            event.accept()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error moving genre: {str(e)}")
            event.ignore()

    def show_context_menu(self, pos):
        """Display context menu for genre operations."""
        item = self.tree.itemAt(pos)
        if not item:
            return

        menu = QMenu()
        selected_items = self.tree.selectedItems()

        if len(selected_items) == 1:
            # Single selection - store the current genre ID
            self.current_genre_id = item.data(0, Qt.UserRole)
            menu.addAction("View Tracks", lambda: self.view_tracks_for_selected_genre())
            menu.addAction("Edit", lambda: self.edit_genre(self.current_genre_id))
            menu.addAction("Merge", lambda: self.merge_genre(self.current_genre_id))
            menu.addAction("Split", lambda: self._split_genre())
            menu.addSeparator()
            menu.addAction(
                "New Parent Genre", lambda: self.create_new_parent(self.current_genre_id)
            )
            menu.addAction(
                "New Child Genre", lambda: self.create_new_child(self.current_genre_id)
            )
        else:
            # Multiple selection
            self.current_genre_id = None

        # Always show delete option (works for single or multiple)
        menu.addAction("Delete", lambda: self.delete_selected_genres())

        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def view_tracks_for_selected_genre(self):
        """Open tracks view window for selected genre."""
        current_item = self.tree.currentItem()
        if not current_item:
            show_status_message(self, "Please select a genre first.")
            return

        genre_id = current_item.data(0, Qt.UserRole)
        genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)

        if genre:
            tracks_window = GenreTracksWindow(self.controller, genre, self)
            tracks_window.show()

    def merge_genre(self, source_genre_id):
        """Open the merge dialog for the selected genre."""
        try:
            genre_obj = self.controller.get.get_entity_object(
                "Genre", genre_id=source_genre_id
            )
            if not genre_obj:
                show_status_message(self, "The selected genre no longer exists.")
                return

            merge_dialog = GenreMergeDialog(self.controller, self, genre_obj=genre_obj)

            if merge_dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText("Genre merge completed successfully")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error merging genre: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to merge genre: {str(e)}")

    def edit_genre(self, genre_id):
        """Open edit dialog for selected genre."""
        try:
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            dialog = GenreEditDialog(self.controller, genre)
            if dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error editing genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to edit genre")

    def create_new_parent(self, genre_id):
        """Create a new genre and insert it as the parent of the given genre.

        The new genre takes over the genre's old parent slot (preserving the
        grandparent chain), and the genre becomes a child of the new genre.
        """
        genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
        if not genre:
            show_status_message(self, "The selected genre no longer exists.")
            return

        dialog = GenreEditDialog(self.controller, None)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_genre:
            return

        new_genre = dialog.result_genre
        try:
            insert_as_new_parent(self.controller, "Genre", "genre_id", genre, new_genre)
            self.load_genres()
            self.genre_updated.emit()
            self.status_bar.setText(
                f"Created '{new_genre.genre_name}' as parent of '{genre.genre_name}'"
            )
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error creating new parent genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to create new parent genre")

    def create_new_child(self, genre_id):
        """Create a new genre and set it as a child of the given genre."""
        genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
        if not genre:
            show_status_message(self, "The selected genre no longer exists.")
            return

        dialog = GenreEditDialog(self.controller, None)
        if dialog.exec_() != QDialog.Accepted or not dialog.result_genre:
            return

        new_genre = dialog.result_genre
        try:
            insert_as_new_child(self.controller, "Genre", "genre_id", genre, new_genre)
            self.load_genres()
            self.genre_updated.emit()
            self.status_bar.setText(
                f"Created '{new_genre.genre_name}' as child of '{genre.genre_name}'"
            )
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error creating new child genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to create new child genre")

    def delete_selected_genres(self):
        """Delete all selected genres after confirmation."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            show_status_message(self, "Please select genres to delete.")
            return

        # Get genre names for confirmation message, keeping each tree item
        # paired with its genre_id so we can target the right item on delete
        genre_names = []
        to_delete = []  # list of (item, genre_id)

        for item in selected_items:
            genre_id = item.data(0, Qt.UserRole)
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            if genre:
                genre_names.append(genre.genre_name)
                to_delete.append((item, genre_id))

        if not to_delete:
            return

        # Confirm deletion
        if len(genre_names) == 1:
            message = f"Are you sure you want to delete '{genre_names[0]}'?"
        else:
            message = (
                f"Are you sure you want to delete {len(genre_names)} genres?\n\n"
                + "\n".join(f"• {name}" for name in genre_names)
            )

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            message,
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            try:
                # Delete each genre and remove just its tree item, rather
                # than reloading the whole tree (which would collapse/expand
                # items back to their saved state instead of leaving the
                # rest of the tree untouched).
                success_count = 0
                for item, genre_id in to_delete:
                    try:
                        self.controller.delete.delete_entity("Genre", genre_id)
                        self._remove_genre_tree_item(item)
                        success_count += 1
                    except SQLAlchemyError as e:
                        logger.error(f"Error deleting genre {genre_id}: {str(e)}")

                self.genre_updated.emit()

                if success_count == len(to_delete):
                    self.status_bar.setText(f"Deleted {success_count} genre(s)")
                else:
                    self.status_bar.setText(
                        f"Deleted {success_count} of {len(to_delete)} genre(s)"
                    )

            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"Error in bulk delete: {str(e)}")
                QMessageBox.critical(
                    self, "Error", "Failed to delete one or more genres"
                )

    def delete_genre(self, genre_id):
        """Delete single genre after confirmation (kept for backward compatibility)."""
        try:
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                f"Are you sure you want to delete '{genre.genre_name}'?",
                QMessageBox.Yes | QMessageBox.No,
            )

            if reply == QMessageBox.Yes:
                self.controller.delete.delete_entity("Genre", genre_id)
                item = self._find_genre_item(genre_id)
                if item:
                    self._remove_genre_tree_item(item)
                self.genre_updated.emit()
                self.status_bar.setText(f"Deleted {genre.genre_name}")

        except (AttributeError, SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error deleting genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to delete genre")

    def _find_genre_item(self, genre_id, container=None):
        """Recursively find the tree item for a genre_id."""
        container = container or self.tree.invisibleRootItem()
        for i in range(container.childCount()):
            child = container.child(i)
            if child.data(0, Qt.UserRole) == genre_id:
                return child
            found = self._find_genre_item(genre_id, child)
            if found:
                return found
        return None

    def _remove_genre_tree_item(self, item):
        """Remove a single genre's tree item without reloading the whole tree.

        Deleting a genre nullifies its children's parent_id in the DB
        (they become top-level genres), so their tree items are promoted
        to the top level rather than deleted along with their parent.
        The rest of the tree - including every other item's expanded or
        collapsed state - is left untouched.
        """
        children = item.takeChildren()
        container = item.parent() or self.tree.invisibleRootItem()
        container.removeChild(item)

        root = self.tree.invisibleRootItem()
        for child in children:
            index = self._sorted_insert_index(root, child.text(0))
            root.insertChild(index, child)
            self._reindent_subtree(child, 0)

    def _sorted_insert_index(self, container, text):
        """Find the alphabetical insertion index for text among container's children."""
        key = text.lower()
        for i in range(container.childCount()):
            if container.child(i).text(0).lower() > key:
                return i
        return container.childCount()

    def _reindent_subtree(self, item, depth):
        """Refresh depth-based icons after an item moves to a new tree level."""
        item.setIcon(0, icon_for_depth(depth))
        for i in range(item.childCount()):
            self._reindent_subtree(item.child(i), depth + 1)

    def _refresh_genre_display_text(self, item, genre_id, genre_name):
        """Refresh the display text with updated track count."""
        try:
            # Get current track count for this genre
            track_genres = self.controller.get.get_all_entities("TrackGenre")
            count = sum(1 for tg in track_genres if tg.genre_id == genre_id)

            # Build display text with count
            display_text = f"{genre_name} ({count})"

            item.setText(0, display_text)

        except SQLAlchemyError as e:
            logger.error(f"Error refreshing genre display text: {str(e)}")

    def startDrag(self, supportedActions):
        """Override to handle multi-selection drag better."""
        selected_items = self.tree.selectedItems()
        if len(selected_items) > 1:
            # Show a count of selected items during drag
            drag = QDrag(self.tree)
            mime_data = QMimeData()
            # You could customize the drag icon/text here
            drag.setMimeData(mime_data)
            drag.exec_(supportedActions)
        else:
            super().startDrag(supportedActions)
