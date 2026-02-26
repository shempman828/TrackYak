from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QHeaderView, QMessageBox, QTreeWidget, QTreeWidgetItem

from logger_config import logger


class PublisherTreeWidget(QTreeWidget):
    """Modern tree widget for publishers with drag-and-drop and sorting."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setHeaderHidden(False)
        self.setColumnCount(2)
        self.setHeaderLabels(["Publisher", "Tracks"])
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.InternalMove)
        self.setSortingEnabled(True)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.itemChanged.connect(self.on_item_changed)

    def load_publishers(self):
        """Load publishers as a hierarchical tree with track counts."""
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
            track_count = self.calculate_recursive_track_count(publisher.publisher_id)
            item = QTreeWidgetItem()
            item.setText(0, publisher.publisher_name)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setData(1, Qt.DisplayRole, track_count)
            item.setData(0, Qt.UserRole, publisher.publisher_id)

            # CRITICAL: Set numeric value for sorting in Qt.DisplayRole
            item.setData(1, Qt.DisplayRole, track_count)  # Sort by this numeric value

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

        # IMPORTANT: Force a sort on the numeric column first
        self.sortByColumn(1, Qt.AscendingOrder)
        self.sortByColumn(0, Qt.AscendingOrder)  # Then sort by name

        self.expandAll()

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
            # Optional: reload to revert the text on failure
            self.load_publishers()

    def calculate_recursive_track_count(self, publisher_id):
        """Calculate total tracks for a publisher including all children."""
        try:
            # Get direct albums
            album_links = self.controller.get.get_entity_links(
                "AlbumPublisher", publisher_id=publisher_id
            )

            total_tracks = 0
            for link in album_links:
                album = self.controller.get.get_entity_object(
                    "Album", album_id=link.album_id
                )
                if album and album.track_count:
                    total_tracks += int(album.track_count)  # Ensure it's an int

            # Get child publishers and add their tracks
            child_publishers = self.controller.get.get_all_entities(
                "Publisher", parent_id=publisher_id
            )

            for child in child_publishers:
                total_tracks += self.calculate_recursive_track_count(child.publisher_id)

            return total_tracks

        except Exception as e:
            logger.error(f"Error calculating track count: {str(e)}")
            return 0

    def filter_items(self, search_text):
        """Filter tree items based on search text."""

        def filter_item(item, text):
            text_lower = text.lower()
            item_text_lower = item.text(0).lower()
            matches = text_lower in item_text_lower

            # Check children recursively
            child_matches = False
            for i in range(item.childCount()):
                if filter_item(item.child(i), text):
                    child_matches = True

            should_show = matches or child_matches
            item.setHidden(not should_show)

            # Expand if searching and matches
            if text and should_show:
                item.setExpanded(True)
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()

            return should_show

        # Apply to all top-level items
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
            # Drop on empty space - remove parent
            self.remove_parent(source_item)
            return

        source_id = source_item.data(0, Qt.UserRole)
        target_id = target_item.data(0, Qt.UserRole)

        # Prevent self-parenting
        if source_id == target_id:
            return

        # Prevent circular parenting
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
            # Refresh the tree to show new hierarchy
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
