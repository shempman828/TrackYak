from collections import defaultdict
from typing import Optional

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QIcon, QPainter, QPixmap
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

from src.base_split_dialog import SplitDBDialog
from src.genre_edit import GenreEditDialog
from src.genre_merge import GenreMergeDialog
from src.genre_tracks import GenreTracksWindow
from src.logger_config import logger


class GenreView(QWidget):
    """Widget displaying genre hierarchy with CRUD operations and parent-child relationships."""

    genre_updated = Signal()

    def __init__(self, controller):
        super().__init__()
        self.current_genre_id: Optional[int] = None
        self.controller = controller
        self.show_recursive_tracks = False
        self.init_UI()
        self.load_genres()

    def init_UI(self):
        """Initialize UI components with modern styling and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

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

        # Add horizontal layout to the main vertical layout
        layout.addLayout(top_row)

        # Tree widget configuration
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(
            QTreeWidget.MultiSelection
        )  # Changed from SingleSelection
        self.tree.setAnimated(True)
        self.tree.itemChanged.connect(self.on_item_edited)

        # Drag and drop configuration
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.dropEvent = self.on_drop_event

        # Context menu signals
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        # Install event filter for keyboard shortcuts
        self.tree.installEventFilter(self)

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
                QMessageBox.warning(
                    self, "No Genre Selected", "Please select a genre to split."
                )
                return

            current_genre_id = current_item.data(0, Qt.UserRole)

            # Fetch the actual Genre ORM object from the database
            genre_obj = self.controller.get.get_entity_object(
                "Genre", genre_id=current_genre_id
            )
            if not genre_obj:
                QMessageBox.warning(
                    self, "Not Found", "The selected genre no longer exists."
                )
                return

            # Create the split dialog with proper parameters
            split_dialog = SplitDBDialog(
                self.controller.split,  # split helper with session
                "Genre",  # entity_type
                genre_obj,  # entity object
                self,  # parent
            )

            # Run dialog and refresh if accepted
            if split_dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText("Genre split completed successfully")

        except Exception as e:
            logger.error(f"Error in _split_genre(): {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"An unexpected error occurred:\n{e}")

    def load_genres(self):
        """Load genres from the database using the controller."""
        try:
            # Save which genre IDs are currently expanded
            expanded_ids = set()

            def collect_expanded(item):
                if item.isExpanded():
                    genre_id = item.data(0, Qt.UserRole)
                    if genre_id is not None:
                        expanded_ids.add(genre_id)
                for i in range(item.childCount()):
                    collect_expanded(item.child(i))

            root = self.tree.invisibleRootItem()
            for i in range(root.childCount()):
                collect_expanded(root.child(i))

            self.tree.clear()
            genres = self.controller.get.get_all_entities("Genre")

            # Get track counts for each genre from TrackGenre table
            track_counts = {}
            track_genres = self.controller.get.get_all_entities("TrackGenre")

            # Count tracks per genre using TrackGenre table (much faster)
            for track_genre in track_genres:
                genre_id = track_genre.genre_id
                track_counts[genre_id] = track_counts.get(genre_id, 0) + 1

            # Build a mapping of genre_id to genre for quick lookup
            genre_map = {genre.genre_id: genre for genre in genres}

            # Build a parent-child mapping
            children_map = defaultdict(list)
            for genre in genres:
                logger.debug(
                    f"Genre: {genre.genre_name} (ID: {genre.genre_id}), Parent: "
                    f"{genre.parent.genre_name if genre.parent else 'None'}"
                )

                children_map[genre.parent_id].append(genre)

            # Build the tree recursively starting from root nodes (parent_id=None)
            self._build_genre_tree(None, children_map, genre_map, track_counts, 0)

            def restore_expanded(item):
                genre_id = item.data(0, Qt.UserRole)
                if genre_id in expanded_ids:
                    item.setExpanded(True)
                for i in range(item.childCount()):
                    restore_expanded(item.child(i))

            if expanded_ids:
                root = self.tree.invisibleRootItem()
                for i in range(root.childCount()):
                    restore_expanded(root.child(i))
            else:
                self.tree.expandAll()
            logger.info(f"Loaded {len(genres)} genres with track counts")

        except Exception as e:
            logger.error(f"Error loading genres: {str(e)}")

    def _build_genre_tree(
        self, parent_item, children_map, genre_map, track_counts, depth
    ):
        """Recursively build the tree structure with visual hierarchy indicators."""
        parent_id = parent_item.data(0, Qt.UserRole) if parent_item else None
        for genre in sorted(
            children_map.get(parent_id, []), key=lambda g: g.genre_name.lower()
        ):
            # Get track count for this genre
            count = track_counts.get(genre.genre_id, 0)

            # Create display text with track count
            display_text = f"{genre.genre_name} ({count})"

            item = QTreeWidgetItem([display_text])
            item.setData(0, Qt.UserRole, genre.genre_id)
            item.setFlags(item.flags() | Qt.ItemIsEditable)

            # Store original genre name as tooltip data for editing
            item.setData(1, Qt.UserRole, genre.genre_name)

            # Use indentation and icons instead of colors
            if depth > 0:
                # Add prefix with hierarchy indicators
                indent_prefix = "  " * depth + "↳ "
                item.setText(0, indent_prefix + display_text)

            # Set different icons for different levels
            if depth == 0:
                item.setIcon(
                    0, self.create_colored_icon(QColor(70, 130, 180))
                )  # Steel Blue
            elif depth == 1:
                item.setIcon(
                    0, self.create_colored_icon(QColor(46, 139, 87))
                )  # Sea Green
            elif depth == 2:
                item.setIcon(
                    0, self.create_colored_icon(QColor(218, 165, 32))
                )  # Goldenrod
            elif depth == 3:
                item.setIcon(
                    0, self.create_colored_icon(QColor(178, 34, 34))
                )  # Firebrick
            elif depth == 4:
                item.setIcon(
                    0, self.create_colored_icon(QColor(138, 43, 226))
                )  # Blue Violet
            elif depth == 5:
                item.setIcon(
                    0, self.create_colored_icon(QColor(255, 140, 0))
                )  # Dark Orange
            elif depth == 6:
                item.setIcon(
                    0, self.create_colored_icon(QColor(199, 21, 133))
                )  # Medium Violet Red
            elif depth == 7:
                item.setIcon(
                    0, self.create_colored_icon(QColor(0, 191, 255))
                )  # Deep Sky Blue
            else:
                item.setIcon(
                    0, self.create_colored_icon(QColor(128, 128, 128))
                )  # Gray (fallback)

            # Update tooltip to include track count
            tooltip = f"ID: {genre.genre_id}\nTracks: {count}"
            if genre.description:
                tooltip += f"\nDescription: {genre.description}"
            if genre.parent:
                tooltip += f"\nParent: {genre.parent.genre_name}"
            item.setToolTip(0, tooltip)

            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

            self._build_genre_tree(
                item, children_map, genre_map, track_counts, depth + 1
            )

    def create_colored_icon(self, color, size=16):
        """Create a colored circle icon for hierarchy levels."""
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, size - 4, size - 4)
        painter.end()
        return QIcon(pixmap)

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
            QMessageBox.warning(self, "Rename Error", str(e))
            item.setText(0, old_display_text)  # Revert to old display text
        except Exception as e:
            logger.error(f"Error renaming genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to rename genre")
            item.setText(0, old_display_text)  # Revert to old display text

    def filter_genres(self, text):
        """Simple text-based filtering."""
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), text)

    def _filter_item(self, item, text):
        """Recursive filtering helper."""
        visible = text in item.text(0).lower()
        child_visible = False
        for i in range(item.childCount()):
            child_visible |= self._filter_item(item.child(i), text)
        item.setHidden(not (visible or child_visible))
        return visible or child_visible

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

        except Exception as e:
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
            QMessageBox.warning(self, "No Selection", "Please select a genre first.")
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
                QMessageBox.warning(
                    self, "Not Found", "The selected genre no longer exists."
                )
                return

            merge_dialog = GenreMergeDialog(self.controller, self, genre_obj=genre_obj)

            if merge_dialog.exec_() == QDialog.Accepted:
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText("Genre merge completed successfully")

        except Exception as e:
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
        except Exception as e:
            logger.error(f"Error editing genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to edit genre")

    def delete_selected_genres(self):
        """Delete all selected genres after confirmation."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            QMessageBox.information(
                self, "No Selection", "Please select genres to delete."
            )
            return

        # Get genre names for confirmation message
        genre_names = []
        genre_ids = []

        for item in selected_items:
            genre_id = item.data(0, Qt.UserRole)
            genre = self.controller.get.get_entity_object("Genre", genre_id=genre_id)
            if genre:
                genre_names.append(genre.genre_name)
                genre_ids.append(genre_id)

        if not genre_ids:
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
                # Delete each genre
                success_count = 0
                for genre_id in genre_ids:
                    try:
                        self.controller.delete.delete_entity("Genre", genre_id)
                        success_count += 1
                    except Exception as e:
                        logger.error(f"Error deleting genre {genre_id}: {str(e)}")

                # Refresh the tree
                self.load_genres()
                self.genre_updated.emit()

                if success_count == len(genre_ids):
                    self.status_bar.setText(f"Deleted {success_count} genre(s)")
                else:
                    self.status_bar.setText(
                        f"Deleted {success_count} of {len(genre_ids)} genre(s)"
                    )

            except Exception as e:
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
                self.load_genres()
                self.genre_updated.emit()
                self.status_bar.setText(f"Deleted {genre.genre_name}")

        except Exception as e:
            logger.error(f"Error deleting genre: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to delete genre")

    def _refresh_genre_display_text(self, item, genre_id, genre_name):
        """Refresh the display text with updated track count."""
        try:
            # Get current track count for this genre
            track_genres = self.controller.get.get_all_entities("TrackGenre")
            count = sum(1 for tg in track_genres if tg.genre_id == genre_id)

            # Build display text with count
            display_text = f"{genre_name} ({count})"

            # Add indentation if needed
            indent_prefix = ""
            text = item.text(0)
            if text.startswith("  "):
                # Extract indentation from current text
                for char in text:
                    if char == " ":
                        indent_prefix += " "
                    elif char == "↳":
                        indent_prefix += "↳"
                        break
                    else:
                        break
                display_text = indent_prefix + display_text

            item.setText(0, display_text)

        except Exception as e:
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
