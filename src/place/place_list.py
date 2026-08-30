import html as html_escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
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
from src.place.place_map_filter import MultiSelectWidget
from src.place.place_merge_dialog import PlaceMergeDialog

# Pseudo-type bucket for places with no place_type set, so they can still be
# included/excluded via the type filter instead of always being shown.
_NO_TYPE_LABEL = "No Type"


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
                logger.info(f"Updated parent for {moved_place.place_name} to {new_parent_id}")

            # Reload both views so tree items always reflect the real database state
            if self.list_view.parent_view:
                self.list_view.parent_view.refresh_views()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Failed to update parent: {e!s}")
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

    def filter_items(
        self,
        search_text,
        selected_types=None,
        mbid_missing_only=False,
        coords_missing_only=False,
        expand_matches=False,
    ):
        """Filter tree items based on search text plus optional type/MBID/coordinate criteria.

        An ancestor is kept visible whenever any descendant matches, so the
        path down to a match is never hidden, even if the ancestor itself
        doesn't satisfy the criteria.
        """
        text_lower = search_text.lower()

        def item_matches(place):
            if place is None:
                return False

            name_lower = (place.place_name or "").lower()
            if text_lower not in name_lower:
                return False

            if selected_types is not None:
                place_type = (
                    place.place_type.strip().title()
                    if place.place_type and place.place_type.strip()
                    else _NO_TYPE_LABEL
                )
                if place_type not in selected_types:
                    return False

            if mbid_missing_only and place.MBID:
                return False

            if coords_missing_only and (
                place.place_latitude is not None and place.place_longitude is not None
            ):
                return False

            return True

        def filter_item(item):
            place = item.data(0, Qt.UserRole)
            matches = item_matches(place)

            child_matches = False
            for i in range(item.childCount()):
                if filter_item(item.child(i)):
                    child_matches = True

            should_show = matches or child_matches
            item.setHidden(not should_show)

            if expand_matches and should_show:
                item.setExpanded(True)
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()

            return should_show

        for i in range(self.topLevelItemCount()):
            filter_item(self.topLevelItem(i))


class ListView(QWidget):
    """List view with CRUD operations for places"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.parent_view = None
        self.sort_mode = "alphabetical"
        self.flat_view = False
        self.filter_text = ""
        self.all_place_types = set()
        self.selected_types = set()
        self.mbid_missing_only = False
        self.coords_missing_only = False
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

        self.toggle_filter_button = QPushButton("Show Filters")
        self.toggle_filter_button.setCheckable(True)
        self.toggle_filter_button.setChecked(False)
        self.toggle_filter_button.clicked.connect(self.toggle_filter_visibility)
        control_layout.addWidget(self.toggle_filter_button)

        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip(
            "Toggle between the hierarchical tree and a flat alphabetical list"
        )
        self.flat_view_button.clicked.connect(self.toggle_flat_view)
        control_layout.addWidget(self.flat_view_button)

        # Search bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search places...")
        self.search_bar.textChanged.connect(self.filter_places)

        # Advanced filter bar (type / MBID missing / coordinates missing) —
        # hidden by default, toggled via toggle_filter_button.
        self.filter_container = QWidget()
        self.filter_container.setVisible(False)
        self.filter_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        filter_layout = QHBoxLayout(self.filter_container)
        filter_layout.setContentsMargins(0, 0, 0, 0)

        filter_layout.addWidget(QLabel("Type:"))
        self.type_filter_widget = MultiSelectWidget()
        self.type_filter_widget.selection_changed.connect(self.apply_type_filter)
        filter_layout.addWidget(self.type_filter_widget)

        self.mbid_missing_checkbox = QCheckBox("MBID missing")
        self.mbid_missing_checkbox.toggled.connect(self.toggle_mbid_missing_filter)
        filter_layout.addWidget(self.mbid_missing_checkbox)

        self.coords_missing_checkbox = QCheckBox("Coordinates missing")
        self.coords_missing_checkbox.toggled.connect(self.toggle_coords_missing_filter)
        filter_layout.addWidget(self.coords_missing_checkbox)

        filter_layout.addStretch()

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
        main_layout.addWidget(self.filter_container)
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
        self._refresh_type_filter_options(places)

        if self.flat_view:
            self._add_places_flat(places)
        else:
            hierarchy = self._build_hierarchy(places)
            # Add places to the tree with expand/collapse capability
            self._add_places_to_tree(hierarchy, None, self.tree_widget.invisibleRootItem())

        # Reapply any active search/type/MBID/coordinate filters, since the
        # tree was just rebuilt from scratch.
        self._apply_filters()

    def toggle_flat_view(self):
        """Toggle between the nested hierarchy and a flat alphabetical list."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list.
        self.tree_widget.setDragEnabled(not self.flat_view)
        self.load_places()

    def filter_places(self, text):
        """Filter places based on search text."""
        self.filter_text = text
        self._apply_filters()

    def toggle_filter_visibility(self):
        """Toggle the advanced filter bar (type / MBID missing / coordinates missing)."""
        is_visible = self.filter_container.isVisible()
        self.filter_container.setVisible(not is_visible)
        self.toggle_filter_button.setText("Hide Filters" if not is_visible else "Show Filters")

    def apply_type_filter(self, selected_types):
        """Handle a change in the selected place types from the type filter dropdown."""
        self.selected_types = set(selected_types)
        self._apply_filters()

    def toggle_mbid_missing_filter(self, checked):
        """Handle toggling the "MBID missing" checkbox."""
        self.mbid_missing_only = checked
        self._apply_filters()

    def toggle_coords_missing_filter(self, checked):
        """Handle toggling the "Coordinates missing" checkbox."""
        self.coords_missing_only = checked
        self._apply_filters()

    def _refresh_type_filter_options(self, places):
        """Sync the type filter's checkboxes with the types currently present in the data.

        Preserves the user's existing selection across reloads (limited to
        types that still exist); the first time it's populated, everything
        is selected so the filter starts out as a no-op.
        """
        unique_types = set()
        for place in places:
            if place.place_type and place.place_type.strip():
                unique_types.add(place.place_type.strip().title())
            else:
                unique_types.add(_NO_TYPE_LABEL)

        if unique_types == self.all_place_types:
            return

        is_first_load = not self.all_place_types
        previous_selection = self.selected_types
        self.all_place_types = unique_types

        ordered_types = sorted(unique_types, key=lambda t: (t == _NO_TYPE_LABEL, t))
        self.type_filter_widget.set_items(ordered_types, default_selected=False)

        restored = unique_types if is_first_load else (previous_selection & unique_types)
        self.type_filter_widget.set_selected_items(restored)
        self.selected_types = set(self.type_filter_widget.get_selected_items())

    def _apply_filters(self):
        """Reapply all active filters (search text, type, MBID missing, coordinates missing)."""
        filters_active = bool(
            self.filter_text
            or self.mbid_missing_only
            or self.coords_missing_only
            or (self.all_place_types and self.selected_types != self.all_place_types)
        )
        self.tree_widget.filter_items(
            self.filter_text,
            self.selected_types,
            self.mbid_missing_only,
            self.coords_missing_only,
            expand_matches=filters_active,
        )
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
                "View Associations", lambda p=place: self.show_association_details_for(p)
            )
            menu.addAction("View Details", lambda p=place: self.view_place_details_for(p))
            menu.addAction("Edit", lambda p=place: self.edit_place_for(p))
            menu.addAction("Merge", lambda p=place: self.merge_place(p))
            menu.addAction("Split", lambda p=place: self._split_place())
            menu.addSeparator()
            menu.addAction("New Parent Place", lambda p=place: self.create_new_parent_place(p))
            menu.addAction("New Child Place", lambda p=place: self.create_new_child_place(p))
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
                    key=lambda p: (-p.recursive_association_count, (p.place_name or "").lower())
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

    def _add_places_flat(self, places):
        """Add every place as a top-level item, sorted per self.sort_mode, with no nesting."""
        if self.sort_mode == "associations":
            ordered = sorted(
                places, key=lambda p: (-p.recursive_association_count, (p.place_name or "").lower())
            )
        else:
            ordered = sorted(places, key=lambda p: (p.place_name or "").lower())

        root = self.tree_widget.invisibleRootItem()
        for place in ordered:
            item_text = self.format_list_item(place)
            item = QTreeWidgetItem([item_text])
            item.setData(0, Qt.UserRole, place)
            item.setToolTip(0, self.create_tooltip(place))
            root.addChild(item)

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
            assoc_text = f"<span class='assoc-count'> - {direct} direct, {recursive} total</span>"
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
                logger.error(f"Failed to create place: {e!s}")
                QMessageBox.critical(self, "Error", "Failed to create place")

    def edit_place_for(self, old_place):
        """Edit the given place and refresh both views."""
        dialog = PlaceEditDialog(self.controller, self, old_place)
        if dialog.exec_() == QDialog.Accepted:
            updated_data = dialog.get_place_data()
            try:
                self.controller.update.update_entity("Place", old_place.place_id, **updated_data)
                if self.parent_view:
                    self.parent_view.refresh_views()
                logger.info("Place updated successfully")
            except (SQLAlchemyError, RuntimeError) as e:
                logger.error(f"Failed to update place: {e!s}")
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
            logger.error(f"Failed to create new parent place: {e!s}")
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
            logger.error(f"Failed to create new child place: {e!s}")
            QMessageBox.critical(self, "Error", "Failed to create new child place")

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
            self, "Confirm Delete", message, QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        errors = []
        for place in places:
            try:
                self.controller.delete.delete_entity("Place", place.place_id)
            except SQLAlchemyError as e:
                errors.append(place.place_name)
                logger.error(f"Failed to delete place '{place.place_name}': {e!s}")

        if self.parent_view:
            self.parent_view.refresh_views()

        if errors:
            QMessageBox.critical(
                self, "Error", "Could not delete the following places:\n" + "\n".join(errors)
            )
        else:
            logger.info(f"Deleted {count} place(s) successfully")

    def merge_place(self, place):
        """Open the merge dialog, pre-populated with the given place as source."""
        merge_dialog = PlaceMergeDialog(self.controller, self, place_obj=place)
        if merge_dialog.exec_() == QDialog.Accepted:
            self.load_places()
            if self.parent_view:
                self.parent_view.refresh_views()
            logger.info("Places merged successfully.")

    def _split_place(self):
        """Split this place into multiple places. Not yet implemented."""
        show_status_message(self, "Split is not yet available for places.")

    def view_place_details_for(self, place):
        """View detailed information about the given place."""
        dialog = PlaceDetailView(self.controller, place, self)
        dialog.exec_()
