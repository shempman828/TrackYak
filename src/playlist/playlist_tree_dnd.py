"""Drag-and-drop reparenting for the playlist tree."""

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTreeWidgetItem
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.hierarchy_tree_style import icon_for_depth
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


class PlaylistTreeDnD:
    """Drag-and-drop reparenting for PlaylistView's tree."""

    # Holds a back-reference to the view for tree/controller access and its
    # name/count formatting helpers.

    def __init__(self, view) -> None:
        """Bind the helper to `view`."""
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

    @staticmethod
    def _subtree_height(item: QTreeWidgetItem) -> int:
        """Return how many levels are nested below `item` (0 for a leaf)."""
        return max((1 + PlaylistTreeDnD._subtree_height(item.child(i)) for i in range(item.childCount())), default=0)

    def _update_subtree_depth(self, item: QTreeWidgetItem, depth: int) -> None:
        """Recompute the depth icon for `item` and everything below it."""
        item.setIcon(0, icon_for_depth(depth))
        for i in range(item.childCount()):
            self._update_subtree_depth(item.child(i), depth + 1)

    def refresh_item_display(self, item: QTreeWidgetItem) -> None:
        """Re-fetch one playlist from the DB and update its row in place."""
        view = self.view
        item_data = item.data(0, Qt.UserRole)
        if not item_data or len(item_data) != 2 or item_data[0] != "playlist":
            return
        try:
            playlist_obj = view.controller.get.get_entity_object("Playlist", playlist_id=item_data[1])
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

    def _dragged_items(self) -> list[QTreeWidgetItem]:
        """Return the selected items to move, leaving out any whose ancestor is also moving."""
        tree = self.view.tree
        selected = tree.selectedItems()
        current = tree.currentItem()
        if current is not None and current not in selected:
            selected = [current]
        return [it for it in selected if not any(self._is_descendant(other, it) for other in selected if other is not it)]

    def _drop_parent(self, target_item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        """Return the item a drop on `target_item` should nest under (None = top level)."""
        if target_item is None:
            return None
        # Dropping between rows makes the playlist a sibling of the target,
        # not its child.
        if self.view.tree.dropIndicatorPosition() in (QAbstractItemView.AboveItem, QAbstractItemView.BelowItem):
            return target_item.parent()
        return target_item

    def handle_drop(self, event: Any) -> None:
        """Move the dragged playlist(s) under the drop target, in the DB and in place in the tree."""
        view = self.view
        tree = view.tree
        dragged_items = self._dragged_items()
        if not dragged_items:
            event.ignore()
            return

        new_parent_item = self._drop_parent(tree.itemAt(event.pos()))
        new_parent_data = new_parent_item.data(0, Qt.UserRole) if new_parent_item is not None else None
        if new_parent_item is not None and not new_parent_data:
            # Dropped on a non-playlist row (e.g. the empty-state placeholder).
            event.ignore()
            return
        new_parent_id = new_parent_data[1] if new_parent_data else None
        new_depth = self._depth_of(new_parent_item) + 1 if new_parent_item is not None else 0

        moved = []
        try:
            for dragged_item in dragged_items:
                dragged_data = dragged_item.data(0, Qt.UserRole)
                if not dragged_data:
                    continue
                dragged_id = dragged_data[1]

                # Dropping a playlist onto itself or one of its own descendants
                # would create a cycle in the tree - refuse it.
                if new_parent_item is dragged_item or self._is_descendant(dragged_item, new_parent_item):
                    continue
                # Rows deeper than MAX_HIERARCHY_DEPTH are not built, so the
                # moved playlist would vanish on the next reload.
                if new_depth + self._subtree_height(dragged_item) > view.MAX_HIERARCHY_DEPTH:
                    show_status_message(view, f"Cannot nest '{dragged_item.text(0)}' there: playlists can be at most {view.MAX_HIERARCHY_DEPTH + 1} levels deep.")
                    continue

                old_parent_item = dragged_item.parent()
                # update_entity logs and returns False on failure instead of raising.
                if view.controller.update.update_entity("Playlist", dragged_id, parent_id=new_parent_id) is False:
                    continue
                self._move_item(dragged_item, old_parent_item, new_parent_item)
                moved.append((dragged_item, dragged_id, old_parent_item))
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Drag-drop error: {e!s}")

        if not moved:
            event.ignore()
            return

        # The recursive track counts shown on the old and new ancestor chains changed.
        refreshed: set[int] = set()
        for start in [old_parent for _, _, old_parent in moved] + [new_parent_item]:
            ancestor = start
            while ancestor is not None and id(ancestor) not in refreshed:
                refreshed.add(id(ancestor))
                self.refresh_item_display(ancestor)
                ancestor = ancestor.parent()

        last_item = moved[-1][0]
        last_item.setExpanded(True)
        tree.setCurrentItem(last_item)
        view.playlist_updated.emit()
        logger.debug(f"Moved playlist(s) {[pid for _, pid, _ in moved]} to parent {new_parent_id}")

        # We've already re-parented the items ourselves. The tree runs in
        # InternalMove mode, so if we let this drop resolve as a MoveAction
        # QAbstractItemView.startDrag() would then run its own
        # clearOrRemove() on the selected rows - deleting the items we just
        # moved out from under their new parent. Report IgnoreAction so Qt
        # leaves the tree alone.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()

    def _move_item(self, item: QTreeWidgetItem, old_parent: QTreeWidgetItem | None, new_parent: QTreeWidgetItem | None) -> None:
        """Move `item` in the tree in place, keeping selection, scroll and expanded state."""
        tree = self.view.tree
        if old_parent is not None:
            old_parent.removeChild(item)
        else:
            tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        if new_parent is not None:
            new_parent.addChild(item)
        else:
            tree.addTopLevelItem(item)
        self._update_subtree_depth(item, self._depth_of(item))
