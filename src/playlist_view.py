"""
playlist_view.py

"""

import datetime
from collections import defaultdict
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from logger_config import logger
from playlist_edit import EditPlaylist
from playlist_export import PlaylistExporter
from playlist_new import PlaylistCreateDialog
from playlist_smart_builder import SmartPlaylistBuilder
from playlist_smart_new import SmartPlaylistCreateDialog
from playlist_tracks_window import PlaylistTracksWindow


class PlaylistView(QWidget):
    """Main view for managing playlists."""

    playlist_updated = Signal()
    MAX_HIERARCHY_DEPTH = 8

    def __init__(self, controller: Any) -> None:
        """
        Initialize the playlist view.

        :param controller: The controller providing database and update functionalities.
        """
        super().__init__()
        self.controller = controller
        self.open_playlist_windows = {}
        self.selected_item: Optional[QTreeWidgetItem] = None
        self.exporter = PlaylistExporter(self.controller)
        self.init_ui()
        self.load_playlists()
        self.builder = SmartPlaylistBuilder(self.controller)

    def init_ui(self) -> None:
        """Initialize UI components with a modern layout and styling."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Control Buttons layout
        button_layout = QHBoxLayout()
        self.btn_new = QPushButton("New Playlist")
        self.btn_new.clicked.connect(self.create_normal_playlist)
        self.btn_smart = QPushButton("New Smart Playlist")
        self.btn_smart.clicked.connect(self.create_smart_playlist)

        self.btn_export = QPushButton("Export")
        self.btn_export.clicked.connect(self.export_selected_playlist)

        button_layout.addWidget(self.btn_new)
        button_layout.addWidget(self.btn_smart)
        button_layout.addStretch()
        button_layout.addWidget(self.btn_export)
        main_layout.addLayout(button_layout)

        # Tree widget for displaying playlist hierarchy
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.MultiSelection)

        # Enable drag and drop reorganization of items
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        # Override dropEvent with our custom handler for persistence
        self.tree.dropEvent = self.handle_drop

        # Context menu for additional actions
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        main_layout.addWidget(self.tree)
        self.setLayout(main_layout)

    def load_playlists(self) -> None:
        """Load hierarchical playlists from the database."""
        try:
            self.tree.clear()

            # Fetch all playlists with their relationships
            playlists = self.controller.get.get_all_entities("Playlist") or []

            # Build hierarchy
            playlist_map = {p.playlist_id: p for p in playlists}
            children_map = defaultdict(list)

            for playlist in playlists:
                parent_id = getattr(playlist, "parent_id", None)
                children_map[parent_id].append(playlist)

            # Build tree recursively from root (None parent)
            self._build_tree(None, children_map, playlist_map, 0)

            logger.info("Playlist hierarchy loaded successfully")

        except Exception as e:
            logger.error(f"Error loading playlists: {str(e)}")
            QMessageBox.critical(
                self, "Loading Error", "Failed to load playlist hierarchy"
            )

    def export_selected_playlist(self) -> None:
        """Export the currently selected playlist."""
        item = self.tree.currentItem()
        if not item:
            QMessageBox.warning(
                self, "Export Error", "Please select a playlist to export."
            )
            return

        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2 or item_data[0] != "playlist":
            QMessageBox.warning(
                self, "Export Error", "Please select a valid playlist to export."
            )
            return

        playlist_id = item_data[1]
        self.exporter.export_playlist(playlist_id)

    def open_playlist_editor(self, playlist_id: int):
        """Open or focus an independent playlist editor window."""
        # Check if window already exists
        if playlist_id in self.open_playlist_windows:
            window = self.open_playlist_windows[playlist_id]
            window.show()
            window.raise_()
            window.activateWindow()
            # Force refresh when reopening an existing window
            window.load_playlist_tracks()
            return

        # Create new window - it will load fresh data in its constructor
        window = PlaylistTracksWindow(playlist_id, self.controller, self)
        window.show()

    def show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2:
            return

        item_type, item_id = item_data

        # Properly check for smart playlist flag
        is_smart_playlist = False
        smart_flag = item.data(0, Qt.UserRole + 1)
        if smart_flag is not None:
            try:
                is_smart_playlist = bool(smart_flag)
            except:  # noqa: E722
                is_smart_playlist = False

        menu = QMenu()

        if item_type == "playlist":
            menu.addAction("Edit Playlist Metadata", self.edit_playlist)

            # Only allow track editing for non-smart playlists
            if not is_smart_playlist:
                menu.addAction(
                    "Open Track Editor", lambda: self.open_playlist_editor(item_id)
                )
            else:
                # For smart playlists, show view-only option
                menu.addAction(
                    "View Tracks", lambda: self.open_playlist_editor(item_id)
                )
                menu.addAction(
                    "Refresh Playlist", lambda: self.builder.refresh_playlist(item_id)
                )

            menu.addSeparator()

        menu.addAction("Delete", self.delete_selected)
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _build_tree(self, parent_item, children_map, playlist_map, depth):
        """Recursively build playlist tree with smart playlist symbols."""
        parent_id = parent_item.data(0, Qt.UserRole)[1] if parent_item else None

        children = sorted(
            children_map.get(parent_id, []),
            key=lambda x: getattr(x, "created_date", getattr(x, "playlist_name", "")),
        )

        for child in children:
            # Add smart playlist symbol (🔍) for smart playlists
            display_name = child.playlist_name
            if getattr(child, "is_smart", False):
                display_name = f"🔍 {display_name}"

            item = QTreeWidgetItem([display_name])
            item.setData(0, Qt.UserRole, ("playlist", child.playlist_id))
            item.setFlags(item.flags() | Qt.ItemIsEditable)

            # Store whether this is a smart playlist for context menu checks
            item.setData(0, Qt.UserRole + 1, getattr(child, "is_smart", False))

            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

            # Recursively add children
            if depth < self.MAX_HIERARCHY_DEPTH:
                self._build_tree(item, children_map, playlist_map, depth + 1)

    def _cleanup_editor(self, editor) -> None:
        """Remove reference to closed editor."""
        if hasattr(self, "_playlist_editors"):
            try:
                self._playlist_editors.remove(editor)
            except ValueError:
                pass

    def create_normal_playlist(self) -> None:
        """Open dialog to create a new playlist with name and description."""
        dialog = PlaylistCreateDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            name, description = dialog.get_data()

            if not name:
                QMessageBox.warning(
                    self, "Input Error", "Playlist name cannot be empty."
                )
                return

            try:
                # Add to database using your controller's create method
                self.controller.add.add_entity(
                    "Playlist",
                    playlist_name=name,
                    playlist_description=description,
                )

                # Refresh the UI
                self.load_playlists()
                self.playlist_updated.emit()
                logger.info(f"Created new playlist: {name}")

            except Exception as e:
                logger.error(f"Failed to create playlist: {str(e)}")
                QMessageBox.critical(self, "Error", f"Could not create playlist: {e}")

    def create_smart_playlist(self):
        """Open dialog for creating smart playlist"""
        dialog = SmartPlaylistCreateDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            name, description, criteria = dialog.get_data()

            if not name:
                QMessageBox.warning(
                    self, "Input Error", "Playlist name cannot be empty."
                )
                return

            try:
                # Add to database using your controller's create method
                playlist = self.controller.add.add_entity(
                    "Playlist",
                    playlist_name=name,
                    playlist_description=description,
                    is_smart=1,
                )

                # Create smart playlist record first
                smart_playlist = self.controller.add.add_entity(
                    "SmartPlaylist",
                    playlist_id=playlist.playlist_id,
                    last_refreshed=datetime.datetime.now(),
                )

                # Add each criterion separately
                if criteria:
                    for criterion in criteria:
                        self.controller.add.add_entity(
                            "SmartPlaylistCriteria",
                            smart_playlist_id=smart_playlist.playlist_id,
                            field_name=criterion.get("field", ""),
                            comparison=criterion.get("comparison", ""),
                            value=criterion.get("value", ""),
                            type=criterion.get("type", "String"),
                        )

                # Refresh the UI
                self.load_playlists()
                self.playlist_updated.emit()
                logger.info(f"Created new smart playlist: {name}")

            except Exception as e:
                logger.error(f"Failed to create smart playlist: {str(e)}")
                QMessageBox.critical(
                    self, "Error", f"Could not create smart playlist: {e}"
                )

    def delete_selected(self) -> None:
        item = self.tree.currentItem()
        if not item:
            return
        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2:
            return
        item_type, item_id = item_data
        name = item.text(0)

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete this {item_type} '{name}' and all its contents?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm == QMessageBox.Yes:
            try:
                # Always delete from Playlist table
                self.controller.delete.delete_entity("Playlist", item_id)

                self.load_playlists()
                self.playlist_updated.emit()
                logger.info(f"Deleted {item_type}: {name}")

            except Exception as e:
                logger.error(f"Deletion failed: {str(e)}")
                QMessageBox.critical(
                    self, "Error", f"Failed to delete {item_type}:\n{str(e)}"
                )

    def handle_drop(self, event: Any) -> None:
        target_item = self.tree.itemAt(event.pos())
        dragged_item = self.tree.currentItem()

        if not dragged_item:
            event.ignore()
            return

        try:
            # FIX: Extract data correctly - it's a tuple (type, id)
            if target_item:
                target_data = target_item.data(0, Qt.UserRole)
                new_parent_id = target_data[1] if target_data else None  # FIXED
            else:
                new_parent_id = None

            dragged_data = dragged_item.data(0, Qt.UserRole)
            dragged_id = dragged_data[1] if dragged_data else None  # FIXED

            if dragged_id is None:
                event.ignore()
                return

            # Update the playlist's parent_id in database
            self.controller.update.update_entity(
                "Playlist", dragged_id, parent_id=new_parent_id
            )

            self.playlist_updated.emit()
            logger.debug(f"Moved playlist {dragged_id} to parent {new_parent_id}")
            event.accept()

        except Exception as e:
            logger.error(f"Drag-drop error: {str(e)}")
            event.ignore()

    def edit_playlist(self) -> None:
        """
        Open the selected playlist for editing.
        Only available when a playlist item is selected.
        """
        item = self.tree.currentItem()
        if not item or item.data(0, Qt.UserRole)[0] != "playlist":
            return

        playlist_id = item.data(0, Qt.UserRole)[1]
        try:
            playlist = self.controller.get.get_entity_object(
                "Playlist", playlist_id=playlist_id
            )
        except Exception as e:
            logger.error(f"Failed to fetch playlist object: {str(e)}")
            QMessageBox.critical(self, "Error", "Unable to load playlist details.")
            return

        dialog = EditPlaylist(self.controller, playlist)
        if dialog.exec_():
            self.load_playlists()
            self.playlist_updated.emit()
