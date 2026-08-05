from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
)

from src.core.asset_paths import icon
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.db.db_tables import AlbumPublisher


class PublisherTreeWidget(QTreeWidget):
    """Modern tree widget for publishers with drag-and-drop and sorting."""

    # Extra data roles for filter criteria, alongside Qt.UserRole (publisher_id)
    _MBID_ROLE = Qt.UserRole + 1
    _FIXED_ROLE = Qt.UserRole + 2

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setHeaderHidden(False)
        self.setColumnCount(2)
        self.setHeaderLabels(["Publisher", "Albums"])
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setSortingEnabled(True)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        # Allow selecting multiple items at once (Ctrl+click, Shift+click)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.itemChanged.connect(self.on_item_changed)

    def load_publishers(self):
        """Load publishers as a hierarchical tree with track counts.

        Preserves the current sort column, sort order, and scroll position
        so that edits don't jump the user back to the top.
        """
        # --- Save state before clearing ---
        sort_column = self.header().sortIndicatorSection()
        sort_order = self.header().sortIndicatorOrder()
        scrollbar = self.verticalScrollBar()
        scroll_value = scrollbar.value() if scrollbar else 0

        try:
            publishers = self.controller.get.get_all_entities("Publisher")
        except SQLAlchemyError as e:
            logger.error(f"Failed loading publishers: {str(e)}")
            return

        self.clear()

        recursive_counts = self.calculate_recursive_album_counts(publishers)

        # Create dictionaries for hierarchy
        publisher_dict = {}
        root_items = []

        # First pass: create all items with recursive track count
        for publisher in publishers:
            album_count = recursive_counts.get(publisher.publisher_id, 0)
            item = QTreeWidgetItem()
            name = publisher.publisher_name
            if publisher.MBID:
                name = f"{name} \U0001f517"
                item.setToolTip(0, "Linked to MusicBrainz")
            item.setText(0, name)
            if publisher.is_fixed:
                item.setIcon(0, icon("checkmark.svg"))
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setData(1, Qt.DisplayRole, album_count)
            item.setData(0, Qt.UserRole, publisher.publisher_id)
            item.setData(0, self._MBID_ROLE, bool(publisher.MBID))
            item.setData(0, self._FIXED_ROLE, bool(publisher.is_fixed))

            publisher_dict[publisher.publisher_id] = {
                "item": item,
                "publisher": publisher,
            }

        # Second pass: build hierarchy
        for publisher_id, data in publisher_dict.items():
            publisher = data["publisher"]
            item = data["item"]

            if publisher.parent_id is None:
                root_items.append(item)
            else:
                parent_data = publisher_dict.get(publisher.parent_id)
                if parent_data:
                    parent_data["item"].addChild(item)
                else:
                    root_items.append(item)

        # Add root items
        self.addTopLevelItems(root_items)

        # --- Restore sort order ---
        self.sortByColumn(sort_column, sort_order)

        self.expandAll()

        # --- Restore scroll position ---
        if scrollbar:
            scrollbar.setValue(scroll_value)

    def keyPressEvent(self, event):
        """Trigger delete when the Delete key is pressed."""
        if event.key() == Qt.Key_Delete:
            # Walk up to the parent PublisherView and call its delete method
            parent_view = self.parent()
            while parent_view is not None:
                if hasattr(parent_view, "_delete_selected_publisher"):
                    parent_view._delete_selected_publisher()
                    return
                parent_view = parent_view.parent()
        # For all other keys, use default behaviour
        super().keyPressEvent(event)

    def on_item_changed(self, item, column):
        """Handle inline rename after the user finishes editing."""
        if column != 0:
            return

        new_name = item.text(0)
        publisher_id = item.data(0, Qt.UserRole)

        try:
            self.controller.update.update_entity(
                "Publisher", publisher_id, publisher_name=new_name
            )
            logger.info(f"Publisher renamed to: {new_name}")
        except SQLAlchemyError as e:
            logger.error(f"Failed to rename publisher: {str(e)}")
            self.load_publishers()

    def calculate_recursive_album_counts(self, publishers):
        """Calculate total albums per publisher, including all child publishers.

        Uses a single grouped query for direct album counts (like
        genre_view.py does for genres) instead of one AlbumPublisher query
        per publisher, then sums child totals into parents bottom-up in
        Python instead of re-querying each descendant subtree once per
        ancestor. This turned ~12,000 sequential queries (and a 2+ minute
        UI freeze) with ~2,187 publishers into a single query.
        """
        try:
            direct_counts = dict(
                self.controller.get.session.execute(
                    select(AlbumPublisher.publisher_id, func.count()).group_by(
                        AlbumPublisher.publisher_id
                    )
                ).all()
            )

            children_map = defaultdict(list)
            for publisher in publishers:
                children_map[publisher.parent_id].append(publisher.publisher_id)

            totals = {}

            def total_for(publisher_id):
                if publisher_id not in totals:
                    total = direct_counts.get(publisher_id, 0)
                    for child_id in children_map.get(publisher_id, []):
                        total += total_for(child_id)
                    totals[publisher_id] = total
                return totals[publisher_id]

            for publisher in publishers:
                total_for(publisher.publisher_id)

            return totals

        except SQLAlchemyError as e:
            logger.error(f"Error calculating album counts: {str(e)}")
            return {}

    def count_total(self):
        """Count all publisher items in the tree, regardless of visibility."""
        count = 0
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            count += 1
            iterator += 1
        return count

    def count_visible(self):
        """Count publisher items currently visible (not hidden by search filter)."""
        count = 0
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            if not iterator.value().isHidden():
                count += 1
            iterator += 1
        return count

    def filter_items(self, search_text, mbid_filter="Any", fixed_filter="Any"):
        """Filter tree items based on search text, MusicBrainz link status, and fixed status.

        An item is shown if it matches all active criteria itself, or if any
        descendant does (so ancestors of a match stay visible for context).
        """
        text_lower = search_text.lower()
        has_criteria = bool(search_text) or mbid_filter != "Any" or fixed_filter != "Any"

        def item_matches(item):
            if text_lower and text_lower not in item.text(0).lower():
                return False
            if mbid_filter == "Linked" and not item.data(0, self._MBID_ROLE):
                return False
            if mbid_filter == "Not Linked" and item.data(0, self._MBID_ROLE):
                return False
            if fixed_filter == "Fixed" and not item.data(0, self._FIXED_ROLE):
                return False
            if fixed_filter == "Not Fixed" and item.data(0, self._FIXED_ROLE):
                return False
            return True

        def filter_item(item):
            matches = item_matches(item)

            child_matches = False
            for i in range(item.childCount()):
                if filter_item(item.child(i)):
                    child_matches = True

            should_show = matches or child_matches
            item.setHidden(not should_show)

            if has_criteria and should_show:
                item.setExpanded(True)
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()

            return should_show

        for i in range(self.topLevelItemCount()):
            filter_item(self.topLevelItem(i))

    def startDrag(self, supportedActions):
        """Start drag operation for parent-child relationships."""
        items = self.selectedItems()
        if not items:
            return

        mime_data = QMimeData()
        mime_data.setText(f"publisher:{items[0].data(0, Qt.UserRole)}")

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec_(Qt.MoveAction)

    def dropEvent(self, event):
        """Handle drop to set parent-child relationships."""
        source_item = self.currentItem()
        if not source_item:
            return

        target_item = self.itemAt(event.pos())
        if not target_item:
            self.remove_parent(source_item)
            return

        source_id = source_item.data(0, Qt.UserRole)
        target_id = target_item.data(0, Qt.UserRole)

        if source_id == target_id:
            return

        if self.is_child_of(target_item, source_item):
            show_status_message(self, "Cannot create circular parent-child relationship.")
            return

        try:
            self.controller.update.update_entity(
                "Publisher", source_id, parent_id=target_id
            )
            self.load_publishers()
            logger.info("Parent relationship updated successfully.")
        except SQLAlchemyError as e:
            logger.error(f"Error updating parent: {str(e)}")

    def is_child_of(self, parent_item, child_item):
        """Check if child_item is a descendant of parent_item."""
        current = child_item.parent()
        while current:
            if current == parent_item:
                return True
            current = current.parent()
        return False

    def remove_parent(self, item):
        """Remove parent from item."""
        publisher_id = item.data(0, Qt.UserRole)
        try:
            self.controller.update.update_entity(
                "Publisher", publisher_id, parent_id=None
            )
            self.load_publishers()
        except SQLAlchemyError as e:
            logger.error(f"Error removing parent: {str(e)}")
