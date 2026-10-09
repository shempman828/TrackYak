"""Place tree widget: drag-and-drop reparenting and search/type/data-gap filtering."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QMessageBox, QTreeWidget, QTreeWidgetItemIterator

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.place.place_hierarchy import would_create_cycle
from src.place.place_types import type_label


class DraggableTreeWidget(QTreeWidget):
    """Place tree that saves a drag-and-drop move as a new parent in the database."""

    def __init__(self, list_view):
        super().__init__()
        self.list_view = list_view

    def _drop_parent_id(self, target_item, indicator, dragged_items):
        """Parent id that a drop at `target_item` gives the dragged rows."""
        if target_item is None or target_item in dragged_items:
            return None  # empty space or one of the dragged rows: top level
        if indicator == QAbstractItemView.OnItem:
            return target_item.data(0, Qt.UserRole).place_id
        # Above/below a row: become its sibling, as Qt's drop indicator shows.
        sibling_parent = target_item.parent()
        return sibling_parent.data(0, Qt.UserRole).place_id if sibling_parent is not None else None

    def dropEvent(self, event):
        """Save the dropped rows' new parent, refusing a drop that puts a place inside itself."""
        # Read the dragged rows and drop target BEFORE super() moves them.
        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        new_parent_id = self._drop_parent_id(self.itemAt(event.pos()), self.dropIndicatorPosition(), dragged_items)
        moved_places = [item.data(0, Qt.UserRole) for item in dragged_items]
        if would_create_cycle(self.list_view.places_by_id, [p.place_id for p in moved_places], new_parent_id):
            event.ignore()
            show_status_message(self, "A place cannot go inside itself or one of its own child places.")
            return

        super().dropEvent(event)

        failed = []
        for place in moved_places:
            if self.list_view.controller.update.update_entity("Place", place.place_id, parent_id=new_parent_id):
                logger.info(f"Updated parent for {place.place_name} to {new_parent_id}")
            else:
                failed.append(place.place_name)

        # Reload so the rows show the real database state (this also undoes a failed move).
        self.list_view.refresh_all()
        if failed:
            QMessageBox.critical(self, "Error", "Could not move these places:\n" + "\n".join(failed))

    def count_items(self):
        """Return (total, visible) place rows, counted in one pass."""
        total = visible = 0
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            total += 1
            if not iterator.value().isHidden():
                visible += 1
            iterator += 1
        return total, visible

    def filter_items(self, search_text, selected_types=None, mbid_missing_only=False, coords_missing_only=False, no_parent_only=False, expand_matches=False):
        """Hide rows that fail the filters; a row stays visible while any descendant matches."""
        text_lower = search_text.lower()

        def item_matches(place):
            if place is None:
                return False
            if text_lower not in (place.place_name or "").lower():
                return False
            if selected_types is not None and type_label(place.place_type) not in selected_types:
                return False
            if mbid_missing_only and place.MBID:
                return False
            if no_parent_only and place.parent_id is not None:
                return False
            return not (coords_missing_only and place.place_latitude is not None and place.place_longitude is not None)

        def filter_item(item):
            child_matches = False
            for i in range(item.childCount()):
                if filter_item(item.child(i)):
                    child_matches = True
            should_show = item_matches(item.data(0, Qt.UserRole)) or child_matches
            item.setHidden(not should_show)
            # Children are visited first, so expanding only rows with a visible
            # child opens every path down to a match without walking up again.
            if expand_matches and child_matches:
                item.setExpanded(True)
            return should_show

        for i in range(self.topLevelItemCount()):
            filter_item(self.topLevelItem(i))
