"""playlist_tree_dnd.py"""

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.hierarchy_tree_style import icon_for_depth
from src.foundation.logger_config import logger


class PlaylistTreeDnD:
    """Drag-and-drop reparenting for PlaylistView's tree, plus the
    depth/display bookkeeping a reparent requires.

    Holds a back-reference to the view for tree/controller access and the
    name/count formatting helpers a refreshed row's label needs.
    """

    def __init__(self, view) -> None:
        self.view = view

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
    def _is_descendant(ancestor: QTreeWidgetItem, item: QTreeWidgetItem | None) -> bool:
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

    def refresh_item_display(self, item: QTreeWidgetItem) -> None:
        """Re-fetch a single playlist's counts from the DB and update its
        label in place, without touching the rest of the tree."""
        view = self.view
        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2 or item_data[0] != "playlist":
            return
        try:
            playlist_obj = view.controller.get.get_entity_object(
                "Playlist", playlist_id=item_data[1]
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to refresh playlist item display: {e!s}")
            return
        if not playlist_obj:
            return

        raw_name = view._format_playlist_name(playlist_obj)
        # Block signals -- setText() below would otherwise re-trigger
        # _on_item_renamed as though the user had edited this row by hand.
        view.tree.blockSignals(True)
        try:
            item.setText(0, raw_name)
            item.setText(1, view._format_track_count(playlist_obj))
        finally:
            view.tree.blockSignals(False)
        view._style_count_cell(item)

    def handle_drop(self, event: Any) -> None:
        view = self.view
        tree = view.tree
        target_item = tree.itemAt(event.pos())
        dragged_item = tree.currentItem()

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
            if target_item is dragged_item or self._is_descendant(dragged_item, target_item):
                event.ignore()
                return

            old_parent_item = dragged_item.parent()

            # Update the playlist's parent_id in database
            view.controller.update.update_entity("Playlist", dragged_id, parent_id=new_parent_id)

            # Move the item within the tree in place instead of reloading the
            # whole module, so selection/scroll position/expanded state don't
            # get disturbed by an unrelated drag-and-drop.
            if old_parent_item is not None:
                old_parent_item.removeChild(dragged_item)
            else:
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(dragged_item))

            if target_item is not None:
                target_item.addChild(dragged_item)
            else:
                tree.addTopLevelItem(dragged_item)

            # The moved item (and everything nested under it) sits at a new
            # depth now - refresh its indent prefix/icon to match.
            self._update_subtree_depth(dragged_item, self._depth_of(dragged_item))

            # The recursive track counts shown on both the old and new
            # ancestor chains changed - refresh just those labels.
            for ancestor in (old_parent_item, target_item):
                while ancestor is not None:
                    self.refresh_item_display(ancestor)
                    ancestor = ancestor.parent()

            dragged_item.setExpanded(True)
            tree.setCurrentItem(dragged_item)

            view.playlist_updated.emit()
            logger.debug(f"Moved playlist {dragged_id} to parent {new_parent_id}")

            # We've already re-parented the item ourselves. The tree runs in
            # InternalMove mode, so if we let this drop resolve as a MoveAction
            # QAbstractItemView.startDrag() would then run its own
            # clearOrRemove() on the selected row - deleting the item we just
            # moved out from under its new parent. Report IgnoreAction so Qt
            # leaves the tree alone.
            event.setDropAction(Qt.IgnoreAction)
            event.accept()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Drag-drop error: {e!s}")
            event.ignore()
