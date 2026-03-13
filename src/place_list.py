import html as html_escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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

from src.logger_config import logger
from src.place_assoc_details import AssociationDetailsDialog
from src.place_detail import PlaceDetailView
from src.place_edit import PlaceEditDialog
from src.place_html import HtmlDelegate


class DraggableTreeWidget(QTreeWidget):
    """QTreeWidget subclass that correctly handles drag-and-drop to update the database."""

    def __init__(self, list_view):
        super().__init__()
        # Keep a reference to the ListView so we can call its controller and refresh
        self.list_view = list_view

    def dropEvent(self, event):
        """Called when a drag-and-drop is completed inside the tree."""
        # IMPORTANT: Read the dragged item and drop target BEFORE calling super().
        dragged_item = self.currentItem()
        if not dragged_item:
            event.ignore()
            return

        # The item the user is hovering over when they release the mouse
        target_item = self.itemAt(event.pos())

        try:
            moved_place = dragged_item.data(0, Qt.UserRole)

            # If target_item is the same as dragged_item, the user dropped on itself — ignore
            if target_item is None or target_item is dragged_item:
                new_parent_id = None
            else:
                parent_place = target_item.data(0, Qt.UserRole)
                new_parent_id = parent_place.place_id

            # Now let Qt handle the visual repositioning
            super().dropEvent(event)

            # Save the new parent to the database
            self.list_view.controller.update.update_entity(
                "Place", moved_place.place_id, parent_id=new_parent_id
            )

            logger.info(
                f"Updated parent for {moved_place.place_name} to {new_parent_id}"
            )

            # Reload both views so tree items always reflect the real database state
            if self.list_view.parent_view:
                self.list_view.parent_view.refresh_views()

        except Exception as e:
            logger.error(f"Failed to update parent: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to update parent place")
            # Refresh to revert visual changes if the DB update failed
            if self.list_view.parent_view:
                self.list_view.parent_view.refresh_views()


class ListView(QWidget):
    """List view with CRUD operations for places"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.parent_view = None
        self.init_ui()

    def set_parent_view(self, parent_view):
        """Set reference to parent PlaceView for cross-view updates"""
        self.parent_view = parent_view

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # Add button
        control_layout = QHBoxLayout()
        self.add_button = QPushButton("Add Place")
        self.add_button.clicked.connect(self.add_place)
        control_layout.addWidget(self.add_button)
        control_layout.addStretch()

        # tree widget with proper selection styling
        self.tree_widget = DraggableTreeWidget(self)
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_widget.customContextMenuRequested.connect(self.show_context_menu)
        self.tree_widget.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree_widget.setItemDelegate(HtmlDelegate())
        self.tree_widget.setObjectName("placeList")
        self.tree_widget.setDragEnabled(True)
        self.tree_widget.setAcceptDrops(True)
        self.tree_widget.setDropIndicatorShown(True)
        self.tree_widget.setDragDropMode(QTreeWidget.InternalMove)

        main_layout.addLayout(control_layout)
        main_layout.addWidget(self.tree_widget)

    def load_places(self):
        """Load places into the tree with hierarchical indentation."""
        self.tree_widget.clear()
        places = self.controller.get.get_all_entities("Place")
        hierarchy = self._build_hierarchy(places)

        # Add places to the tree with expand/collapse capability
        self._add_places_to_tree(hierarchy, None, self.tree_widget.invisibleRootItem())

    def show_context_menu(self, position):
        """Show context menu for tree items."""
        item = self.tree_widget.itemAt(position)
        if not item:
            return

        # Capture the place into a local variable. Each lambda below uses a
        # default argument (p=place) to lock in this value at menu-creation
        # time, so the action always operates on the right place even if
        # something triggers a re-render before the user clicks.
        place = item.data(0, Qt.UserRole)

        menu = QMenu(self)
        menu.addAction(
            "View Associations", lambda p=place: self.show_association_details_for(p)
        )
        menu.addAction("View Details", lambda p=place: self.view_place_details_for(p))
        menu.addAction("Edit", lambda p=place: self.edit_place_for(p))
        menu.addAction("Merge", lambda p=place: self.merge_place(p))
        menu.addAction("Split", lambda p=place: self._split_place())
        menu.addAction("Delete", lambda p=place: self.delete_place_for(p))

        menu.exec_(self.tree_widget.viewport().mapToGlobal(position))

    def _build_hierarchy(self, places):
        """Build a dictionary of parent-child relationships."""
        hierarchy = {}
        for place in places:
            parent_id = place.parent_id
            if parent_id not in hierarchy:
                hierarchy[parent_id] = []
            hierarchy[parent_id].append(place)
        return hierarchy

    def _add_places_to_tree(self, hierarchy, parent_id, parent_tree_item):
        """Recursively add places to the tree widget."""
        if parent_id not in hierarchy:
            return

        for place in hierarchy[parent_id]:
            item_text = self.format_list_item(place)
            item = QTreeWidgetItem([item_text])
            item.setData(0, Qt.UserRole, place)
            item.setToolTip(0, self.create_tooltip(place))

            parent_tree_item.addChild(item)

            self._add_places_to_tree(hierarchy, place.place_id, item)

    def format_list_item(self, place):
        """Return HTML-formatted text for the place entry."""
        place_name = self._escape_html(place.place_name)
        place_type = (
            self._escape_html(place.place_type) if place.place_type else "Unknown"
        )

        base_text = (
            f"<span class='place-name'>{place_name}</span> "
            f"(<span class='place-type'>{place_type}</span>)"
        )

        direct = place.association_count
        recursive = place.recursive_association_count

        if direct == 0 and recursive == 0:
            assoc_text = "<span class='no-assoc'> - no associations</span>"
        elif recursive > direct:
            # Has associations in child places too — show both counts
            assoc_text = (
                f"<span class='assoc-count'> - {direct} direct"
                f", {recursive} total</span>"
            )
        else:
            assoc_text = f"<span class='assoc-count'> - {direct} association(s)</span>"

        return f"<div style='font-family:inherit'>{base_text}{assoc_text}</div>"

    def _escape_html(self, text):
        """Escape HTML entities for safe display."""
        if text is None:
            return ""
        return html_escape.escape(str(text))

    def create_tooltip(self, place):
        """Create detailed tooltip for place."""
        direct = place.association_count
        recursive = place.recursive_association_count

        tooltip = f"Name: {place.place_name}\n"
        tooltip += f"Type: {place.place_type}\n"
        tooltip += f"Coordinates: {place.place_latitude}, {place.place_longitude}\n"
        tooltip += f"Description: {place.place_description or 'No description'}\n"
        tooltip += f"Direct associations: {direct}"
        if recursive > direct:
            tooltip += f"\nTotal (including children): {recursive}"
        return tooltip

    def show_association_details_for(self, place):
        """Show detailed view of all entities associated with the given place."""
        dialog = AssociationDetailsDialog(self.controller, place, self)
        dialog.exec_()

    def show_association_details(self):
        """Show detailed view of all entities associated with the selected place."""
        selected = self.tree_widget.currentItem()
        if not selected:
            QMessageBox.information(
                self, "No Selection", "Please select a place to view its associations."
            )
            return
        self.show_association_details_for(selected.data(0, Qt.UserRole))

    def add_place(self):
        """Add place and refresh both views"""
        dialog = PlaceEditDialog(self.controller, self)
        if dialog.exec_() == QDialog.Accepted:
            new_place = dialog.get_place_data()
            try:
                self.controller.add.add_entity("Place", **new_place)
                if self.parent_view:
                    self.parent_view.refresh_views()
                logger.info("Place created successfully")
            except Exception as e:
                logger.error(f"Failed to create place: {str(e)}")
                QMessageBox.critical(self, "Error", "Failed to create place")

    def edit_place_for(self, old_place):
        """Edit the given place and refresh both views."""
        dialog = PlaceEditDialog(self.controller, self, old_place)
        if dialog.exec_() == QDialog.Accepted:
            updated_data = dialog.get_place_data()
            try:
                self.controller.update.update_entity(
                    "Place", old_place.place_id, **updated_data
                )
                if self.parent_view:
                    self.parent_view.refresh_views()
                logger.info("Place updated successfully")
            except Exception as e:
                logger.error(f"Failed to update place: {str(e)}")
                QMessageBox.critical(self, "Error", "Failed to update place")

    def edit_place(self):
        """Edit the currently selected place."""
        selected = self.tree_widget.currentItem()
        if not selected:
            return
        self.edit_place_for(selected.data(0, Qt.UserRole))

    def delete_place_for(self, place):
        """Delete the given place after confirmation."""
        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete {place.place_name} permanently?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            try:
                self.controller.delete.delete_entity("Place", place.place_id)
                if self.parent_view:
                    self.parent_view.refresh_views()
                logger.info("Place deleted successfully")
            except Exception as e:
                logger.error(f"Failed to delete place: {str(e)}")
                QMessageBox.critical(self, "Error", "Failed to delete place")

    def delete_place(self):
        """Delete the currently selected place."""
        selected = self.tree_widget.currentItem()
        if not selected:
            return
        self.delete_place_for(selected.data(0, Qt.UserRole))

    def merge_place(self, place):
        """Merge this place into another. Not yet implemented."""
        QMessageBox.information(
            self, "Not Implemented", "Merge is not yet available for places."
        )

    def _split_place(self):
        """Split this place into multiple places. Not yet implemented."""
        QMessageBox.information(
            self, "Not Implemented", "Split is not yet available for places."
        )

    def view_place_details_for(self, place):
        """View detailed information about the given place."""
        dialog = PlaceDetailView(self.controller, place, self)
        dialog.exec_()
