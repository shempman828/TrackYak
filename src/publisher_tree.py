from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)

from src.logger_config import logger


class PublisherTreeWidget(QTreeWidget):
    """Modern tree widget for publishers with drag-and-drop and sorting."""

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
        except Exception as e:
            logger.error(f"Failed loading publishers: {str(e)}")
            return

        self.clear()

        # Create dictionaries for hierarchy
        publisher_dict = {}
        root_items = []

        # First pass: create all items with recursive track count
        for publisher in publishers:
            album_count = self.calculate_recursive_album_count(publisher.publisher_id)
            item = QTreeWidgetItem()
            item.setText(0, publisher.publisher_name)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setData(1, Qt.DisplayRole, album_count)
            item.setData(0, Qt.UserRole, publisher.publisher_id)

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
        except Exception as e:
            logger.error(f"Failed to rename publisher: {str(e)}")
            self.load_publishers()

    def calculate_recursive_album_count(self, publisher_id):
        """Calculate total albums for a publisher including all child publishers."""
        try:
            album_links = self.controller.get.get_entity_links(
                "AlbumPublisher", publisher_id=publisher_id
            )
            total_albums = len(album_links)

            child_publishers = self.controller.get.get_all_entities(
                "Publisher", parent_id=publisher_id
            )
            for child in child_publishers:
                total_albums += self.calculate_recursive_album_count(child.publisher_id)

            return total_albums

        except Exception as e:
            logger.error(f"Error calculating album count: {str(e)}")
            return 0

    def filter_items(self, search_text):
        """Filter tree items based on search text."""

        def filter_item(item, text):
            text_lower = text.lower()
            item_text_lower = item.text(0).lower()
            matches = text_lower in item_text_lower

            child_matches = False
            for i in range(item.childCount()):
                if filter_item(item.child(i), text):
                    child_matches = True

            should_show = matches or child_matches
            item.setHidden(not should_show)

            if text and should_show:
                item.setExpanded(True)
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()

            return should_show

        for i in range(self.topLevelItemCount()):
            filter_item(self.topLevelItem(i), search_text)

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
            QMessageBox.warning(
                self,
                "Invalid Operation",
                "Cannot create circular parent-child relationship.",
            )
            return

        try:
            self.controller.update.update_entity(
                "Publisher", source_id, parent_id=target_id
            )
            self.load_publishers()
            logger.info("Parent relationship updated successfully.")
        except Exception as e:
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
        except Exception as e:
            logger.error(f"Error removing parent: {str(e)}")
