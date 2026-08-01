import html as html_escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.place.place_assoc_details import AssociationDetailsDialog
from src.place.place_detail import PlaceDetailView
from src.place.place_edit import PlaceEditDialog
from src.place.place_html import HtmlDelegate


class DraggableTreeWidget(QTreeWidget):
    """QTreeWidget subclass that correctly handles drag-and-drop to update the database."""

    def __init__(self, list_view):
        super().__init__()
        # Keep a reference to the ListView so we can call its controller and refresh
        self.list_view = list_view

    def dropEvent(self, event):
        """Called when a drag-and-drop is completed inside the tree."""
        # IMPORTANT: Read the dragged items and drop target BEFORE calling super().
        # Multi-selection means several rows can be dragged at once.
        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        # The item the user is hovering over when they release the mouse
        target_item = self.itemAt(event.pos())
        # Whether the drop landed squarely on target_item (nest under it) or
        # above/below it (become a sibling) — Qt's own drop indicator already
        # shows the user which of these will happen, so honor it instead of
        # always nesting under whatever item happens to be under the cursor.
        indicator = self.dropIndicatorPosition()

        try:
            moved_places = []
            for dragged_item in dragged_items:
                moved_place = dragged_item.data(0, Qt.UserRole)

                if target_item is None or target_item in dragged_items:
                    # Dropped on empty space or on one of the dragged items — becomes top-level
                    new_parent_id = None
                elif indicator == QAbstractItemView.OnItem:
                    # Dropped directly onto an item — nest under it
                    parent_place = target_item.data(0, Qt.UserRole)
                    new_parent_id = parent_place.place_id
                else:
                    # Dropped above/below an item — become a sibling of that item
                    sibling_parent_item = target_item.parent()
                    if sibling_parent_item is None:
                        new_parent_id = None
                    else:
                        parent_place = sibling_parent_item.data(0, Qt.UserRole)
                        new_parent_id = parent_place.place_id

                moved_places.append((moved_place, new_parent_id))

            # Now let Qt handle the visual repositioning
            super().dropEvent(event)

            # Save the new parents to the database
            for moved_place, new_parent_id in moved_places:
                self.list_view.controller.update.update_entity(
                    "Place", moved_place.place_id, parent_id=new_parent_id
                )
                logger.info(
                    f"Updated parent for {moved_place.place_name} to {new_parent_id}"
                )

            # Reload both views so tree items always reflect the real database state
            if self.list_view.parent_view:
                self.list_view.parent_view.refresh_views()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Failed to update parent: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to update parent place")
            # Refresh to revert visual changes if the DB update failed
            if self.list_view.parent_view:
                self.list_view.parent_view.refresh_views()

    def count_total(self):
        """Count all place items in the tree, regardless of visibility."""
        count = 0
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            count += 1
            iterator += 1
        return count

    def count_visible(self):
        """Count place items currently visible (not hidden by search filter)."""
        count = 0
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            if not iterator.value().isHidden():
                count += 1
            iterator += 1
        return count

    def filter_items(self, search_text):
        """Filter tree items based on search text, matching against place name."""

        def filter_item(item, text):
            text_lower = text.lower()
            place = item.data(0, Qt.UserRole)
            name_lower = (place.place_name or "").lower() if place else ""
            matches = text_lower in name_lower

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


class ListView(QWidget):
    """List view with CRUD operations for places"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.parent_view = None
        self.sort_mode = "alphabetical"
        self.filter_text = ""
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

        self.sort_toggle_button = QPushButton("Sort: A-Z")
        self.sort_toggle_button.setToolTip(
            "Toggle sorting between alphabetical and total association count"
        )
        self.sort_toggle_button.clicked.connect(self.toggle_sort_mode)
        control_layout.addWidget(self.sort_toggle_button)

        # Search bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search places...")
        self.search_bar.textChanged.connect(self.filter_places)

        # Count label — shows "N places" or "Showing X of Y" while filtering
        self.count_label = QLabel()
        self.count_label.setProperty("textRole", "muted")

        # tree widget with proper selection styling
        self.tree_widget = DraggableTreeWidget(self)
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_widget.customContextMenuRequested.connect(self.show_context_menu)
        self.tree_widget.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree_widget.setItemDelegate(HtmlDelegate())
        self.tree_widget.setObjectName("placeList")
        self.tree_widget.setDragEnabled(True)
        self.tree_widget.setAcceptDrops(True)
        self.tree_widget.setDropIndicatorShown(True)
        self.tree_widget.setDragDropMode(QTreeWidget.InternalMove)

        main_layout.addLayout(control_layout)
        main_layout.addWidget(self.search_bar)
        main_layout.addWidget(self.count_label)
        main_layout.addWidget(self.tree_widget)

    def toggle_sort_mode(self):
        """Toggle place list sorting between alphabetical and recursive association count."""
        if self.sort_mode == "alphabetical":
            self.sort_mode = "associations"
            self.sort_toggle_button.setText("Sort: Most Associations")
        else:
            self.sort_mode = "alphabetical"
            self.sort_toggle_button.setText("Sort: A-Z")
        self.load_places()

    def load_places(self):
        """Load places into the tree with hierarchical indentation."""
        self.tree_widget.clear()
        places = self.controller.get.get_all_entities("Place")
        hierarchy = self._build_hierarchy(places)

        # Add places to the tree with expand/collapse capability
        self._add_places_to_tree(hierarchy, None, self.tree_widget.invisibleRootItem())

        # Reapply any active search filter, since the tree was just rebuilt
        self.tree_widget.filter_items(self.filter_text)
        self._update_count_label()

    def filter_places(self, text):
        """Filter places based on search text."""
        self.filter_text = text
        self.tree_widget.filter_items(text)
        self._update_count_label()

    def _update_count_label(self):
        """Refresh the "N places" / "Showing X of Y" count label."""
        total = self.tree_widget.count_total()
        visible = self.tree_widget.count_visible()
        if visible == total:
            self.count_label.setText(f"{total} place{'s' if total != 1 else ''}")
        else:
            self.count_label.setText(f"{visible} of {total} places")

    def show_context_menu(self, position):
        """Show context menu for tree items."""
        item = self.tree_widget.itemAt(position)
        if not item:
            return

        selected_items = self.tree_widget.selectedItems()

        menu = QMenu(self)

        # Only show single-place actions when exactly one item is selected
        if len(selected_items) <= 1:
            # Capture the place into a local variable. Each lambda below uses a
            # default argument (p=place) to lock in this value at menu-creation
            # time, so the action always operates on the right place even if
            # something triggers a re-render before the user clicks.
            place = item.data(0, Qt.UserRole)

            menu.addAction(
                "View Associations",
                lambda p=place: self.show_association_details_for(p),
            )
            menu.addAction(
                "View Details", lambda p=place: self.view_place_details_for(p)
            )
            menu.addAction("Edit", lambda p=place: self.edit_place_for(p))
            menu.addAction("Merge", lambda p=place: self.merge_place(p))
            menu.addAction("Split", lambda p=place: self._split_place())
            menu.addSeparator()
            menu.addAction(
                "New Parent Place", lambda p=place: self.create_new_parent_place(p)
            )
            menu.addAction(
                "New Child Place", lambda p=place: self.create_new_child_place(p)
            )
            menu.addSeparator()

        # Delete works for single or multiple selection
        count = len(selected_items)
        delete_label = f"Delete {count} Places" if count > 1 else "Delete"
        menu.addAction(delete_label, self.delete_selected_places)

        menu.exec_(self.tree_widget.viewport().mapToGlobal(position))

    def _build_hierarchy(self, places):
        """Build a dictionary of parent-child relationships, each level sorted per self.sort_mode."""
        hierarchy = {}
        for place in places:
            parent_id = place.parent_id
            if parent_id not in hierarchy:
                hierarchy[parent_id] = []
            hierarchy[parent_id].append(place)

        for children in hierarchy.values():
            if self.sort_mode == "associations":
                children.sort(
                    key=lambda p: (
                        -p.recursive_association_count,
                        (p.place_name or "").lower(),
                    )
                )
            else:
                children.sort(key=lambda p: (p.place_name or "").lower())

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
        if place.place_type:
            place_type = self._escape_html(place.place_type)
            type_class = "place-type"
        else:
            place_type = "No type"
            type_class = "place-type-missing"

        mb_badge = " \U0001f517" if place.MBID else ""

        base_text = (
            f"<span class='place-name'>{place_name}</span>{mb_badge} "
            f"(<span class='{type_class}'>{place_type}</span>)"
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
        if place.MBID:
            tooltip += "Linked to MusicBrainz\n"
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
            show_status_message(
                self, "Please select a place to view its associations."
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
            except (SQLAlchemyError, RuntimeError) as e:
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
            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"Failed to update place: {str(e)}")
                QMessageBox.critical(self, "Error", "Failed to update place")

    def edit_place(self):
        """Edit the currently selected place."""
        selected = self.tree_widget.currentItem()
        if not selected:
            return
        self.edit_place_for(selected.data(0, Qt.UserRole))

    def create_new_parent_place(self, place):
        """Create a new place and insert it as the parent of the given place.

        The new place takes over the place's old parent slot (preserving
        the grandparent chain), and the place becomes a child of the new
        place.
        """
        dialog = PlaceEditDialog(self.controller, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        new_place_data = dialog.get_place_data()
        if new_place_data is None:
            return

        try:
            new_place = self.controller.add.add_entity("Place", **new_place_data)
            if not new_place:
                raise ValueError("Failed to create new place")

            if new_place_data["parent_id"] is None:
                # User didn't pick a parent for the new place in the
                # dialog, so default to preserving the grandparent chain.
                self.controller.update.update_entity(
                    "Place", new_place.place_id, parent_id=place.parent_id
                )
            self.controller.update.update_entity(
                "Place", place.place_id, parent_id=new_place.place_id
            )
            if self.parent_view:
                self.parent_view.refresh_views()
            logger.info("New parent place created and linked successfully.")
        except (SQLAlchemyError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to create new parent place: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to create new parent place")

    def create_new_child_place(self, place):
        """Create a new place and set it as a child of the given place."""
        dialog = PlaceEditDialog(self.controller, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        new_place_data = dialog.get_place_data()
        if new_place_data is None:
            return

        try:
            new_place = self.controller.add.add_entity("Place", **new_place_data)
            if not new_place:
                raise ValueError("Failed to create new place")

            self.controller.update.update_entity(
                "Place", new_place.place_id, parent_id=place.place_id
            )
            if self.parent_view:
                self.parent_view.refresh_views()
            logger.info("New child place created and linked successfully.")
        except (SQLAlchemyError, ValueError, RuntimeError) as e:
            logger.error(f"Failed to create new child place: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to create new child place")

    def delete_place(self):
        """Delete the currently selected place(s)."""
        self.delete_selected_places()

    def delete_selected_places(self):
        """Delete all currently selected places after a single confirmation.

        Supports single and multi-selection, listing the places to be
        deleted in the confirmation dialog before anything is removed.
        """
        selected_items = self.tree_widget.selectedItems()
        if not selected_items:
            show_status_message(self, "Please select a place to delete.")
            return

        places = [item.data(0, Qt.UserRole) for item in selected_items]
        count = len(places)
        if count == 1:
            message = f"Delete {places[0].place_name} permanently?"
        else:
            names_preview = ", ".join(p.place_name for p in places[:5])
            if count > 5:
                names_preview += f", … (+{count - 5} more)"
            message = f"Delete {count} places permanently?\n\n{names_preview}"

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            message,
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        errors = []
        for place in places:
            try:
                self.controller.delete.delete_entity("Place", place.place_id)
            except SQLAlchemyError as e:
                errors.append(place.place_name)
                logger.error(f"Failed to delete place '{place.place_name}': {str(e)}")

        if self.parent_view:
            self.parent_view.refresh_views()

        if errors:
            QMessageBox.critical(
                self,
                "Error",
                "Could not delete the following places:\n" + "\n".join(errors),
            )
        else:
            logger.info(f"Deleted {count} place(s) successfully")

    def merge_place(self, place):
        """Merge this place into another. Not yet implemented."""
        show_status_message(self, "Merge is not yet available for places.")

    def _split_place(self):
        """Split this place into multiple places. Not yet implemented."""
        show_status_message(self, "Split is not yet available for places.")

    def view_place_details_for(self, place):
        """View detailed information about the given place."""
        dialog = PlaceDetailView(self.controller, place, self)
        dialog.exec_()
