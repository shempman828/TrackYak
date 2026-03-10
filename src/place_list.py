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
        # After super().dropEvent(), Qt's InternalMove has already repositioned the
        # item in the tree, so dropped_item.parent() no longer tells us what the
        # user dropped onto — it tells us where Qt moved it (usually a sibling slot).
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

        # FIX: Store the full Place object (not just the ID) with a clear name.
        # Previously named self.current_place_id but it was actually the Place object,
        # which was confusing. Now clearly named self.current_place.
        self.current_place = item.data(0, Qt.UserRole)

        menu = QMenu(self)
        menu.addAction("View Tracks", lambda: self.view_tracks_for_selected_place())
        menu.addAction("Edit", lambda: self.edit_place())
        menu.addAction("Merge", lambda: self.merge_place(self.current_place))
        menu.addAction("Split", lambda: self._split_place())
        menu.addAction("Delete", lambda: self.delete_place())

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
            # Count associations for this place
            association_count = self.get_association_count(place.place_id)

            # Create tree item
            item_text = self.format_list_item(place, association_count, "")
            item = QTreeWidgetItem([item_text])
            item.setData(0, Qt.UserRole, place)
            item.setToolTip(0, self.create_tooltip(place, association_count))

            # Add to parent
            parent_tree_item.addChild(item)

            # Recurse into children
            self._add_places_to_tree(hierarchy, place.place_id, item)

    def get_association_count(self, place_id):
        """Get total number of associations for a place."""
        try:
            associations = self.controller.get.get_all_entities("PlaceAssociation")
            count = sum(1 for assoc in associations if assoc.place_id == place_id)
            return count
        except Exception as e:
            logger.error(f"Error getting association count: {str(e)}")
            return 0

    def get_detailed_associations(self, place_id):
        """Get detailed breakdown of associations by entity type."""
        try:
            associations = self.controller.get.get_all_entities("PlaceAssociation")
            entity_counts = {}

            for assoc in associations:
                if assoc.place_id == place_id:
                    entity_type = assoc.entity_type
                    entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1

            return entity_counts
        except Exception as e:
            logger.error(f"Error getting detailed associations: {str(e)}")
            return {}

    def format_list_item(self, place, association_count, indent):
        """Return HTML-formatted text for the place entry."""
        place_name = self._escape_html(place.place_name)
        place_type = (
            self._escape_html(place.place_type) if place.place_type else "Unknown"
        )

        base_text = (
            f"{indent}<span class='place-name'>{place_name}</span> "
            f"(<span class='place-type'>{place_type}</span>)"
        )

        if association_count and association_count > 0:
            assoc_text = f"<span class='assoc-count'> - {association_count} association(s)</span>"
        else:
            assoc_text = "<span class='no-assoc'> - no associations</span>"

        return f"<div style='font-family:inherit'>{base_text}{assoc_text}</div>"

    def _escape_html(self, text):
        """Escape HTML entities for safe display."""
        if text is None:
            return ""
        return html_escape.escape(str(text))

    def create_tooltip(self, place, association_count):
        """Create detailed tooltip for place."""
        tooltip = f"Name: {place.place_name}\n"
        tooltip += f"Type: {place.place_type}\n"
        tooltip += f"Coordinates: {place.place_latitude}, {place.place_longitude}\n"
        tooltip += f"Description: {place.place_description or 'No description'}\n"
        tooltip += f"Associations: {association_count} related entities"

        if association_count > 0:
            associations = self.get_detailed_associations(place.place_id)
            for entity_type, count in associations.items():
                tooltip += f"\n- {count} {entity_type}(s)"

        return tooltip

    def show_association_details(self):
        """Show detailed view of all entities associated with the selected place."""
        selected = self.tree_widget.currentItem()
        if not selected:
            QMessageBox.information(
                self, "No Selection", "Please select a place to view its associations."
            )
            return

        place = selected.data(0, Qt.UserRole)
        dialog = AssociationDetailsDialog(self.controller, place, self)
        dialog.exec_()

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

    def edit_place(self):
        """Edit place and refresh both views"""
        selected = self.tree_widget.currentItem()
        if not selected:
            return

        old_place = selected.data(0, Qt.UserRole)
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

    def delete_place(self):
        """Delete selected place"""
        selected = self.tree_widget.currentItem()
        if not selected:
            return

        place = selected.data(0, Qt.UserRole)
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

    def view_place_details(self):
        """View detailed information about the selected place."""
        selected = self.tree_widget.currentItem()
        if not selected:
            QMessageBox.information(
                self, "No Selection", "Please select a place to view its details."
            )
            return

        place = selected.data(0, Qt.UserRole)
        dialog = PlaceDetailView(self.controller, place, self)
        dialog.exec_()
