"""playlist_view.py"""

from collections import defaultdict
import datetime
from typing import Any

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
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.hierarchy_tree_style import configure_hierarchy_tree, icon_for_depth
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.playlist.playlist_edit import EditPlaylist
from src.playlist.playlist_export import PlaylistExporter
from src.playlist.playlist_new import PlaylistCreateDialog
from src.playlist.playlist_refresh_controller import PlaylistRefreshController
from src.playlist.playlist_smart_edit import SmartPlaylistEditDialog
from src.playlist.playlist_smart_new import SmartPlaylistCreateDialog
from src.playlist.playlist_tracks_window import PlaylistTracksWindow
from src.playlist.playlist_tree_dnd import PlaylistTreeDnD
from src.track.track_shuffle import shuffle_and_play
from src.track.view.base_track_view import BaseTrackView


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
        self.selected_item: QTreeWidgetItem | None = None
        self.flat_view = False
        self.exporter = PlaylistExporter(self.controller, parent_widget=self)
        self.init_ui()
        self.load_playlists()
        self.refresh_controller = PlaylistRefreshController(self)
        self.tree_dnd = PlaylistTreeDnD(self)
        self._refresh_auto_refresh_playlists()

    def _refresh_auto_refresh_playlists(self) -> None:
        """Refresh every smart playlist flagged auto_refresh on startup."""
        try:
            smart_playlists = self.controller.get.get_all_entities("SmartPlaylist", auto_refresh=1)
        except SQLAlchemyError as e:
            logger.error(f"Failed to load auto-refresh smart playlists: {e}")
            return
        for smart_playlist in smart_playlists or []:
            self.refresh_controller.start_refresh(
                smart_playlist.playlist_id, self.refresh_controller.on_startup_playlist_refreshed
            )

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

        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip(
            "Toggle between the hierarchical tree and a flat alphabetical list"
        )
        self.flat_view_button.clicked.connect(self.toggle_flat_view)

        button_layout.addWidget(self.btn_new)
        button_layout.addWidget(self.btn_smart)
        button_layout.addStretch()
        button_layout.addWidget(self.flat_view_button)
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

        # Persist in-place renames (the tree items are editable via double-click/F2)
        self.tree.itemChanged.connect(self._on_item_renamed)

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
        # Block signals for the whole rebuild -- item.setText() below would
        # otherwise re-trigger _on_item_renamed as though the user had
        # edited each row by hand.
        self.tree.blockSignals(True)
        try:
            # Save which playlists were expanded before clearing the tree
            expanded_ids = self._get_expanded_ids()

            self.tree.clear()

            # Fetch all playlists with their relationships
            playlists = self.controller.get.get_all_entities("Playlist") or []

            if not playlists:
                self._add_empty_state_item()
                return

            # Build hierarchy
            children_map = defaultdict(list)

            for playlist in playlists:
                parent_id = getattr(playlist, "parent_id", None)
                children_map[parent_id].append(playlist)

            if self.flat_view:
                self._build_flat(playlists)
            else:
                # Build tree recursively from root (None parent)
                self._build_tree(None, children_map, 0)

            # Restore the expanded state from before the rebuild
            self._restore_expanded_ids(expanded_ids)

            logger.info("Playlist hierarchy loaded successfully")

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error loading playlists: {e!s}")
            QMessageBox.critical(self, "Loading Error", "Failed to load playlist hierarchy")
        finally:
            self.tree.blockSignals(False)

    def _add_empty_state_item(self) -> None:
        """Show placeholder text instead of leaving the tree looking broken/blank."""
        item = QTreeWidgetItem(['No playlists yet — click "New Playlist" to create one.', ""])
        item.setFlags(Qt.ItemIsEnabled)
        font = item.font(0)
        font.setItalic(True)
        item.setFont(0, font)
        self.tree.addTopLevelItem(item)

    def export_selected_playlist(self) -> None:
        """Export the currently selected playlist."""
        item = self.tree.currentItem()
        if not item:
            show_status_message(self, "Please select a playlist to export.")
            return

        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2 or item_data[0] != "playlist":
            show_status_message(self, "Please select a valid playlist to export.")
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
        window.destroyed.connect(lambda: self.open_playlist_windows.pop(playlist_id, None))
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
            except TypeError:
                is_smart_playlist = False

        menu = QMenu()

        if item_type == "playlist":
            if is_smart_playlist:
                # Smart playlist options
                menu.addAction("Edit Smart Playlist", lambda: self.edit_smart_playlist(item_id))
                menu.addAction(
                    "Refresh Playlist",
                    lambda: self.refresh_controller.refresh_smart_playlist(item_id),
                )
                menu.addAction("View Tracks", lambda: self.open_playlist_editor(item_id))
            else:
                # Normal playlist options
                menu.addAction("Edit Playlist Metadata", self.edit_playlist)
                menu.addAction("Open Track Editor", lambda: self.open_playlist_editor(item_id))

            menu.addAction("Shuffle", lambda: self.shuffle_playlist(item_id))
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

        if len(playlist_items) > 1:
            menu.addAction(
                f"View Tracks ({len(playlist_items)} playlists)",
                lambda: self.view_tracks_for_selected_playlists(playlist_items),
            )
            menu.addAction(
                f"Shuffle ({len(playlist_items)} playlists)",
                lambda: self.shuffle_playlists(playlist_items),
            )
            menu.addSeparator()

        if playlists_with_parent:
            menu.addAction(
                "Add All Tracks to Parent Playlist",
                lambda: self._add_tracks_to_parent_playlists(playlists_with_parent),
            )
            menu.addSeparator()

        menu.addAction("Delete", self.delete_selected)
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _load_tracks_for_playlists(self, playlist_ids: list) -> list | None:
        """Fetch the deduplicated tracks for one or more playlists, or None on error."""
        try:
            playlist_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id__in=playlist_ids
            )
            track_ids = list({pt.track_id for pt in playlist_tracks})
            return (
                self.controller.get.get_all_entities("Track", track_id__in=track_ids)
                if track_ids
                else []
            )
        except SQLAlchemyError as e:
            logger.error(f"Error loading tracks for playlists {playlist_ids}: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to load tracks for playlists")
            return None

    def view_tracks_for_selected_playlists(self, items) -> None:
        """Open a combined, deduplicated tracks view for multiple selected playlists."""
        playlist_ids = [it.data(0, Qt.UserRole)[1] for it in items]
        tracks = self._load_tracks_for_playlists(playlist_ids)
        if tracks is None:
            return

        names = ", ".join(it.text(0) for it in items)
        tracks_window = BaseTrackView(
            controller=self.controller,
            tracks=tracks,
            title=f"Tracks in {len(playlist_ids)} playlists: {names}",
        )
        tracks_window.exec_()

    def shuffle_playlist(self, playlist_id: int) -> None:
        """Shuffle one playlist's tracks into the queue, without opening its tracks view."""
        tracks = self._load_tracks_for_playlists([playlist_id])
        if tracks is not None:
            shuffle_and_play(self, self.controller, tracks)

    def shuffle_playlists(self, items) -> None:
        """Shuffle the combined, deduplicated tracks of multiple selected playlists."""
        playlist_ids = [it.data(0, Qt.UserRole)[1] for it in items]
        tracks = self._load_tracks_for_playlists(playlist_ids)
        if tracks is not None:
            shuffle_and_play(self, self.controller, tracks)

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

        child_names = ", ".join(item.text(0) for children in groups.values() for item in children)
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
                next_position = max((t.position for t in existing_tracks), default=0) + 1

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
            logger.info(f"Added {total_added} track(s) from {child_names} to {parent_names}")
            show_status_message(self, f"Added {total_added} track(s) to the parent playlist(s).")

        except SQLAlchemyError as e:
            logger.error(f"Failed to add tracks to parent playlist: {e!s}")
            QMessageBox.critical(self, "Error", f"Failed to add tracks to parent playlist:\n{e!s}")

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
        recursive_total = getattr(playlist_obj, "recursive_track_count", own_count) or own_count
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

    def _on_item_renamed(self, item: QTreeWidgetItem, column: int) -> None:
        """Save an in-place tree rename (double-click/F2) back to the database."""
        if column != 0:
            return
        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2 or item_data[0] != "playlist":
            return

        playlist_id = item_data[1]
        new_name = item.text(0).strip()
        if new_name.startswith("🔍"):
            # Strip the smart-playlist marker -- it's a display-only prefix,
            # not part of the stored name.
            new_name = new_name[1:].strip()

        if not new_name:
            show_status_message(self, "Playlist name cannot be empty.")
            self.tree_dnd.refresh_item_display(item)
            return

        try:
            self.controller.update.update_entity("Playlist", playlist_id, playlist_name=new_name)
            logger.info(f"Renamed playlist {playlist_id} to {new_name!r}")
            self.playlist_updated.emit()
        except SQLAlchemyError as e:
            logger.error(f"Failed to rename playlist {playlist_id}: {e}")
            QMessageBox.critical(self, "Error", f"Failed to rename playlist:\n{e!s}")

        # Reformat to the canonical display form either way (re-adds the
        # smart-playlist marker, or restores the stored name on failure).
        self.tree_dnd.refresh_item_display(item)

    def toggle_flat_view(self) -> None:
        """Toggle between the nested hierarchy and a flat alphabetical list."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list.
        self.tree.setDragEnabled(not self.flat_view)
        self.load_playlists()

    def _make_playlist_item(self, playlist, depth):
        """Build a single playlist's tree item, shared by the tree and flat builders."""
        display_name = self._format_playlist_name(playlist)
        count_text = self._format_track_count(playlist)

        item = QTreeWidgetItem([display_name, count_text])
        item.setData(0, Qt.UserRole, ("playlist", playlist.playlist_id))
        item.setFlags(item.flags() | Qt.ItemIsEditable)

        # Store whether this is a smart playlist for context menu checks
        item.setData(0, Qt.UserRole + 1, getattr(playlist, "is_smart", False))

        item.setIcon(0, icon_for_depth(depth))
        self._style_count_cell(item)
        return item

    def _build_tree(self, parent_item, children_map, depth):
        """Recursively build playlist tree with smart playlist symbols."""
        parent_id = parent_item.data(0, Qt.UserRole)[1] if parent_item else None

        children = sorted(
            children_map.get(parent_id, []), key=lambda x: getattr(x, "playlist_name", "").lower()
        )

        for child in children:
            item = self._make_playlist_item(child, depth)

            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

            # Recursively add children
            if depth < self.MAX_HIERARCHY_DEPTH:
                self._build_tree(item, children_map, depth + 1)
            elif children_map.get(child.playlist_id):
                # Deeper descendants exist but the tree stops here -- warn
                # instead of silently hiding them with no way to reach them.
                logger.warning(
                    f"Playlist '{child.playlist_name}' (id={child.playlist_id}) has "
                    f"sub-playlists beyond the max hierarchy depth "
                    f"({self.MAX_HIERARCHY_DEPTH}); they are not shown in the tree."
                )

    def _build_flat(self, playlists) -> None:
        """Populate the tree as a single alphabetical list with no nesting."""
        for playlist in sorted(playlists, key=lambda x: getattr(x, "playlist_name", "").lower()):
            item = self._make_playlist_item(playlist, 0)
            self.tree.addTopLevelItem(item)

    def create_normal_playlist(self) -> None:
        """Open dialog to create a new playlist with name and description."""
        dialog = PlaylistCreateDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            name, description = dialog.get_data()

            if not name:
                show_status_message(self, "Playlist name cannot be empty.")
                return

            try:
                # Add to database using your controller's create method
                self.controller.add.add_entity(
                    "Playlist", playlist_name=name, playlist_description=description
                )

                # Refresh the UI
                self.load_playlists()
                self.playlist_updated.emit()
                logger.info(f"Created new playlist: {name}")

            except SQLAlchemyError as e:
                logger.error(f"Failed to create playlist: {e!s}")
                QMessageBox.critical(self, "Error", f"Could not create playlist: {e}")

    def create_smart_playlist(self):
        """Open dialog for creating a smart playlist."""
        dialog = SmartPlaylistCreateDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            name, description, logic, criteria, auto_refresh = dialog.get_data()

            if not name:
                show_status_message(self, "Playlist name cannot be empty.")
                return

            playlist = None
            try:
                # Create the Playlist record
                playlist = self.controller.add.add_entity(
                    "Playlist", playlist_name=name, playlist_description=description, is_smart=1
                )

                # Create the SmartPlaylist record (stores logic = AND/OR)
                smart_playlist = self.controller.add.add_entity(
                    "SmartPlaylist",
                    playlist_id=playlist.playlist_id,
                    logic=logic,
                    auto_refresh=int(auto_refresh),
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

                # Immediately populate the playlist with matching tracks,
                # off the UI thread — a large library can make this slow.
                self.refresh_controller.start_refresh(
                    playlist.playlist_id,
                    lambda success, pid: self.refresh_controller.on_created_playlist_refreshed(
                        success, pid, name
                    ),
                )

            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"Failed to create smart playlist: {e!s}")
                # The Playlist row (if it made it in) has no usable
                # SmartPlaylist/criteria behind it yet -- remove it instead
                # of leaving a broken is_smart=1 playlist the user can see
                # but that will never refresh correctly.
                if playlist is not None:
                    try:
                        self.controller.delete.delete_entity("Playlist", playlist.playlist_id)
                        self.load_playlists()
                    except SQLAlchemyError as cleanup_exc:
                        logger.error(
                            f"Failed to remove orphaned playlist after error: {cleanup_exc}"
                        )
                QMessageBox.critical(self, "Error", f"Could not create smart playlist: {e}")

    def delete_selected(self) -> None:
        item = self.tree.currentItem()
        if not item:
            return
        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2:
            return
        item_type, item_id = item_data

        try:
            playlist_obj = self.controller.get.get_entity_object("Playlist", playlist_id=item_id)
            name = playlist_obj.playlist_name if playlist_obj else item.text(0)
        except SQLAlchemyError as e:
            logger.warning(f"Could not load playlist name for delete confirmation: {e}")
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

            except SQLAlchemyError as e:
                logger.error(f"Deletion failed: {e!s}")
                QMessageBox.critical(self, "Error", f"Failed to delete {item_type}:\n{e!s}")

    def handle_drop(self, event: Any) -> None:
        # Kept as a PlaylistView method (delegating to PlaylistTreeDnD) so
        # QTreeWidget's dropEvent override and external callers can keep
        # calling view.handle_drop(event) directly.
        self.tree_dnd.handle_drop(event)

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
            playlist = self.controller.get.get_entity_object("Playlist", playlist_id=playlist_id)
        except SQLAlchemyError as e:
            logger.error(f"Failed to fetch playlist object: {e!s}")
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
            # Dialog saved changes — now re-evaluate which tracks match,
            # off the UI thread.
            self.refresh_controller.start_refresh(
                playlist_id, self.refresh_controller.on_edited_playlist_refreshed
            )
