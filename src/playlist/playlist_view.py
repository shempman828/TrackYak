"""
playlist_view.py

"""

import datetime
from collections import defaultdict
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from src.common.hierarchy_tree_style import (
    configure_hierarchy_tree,
    icon_for_depth,
)
from src.core.logger_config import logger
from src.playlist.playlist_edit import EditPlaylist
from src.playlist.playlist_export import PlaylistExporter
from src.playlist.playlist_new import PlaylistCreateDialog
from src.playlist.playlist_smart_builder import SmartPlaylistBuilder
from src.playlist.playlist_smart_edit import SmartPlaylistEditDialog
from src.playlist.playlist_smart_new import SmartPlaylistCreateDialog
from src.playlist.playlist_tracks_window import PlaylistTracksWindow


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
        configure_hierarchy_tree(self.tree)

        # Second column keeps track counts out of the name text so the tree
        # reads as "name ... count" instead of a single run-on string.
        self.tree.setColumnCount(2)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)

        # Override dropEvent with our custom handler for persistence
        self.tree.dropEvent = self.handle_drop

        # Context menu for additional actions
        self.tree.customContextMenuRequested.connect(self.show_context_menu)

        main_layout.addWidget(self.tree)
        self.setLayout(main_layout)

    def _get_expanded_ids(self) -> set:
        """Walk the current tree and return the playlist IDs of all expanded items.

        This is called just before clearing the tree so we can restore the same
        expanded state after rebuilding it.
        """
        expanded = set()
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.isExpanded():
                item_data = item.data(0, Qt.UserRole)
                if item_data and len(item_data) == 2:
                    expanded.add(item_data[1])
            iterator += 1
        return expanded

    def _restore_expanded_ids(self, expanded_ids: set) -> None:
        """Walk the newly built tree and re-expand any item whose ID was expanded before."""
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            item_data = item.data(0, Qt.UserRole)
            if item_data and len(item_data) == 2 and item_data[1] in expanded_ids:
                item.setExpanded(True)
            iterator += 1

    def load_playlists(self) -> None:
        """Load hierarchical playlists from the database."""
        try:
            # Save which playlists were expanded before clearing the tree
            expanded_ids = self._get_expanded_ids()

            self.tree.clear()

            # Fetch all playlists with their relationships
            playlists = self.controller.get.get_all_entities("Playlist") or []

            # Build hierarchy
            children_map = defaultdict(list)

            for playlist in playlists:
                parent_id = getattr(playlist, "parent_id", None)
                children_map[parent_id].append(playlist)

            # Build tree recursively from root (None parent)
            self._build_tree(None, children_map, 0)

            # Restore the expanded state from before the rebuild
            self._restore_expanded_ids(expanded_ids)

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
        # Save the reference so we can reuse it if the user opens this playlist again
        self.open_playlist_windows[playlist_id] = window
        # Remove the reference when the window is closed so it can be garbage collected
        window.destroyed.connect(
            lambda: self.open_playlist_windows.pop(playlist_id, None)
        )
        window.show()

    def show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2:
            return

        # A right-click on an item that's part of a multi-selection keeps the
        # whole selection (standard Qt behavior); otherwise it's just this item.
        selected_items = self.tree.selectedItems()
        if item not in selected_items:
            selected_items = [item]

        if len(selected_items) > 1:
            self._show_multi_select_context_menu(selected_items, pos)
            return

        item_type, item_id = item_data

        # Check whether this is a smart playlist
        is_smart_playlist = False
        smart_flag = item.data(0, Qt.UserRole + 1)
        if smart_flag is not None:
            try:
                is_smart_playlist = bool(smart_flag)
            except Exception:
                is_smart_playlist = False

        menu = QMenu()

        if item_type == "playlist":
            if is_smart_playlist:
                # Smart playlist options
                menu.addAction(
                    "✏️ Edit Smart Playlist",
                    lambda: self.edit_smart_playlist(item_id),
                )
                menu.addAction(
                    "🔄 Refresh Playlist",
                    lambda: self._refresh_smart_playlist(item_id),
                )
                menu.addAction(
                    "👁 View Tracks",
                    lambda: self.open_playlist_editor(item_id),
                )
            else:
                # Normal playlist options
                menu.addAction("Edit Playlist Metadata", self.edit_playlist)
                menu.addAction(
                    "Open Track Editor",
                    lambda: self.open_playlist_editor(item_id),
                )

            menu.addSeparator()

        menu.addAction("Delete", self.delete_selected)
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _show_multi_select_context_menu(self, selected_items, pos) -> None:
        """Context menu shown when more than one tree item is selected."""
        playlist_items = [
            it
            for it in selected_items
            if (data := it.data(0, Qt.UserRole)) and len(data) == 2 and data[0] == "playlist"
        ]
        # Only playlists that have a parent can have their tracks folded upward.
        playlists_with_parent = [it for it in playlist_items if it.parent() is not None]

        menu = QMenu()

        if playlists_with_parent:
            menu.addAction(
                "Add All Tracks to Parent Playlist",
                lambda: self._add_tracks_to_parent_playlists(playlists_with_parent),
            )
            menu.addSeparator()

        menu.addAction("Delete", self.delete_selected)
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _add_tracks_to_parent_playlists(self, items: list) -> None:
        """Add every track in each selected playlist to that playlist's parent.

        Selected playlists are grouped by parent so e.g. selecting "sleepy jazz"
        and "sleepy indie" (both children of "sleepy") copies both playlists'
        tracks into "sleepy" in one go.
        """
        groups = defaultdict(list)
        for item in items:
            parent_item = item.parent()
            if parent_item is not None:
                groups[parent_item].append(item)

        if not groups:
            return

        child_names = ", ".join(
            item.text(0) for children in groups.values() for item in children
        )
        parent_names = ", ".join(parent_item.text(0) for parent_item in groups)
        confirm = QMessageBox.question(
            self,
            "Add Tracks to Parent Playlist",
            f"Add all tracks from {child_names} to {parent_names}?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        total_added = 0
        try:
            for parent_item, children in groups.items():
                parent_id = parent_item.data(0, Qt.UserRole)[1]

                existing_tracks = self.controller.get.get_entity_links(
                    "PlaylistTracks", playlist_id=parent_id
                )
                existing_track_ids = {t.track_id for t in existing_tracks}
                next_position = (
                    max((t.position for t in existing_tracks), default=0) + 1
                )

                for child_item in children:
                    child_id = child_item.data(0, Qt.UserRole)[1]
                    child_tracks = self.controller.get.get_entity_links(
                        "PlaylistTracks", playlist_id=child_id
                    )
                    for pt in child_tracks:
                        if pt.track_id in existing_track_ids:
                            continue
                        if self.controller.add.add_entity_link(
                            "PlaylistTracks",
                            playlist_id=parent_id,
                            track_id=pt.track_id,
                            position=next_position,
                        ):
                            existing_track_ids.add(pt.track_id)
                            next_position += 1
                            total_added += 1

            self.load_playlists()
            self.playlist_updated.emit()
            logger.info(
                f"Added {total_added} track(s) from {child_names} to {parent_names}"
            )
            QMessageBox.information(
                self,
                "Tracks Added",
                f"Added {total_added} track(s) to the parent playlist(s).",
            )

        except Exception as e:
            logger.error(f"Failed to add tracks to parent playlist: {str(e)}")
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to add tracks to parent playlist:\n{str(e)}",
            )

    @staticmethod
    def _format_playlist_name(playlist_obj) -> str:
        """Build the raw (depth-prefix-free) display name for a playlist,
        including its smart-playlist symbol. Track counts are rendered
        separately — see `_format_track_count` — so the name column stays
        readable instead of a run-on string of words and numbers."""
        display_name = playlist_obj.playlist_name
        if getattr(playlist_obj, "is_smart", False):
            display_name = f"🔍 {display_name}"
        return display_name

    @staticmethod
    def _format_track_count(playlist_obj) -> str:
        """Build the compact track-count text for the tree's count column.

        Use the playlist's own properties — no recalculation needed here.
        """
        own_count = getattr(playlist_obj, "track_count", 0) or 0
        recursive_total = (
            getattr(playlist_obj, "recursive_track_count", own_count) or own_count
        )
        if recursive_total != own_count:
            # Has sub-playlists contributing additional tracks, e.g. "3 · 10"
            return f"{own_count} · {recursive_total}"
        # Counts match — just the one number, e.g. "5"
        return str(own_count)

    @staticmethod
    def _style_count_cell(item: QTreeWidgetItem) -> None:
        """Right-align and de-emphasize the count column so it reads as a
        secondary detail rather than competing with the playlist name."""
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        font = item.font(1)
        font.setItalic(True)
        item.setFont(1, font)
        if "·" in item.text(1):
            item.setToolTip(1, "Own tracks · total including sub-playlists")

    def _build_tree(self, parent_item, children_map, depth):
        """Recursively build playlist tree with smart playlist symbols."""
        parent_id = parent_item.data(0, Qt.UserRole)[1] if parent_item else None

        children = sorted(
            children_map.get(parent_id, []),
            key=lambda x: getattr(x, "playlist_name", "").lower(),
        )

        for child in children:
            display_name = self._format_playlist_name(child)
            count_text = self._format_track_count(child)

            item = QTreeWidgetItem([display_name, count_text])
            item.setData(0, Qt.UserRole, ("playlist", child.playlist_id))
            item.setFlags(item.flags() | Qt.ItemIsEditable)

            # Store whether this is a smart playlist for context menu checks
            item.setData(0, Qt.UserRole + 1, getattr(child, "is_smart", False))

            item.setIcon(0, icon_for_depth(depth))
            self._style_count_cell(item)

            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

            # Recursively add children
            if depth < self.MAX_HIERARCHY_DEPTH:
                self._build_tree(item, children_map, depth + 1)

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
        """Open dialog for creating a smart playlist."""
        dialog = SmartPlaylistCreateDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            # NOTE: get_data() now returns 4 values — logic is new
            name, description, logic, criteria = dialog.get_data()

            if not name:
                QMessageBox.warning(
                    self, "Input Error", "Playlist name cannot be empty."
                )
                return

            try:
                # Create the Playlist record
                playlist = self.controller.add.add_entity(
                    "Playlist",
                    playlist_name=name,
                    playlist_description=description,
                    is_smart=1,
                )

                # Create the SmartPlaylist record (stores logic = AND/OR)
                smart_playlist = self.controller.add.add_entity(
                    "SmartPlaylist",
                    playlist_id=playlist.playlist_id,
                    logic=logic,
                    last_refreshed=datetime.datetime.now(),
                )

                # Add each criterion as a separate SmartPlaylistCriteria row
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

                # Immediately populate the playlist with matching tracks
                self.builder.refresh_playlist(playlist.playlist_id)

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

        try:
            playlist_obj = self.controller.get.get_entity_object(
                "Playlist", playlist_id=item_id
            )
            name = playlist_obj.playlist_name if playlist_obj else item.text(0)
        except Exception:
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

    @staticmethod
    def _depth_of(item: QTreeWidgetItem) -> int:
        """Count how many ancestors `item` has in the tree."""
        depth = 0
        parent = item.parent()
        while parent is not None:
            depth += 1
            parent = parent.parent()
        return depth

    @staticmethod
    def _is_descendant(ancestor: QTreeWidgetItem, item: Optional[QTreeWidgetItem]) -> bool:
        """Return True if `item` is nested somewhere below `ancestor`."""
        while item is not None:
            item = item.parent()
            if item is ancestor:
                return True
        return False

    def _update_subtree_depth(self, item: QTreeWidgetItem, depth: int) -> None:
        """Recompute the icon for `item` and everything nested below it
        after its depth in the tree has changed."""
        item.setIcon(0, icon_for_depth(depth))
        for i in range(item.childCount()):
            self._update_subtree_depth(item.child(i), depth + 1)

    def _refresh_item_display(self, item: QTreeWidgetItem) -> None:
        """Re-fetch a single playlist's counts from the DB and update its
        label in place, without touching the rest of the tree."""
        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2 or item_data[0] != "playlist":
            return
        try:
            playlist_obj = self.controller.get.get_entity_object(
                "Playlist", playlist_id=item_data[1]
            )
        except Exception as e:
            logger.error(f"Failed to refresh playlist item display: {str(e)}")
            return
        if not playlist_obj:
            return

        raw_name = self._format_playlist_name(playlist_obj)
        item.setText(0, raw_name)
        item.setText(1, self._format_track_count(playlist_obj))
        self._style_count_cell(item)

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

            # Dropping a playlist onto itself or one of its own descendants
            # would create a cycle in the tree - refuse it.
            if target_item is dragged_item or self._is_descendant(
                dragged_item, target_item
            ):
                event.ignore()
                return

            old_parent_item = dragged_item.parent()

            # Update the playlist's parent_id in database
            self.controller.update.update_entity(
                "Playlist", dragged_id, parent_id=new_parent_id
            )

            # Move the item within the tree in place instead of reloading the
            # whole module, so selection/scroll position/expanded state don't
            # get disturbed by an unrelated drag-and-drop.
            if old_parent_item is not None:
                old_parent_item.removeChild(dragged_item)
            else:
                self.tree.takeTopLevelItem(
                    self.tree.indexOfTopLevelItem(dragged_item)
                )

            if target_item is not None:
                target_item.addChild(dragged_item)
            else:
                self.tree.addTopLevelItem(dragged_item)

            # The moved item (and everything nested under it) sits at a new
            # depth now - refresh its indent prefix/icon to match.
            self._update_subtree_depth(dragged_item, self._depth_of(dragged_item))

            # The recursive track counts shown on both the old and new
            # ancestor chains changed - refresh just those labels.
            for ancestor in (old_parent_item, target_item):
                while ancestor is not None:
                    self._refresh_item_display(ancestor)
                    ancestor = ancestor.parent()

            dragged_item.setExpanded(True)
            self.tree.setCurrentItem(dragged_item)

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

    def edit_smart_playlist(self, playlist_id: int):
        """Open the edit dialog for a smart playlist, then refresh it."""
        dialog = SmartPlaylistEditDialog(self.controller, playlist_id, self)
        if dialog.exec_() == QDialog.Accepted:
            # Dialog saved changes — now re-evaluate which tracks match
            success = self.builder.refresh_playlist(playlist_id)
            if success:
                self.load_playlists()
                self.playlist_updated.emit()
            else:
                QMessageBox.warning(
                    self,
                    "Refresh Failed",
                    "Criteria were saved, but the track list could not be updated. "
                    "Try right-clicking the playlist and choosing Refresh.",
                )

    def _refresh_smart_playlist(self, playlist_id: int):
        """Refresh a smart playlist and show the user a result message."""
        success = self.builder.refresh_playlist(playlist_id)
        if success:
            self.load_playlists()
            self.playlist_updated.emit()
            # Read the count directly from the playlist object — no extra DB call needed
            try:
                playlist_obj = self.controller.get.get_entity_object(
                    "Playlist", playlist_id=playlist_id
                )
                count = getattr(playlist_obj, "track_count", None)
                msg = (
                    f"Done! The playlist now contains {count} matching track(s)."
                    if count is not None
                    else "Playlist updated successfully."
                )
            except Exception:
                msg = "Playlist updated successfully."
            QMessageBox.information(self, "Playlist Refreshed", msg)
        else:
            QMessageBox.warning(
                self,
                "Refresh Failed",
                "Could not refresh the playlist. Check the log for details.",
            )
