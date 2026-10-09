from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.widgets.hierarchy_tree_style import collect_expanded_ids, restore_expanded_ids
from src.common.widgets.segmented_control import SegmentedControl
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.place.dialogs.place_edit import PlaceEditDialog
from src.place.dialogs.place_merge_dialog import PlaceMergeDialog
from src.place.map.place_map_filter import MultiSelectWidget
from src.place.place_detail_panel import PlaceDetailPanel
from src.place.place_fuzzy_match import CHAIN_THRESHOLD, NAME_THRESHOLD, FuzzyMatchDialog, PlaceFuzzyMatchWorker
from src.place.place_row_delegate import COUNTS_ROLE, TYPE_LABEL_ROLE, WARNING_ROLE, PlaceRowDelegate
from src.place.place_types import NO_TYPE_LABEL, merge_type_selection, order_types_by_hierarchy, type_label


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
                self.list_view.controller.update.update_entity("Place", moved_place.place_id, parent_id=new_parent_id)
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

    def filter_items(self, search_text, selected_types=None, mbid_missing_only=False, coords_missing_only=False, no_parent_only=False, expand_matches=False):
        """Filter tree items based on search text plus optional type/MBID/coordinate/parent criteria.

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
                if type_label(place.place_type) not in selected_types:
                    return False

            if mbid_missing_only and place.MBID:
                return False

            if no_parent_only and place.parent_id is not None:
                return False

            return not (coords_missing_only and place.place_latitude is not None and place.place_longitude is not None)

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
    """List tab: filterable place tree on the left, detail panel on the right."""

    # Extra data role for the place's ID, kept separate from Qt.UserRole
    # (which holds the full Place object) so collect_expanded_ids /
    # restore_expanded_ids have a stable key across reloads — a freshly
    # fetched Place object is not equal to the one it replaces.
    _ID_ROLE = Qt.UserRole + 1

    _VIEW_TREE, _VIEW_FLAT = 0, 1
    _SORT_ALPHA, _SORT_ASSOC = 0, 1

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
        self.no_parent_only = False
        self._places_by_id = {}
        self._loading = False
        self.init_ui()

    def set_parent_view(self, parent_view):
        """Set reference to parent PlaceView for cross-view updates"""
        self.parent_view = parent_view
        self.detail_panel.map_button.setVisible(parent_view is not None)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Row 1: search + view/sort modes
        top_row = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setObjectName("PlaceSearch")
        self.search_bar.setPlaceholderText("Search places…")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.filter_places)
        top_row.addWidget(self.search_bar, 1)

        self.view_mode_control = SegmentedControl(["Tree", "Flat"])
        self.view_mode_control.setItemToolTip(0, "Show places nested under their parents")
        self.view_mode_control.setItemToolTip(1, "Show every place in one flat list")
        self.view_mode_control.currentIndexChanged.connect(self.toggle_flat_view)
        top_row.addWidget(self.view_mode_control)

        self.sort_control = SegmentedControl(["A-Z", "Most used"])
        self.sort_control.setItemToolTip(0, "Sort alphabetically")
        self.sort_control.setItemToolTip(1, "Sort by number of connected items, including child places")
        self.sort_control.currentIndexChanged.connect(self.toggle_sort_mode)
        top_row.addWidget(self.sort_control)
        main_layout.addLayout(top_row)

        # Row 2: always-visible filter chips
        self.filter_container = QWidget()
        self.filter_container.setObjectName("PlaceFilterBar")
        filter_layout = QHBoxLayout(self.filter_container)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)

        self.type_filter_widget = MultiSelectWidget()
        self.type_filter_widget.selection_changed.connect(self.apply_type_filter)
        filter_layout.addWidget(self.type_filter_widget)

        self.coords_missing_checkbox = self._make_chip("No coordinates", "Show only places without latitude/longitude")
        self.coords_missing_checkbox.toggled.connect(self.toggle_coords_missing_filter)
        self.mbid_missing_checkbox = self._make_chip("No MBID", "Show only places not linked to MusicBrainz")
        self.mbid_missing_checkbox.toggled.connect(self.toggle_mbid_missing_filter)
        self.no_parent_checkbox = self._make_chip("No parent", "Show only top-level places (places with no parent)")
        self.no_parent_checkbox.toggled.connect(self.toggle_no_parent_filter)
        for chip in (self.coords_missing_checkbox, self.mbid_missing_checkbox, self.no_parent_checkbox):
            filter_layout.addWidget(chip)
        filter_layout.addStretch()

        self.clear_filters_button = QPushButton("Clear Filters")
        self.clear_filters_button.setProperty("linkButton", True)
        self.clear_filters_button.setToolTip("Reset all filters")
        self.clear_filters_button.clicked.connect(self._clear_filters)
        self.clear_filters_button.hide()
        filter_layout.addWidget(self.clear_filters_button)
        main_layout.addWidget(self.filter_container)

        # Body: tree | detail panel
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        tree_pane = QWidget()
        tree_layout = QVBoxLayout(tree_pane)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(4)

        count_row = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setProperty("textRole", "muted")
        count_row.addWidget(self.count_label)
        count_row.addStretch()
        self.expand_all_button = QPushButton("Expand all")
        self.expand_all_button.setProperty("linkButton", True)
        self.expand_all_button.setToolTip("Open all branches in the tree")
        self.expand_all_button.clicked.connect(self.tree_widget_expand_all)
        self.collapse_all_button = QPushButton("Collapse all")
        self.collapse_all_button.setProperty("linkButton", True)
        self.collapse_all_button.setToolTip("Close all branches in the tree")
        self.collapse_all_button.clicked.connect(self.tree_widget_collapse_all)
        count_row.addWidget(self.expand_all_button)
        count_row.addWidget(self.collapse_all_button)
        tree_layout.addLayout(count_row)

        self.tree_widget = DraggableTreeWidget(self)
        self.tree_widget.setObjectName("PlaceTree")
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setUniformRowHeights(True)
        self.tree_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_widget.customContextMenuRequested.connect(self.show_context_menu)
        self.tree_widget.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.row_delegate = PlaceRowDelegate(self.tree_widget)
        self.tree_widget.setItemDelegate(self.row_delegate)
        self.tree_widget.setDragEnabled(True)
        self.tree_widget.setAcceptDrops(True)
        self.tree_widget.setDropIndicatorShown(True)
        self.tree_widget.setDragDropMode(QTreeWidget.InternalMove)
        self.tree_widget.itemSelectionChanged.connect(self._on_selection_changed)
        tree_layout.addWidget(self.tree_widget, 1)
        splitter.addWidget(tree_pane)

        self.detail_panel = PlaceDetailPanel(self.controller)
        self.detail_panel.ancestor_clicked.connect(self.select_place)
        self.detail_panel.edit_button.clicked.connect(self.edit_place)
        self.detail_panel.map_button.clicked.connect(self._show_current_on_map)
        self.detail_panel.map_button.hide()  # shown once a PlaceView can switch to the map
        self.detail_panel.more_button.clicked.connect(self._show_more_menu)
        self.detail_panel.multi_edit_button.clicked.connect(self.edit_selected_places)
        self.detail_panel.multi_delete_button.clicked.connect(self.delete_selected_places)
        splitter.addWidget(self.detail_panel)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([600, 420])
        main_layout.addWidget(splitter, 1)

    @staticmethod
    def _make_chip(text, tooltip):
        chip = QPushButton(text)
        chip.setCheckable(True)
        chip.setProperty("class", "filterChip")
        chip.setToolTip(tooltip)
        chip.setCursor(Qt.PointingHandCursor)
        return chip

    # ── view / sort modes ─────────────────────────────────────────────────

    def toggle_sort_mode(self, *_args):
        """Apply the sort chosen in the sort control (A-Z or most connected items)."""
        self.sort_mode = "associations" if self.sort_control.currentIndex() == self._SORT_ASSOC else "alphabetical"
        self.load_places()

    def toggle_flat_view(self, *_args):
        """Apply the Tree/Flat choice from the view-mode control."""
        self.flat_view = self.view_mode_control.currentIndex() == self._VIEW_FLAT
        # Drag-and-drop reparenting doesn't make sense against a flat,
        # always-sorted list, and a flat list has no branches to open or close.
        self.tree_widget.setDragEnabled(not self.flat_view)
        self.expand_all_button.setEnabled(not self.flat_view)
        self.collapse_all_button.setEnabled(not self.flat_view)
        self.load_places()

    def tree_widget_expand_all(self):
        """Open every branch in the tree."""
        self.tree_widget.expandAll()

    def tree_widget_collapse_all(self):
        """Close every branch in the tree."""
        self.tree_widget.collapseAll()

    # ── loading ───────────────────────────────────────────────────────────

    def load_places(self, places=None):
        """Load places into the tree.

        Rebuilds the tree from scratch, so the expanded branches, the
        current selection, and the scroll position are saved beforehand
        and restored afterward — otherwise every edit, sort, or filter
        change would jump the user back to a fully collapsed top of list.
        `places` lets a caller that already fetched them skip a query.
        """
        expanded_ids = collect_expanded_ids(self.tree_widget, id_role=self._ID_ROLE)
        current_item = self.tree_widget.currentItem()
        current_place_id = current_item.data(0, self._ID_ROLE) if current_item else None
        scrollbar = self.tree_widget.verticalScrollBar()
        scroll_value = scrollbar.value() if scrollbar else 0

        self._loading = True
        try:
            self.tree_widget.clear()
            if places is None:
                places = self.controller.get.get_all_entities("Place")
            self._places_by_id = {p.place_id: p for p in places}
            self._refresh_type_filter_options(places)
            self._refresh_chip_counts(places)

            if self.flat_view:
                self._add_places_flat(places)
            else:
                hierarchy = self._build_hierarchy(places)
                self._add_places_to_tree(hierarchy, None, self.tree_widget.invisibleRootItem())

            restore_expanded_ids(self.tree_widget, expanded_ids, id_role=self._ID_ROLE)
            self._restore_current_place(current_place_id)

            # Reapply any active search/type/MBID/coordinate filters, since the
            # tree was just rebuilt from scratch.
            self._apply_filters()
        finally:
            self._loading = False

        if scrollbar:
            scrollbar.setValue(scroll_value)
        self._on_selection_changed()

    def _restore_current_place(self, place_id):
        """Re-select the tree item for `place_id`, if it still exists."""
        item = self._find_item(place_id)
        if item is not None:
            self.tree_widget.setCurrentItem(item)

    def _find_item(self, place_id):
        if place_id is None:
            return None
        iterator = QTreeWidgetItemIterator(self.tree_widget)
        while iterator.value():
            item = iterator.value()
            if item.data(0, self._ID_ROLE) == place_id:
                return item
            iterator += 1
        return None

    def _refresh_type_filter_options(self, places):
        """Sync the type filter's options with the types present in the data,
        keeping the user's selection and pre-selecting types that are new."""
        unique_types = {type_label(p.place_type) for p in places}
        if unique_types == self.all_place_types:
            return

        restored = merge_type_selection(self.all_place_types, self.selected_types, unique_types)
        self.all_place_types = unique_types
        self.type_filter_widget.blockSignals(True)
        try:
            self.type_filter_widget.set_items(order_types_by_hierarchy(places, unique_types), default_selected=False)
            self.type_filter_widget.set_selected_items(restored)
        finally:
            self.type_filter_widget.blockSignals(False)
        self.selected_types = set(self.type_filter_widget.get_selected_items())

    def _refresh_chip_counts(self, places):
        """Show how many places each data-gap chip would surface."""
        no_coords = sum(1 for p in places if p.place_latitude is None or p.place_longitude is None)
        no_mbid = sum(1 for p in places if not p.MBID)
        self.coords_missing_checkbox.setText(f"No coordinates · {no_coords}" if no_coords else "No coordinates")
        self.mbid_missing_checkbox.setText(f"No MBID · {no_mbid}" if no_mbid else "No MBID")

    # ── filtering ─────────────────────────────────────────────────────────

    def filter_places(self, text):
        """Filter places based on search text."""
        self.filter_text = text
        self._apply_filters()

    def apply_type_filter(self, selected_types):
        """Handle a change in the selected place types from the type filter dropdown."""
        self.selected_types = set(selected_types)
        self._apply_filters()

    def toggle_mbid_missing_filter(self, checked):
        """Handle toggling the "No MBID" chip."""
        self.mbid_missing_only = checked
        self._apply_filters()

    def toggle_coords_missing_filter(self, checked):
        """Handle toggling the "No coordinates" chip."""
        self.coords_missing_only = checked
        self._apply_filters()

    def toggle_no_parent_filter(self, checked):
        """Handle toggling the "No parent" chip."""
        self.no_parent_only = checked
        self._apply_filters()

    def _clear_filters(self):
        """Reset the search text, type filter, and all chips to their defaults."""
        self.search_bar.clear()
        self.type_filter_widget.select_all()
        self.mbid_missing_checkbox.setChecked(False)
        self.coords_missing_checkbox.setChecked(False)
        self.no_parent_checkbox.setChecked(False)

    def _filters_active(self):
        return bool(self.filter_text or self.mbid_missing_only or self.coords_missing_only or self.no_parent_only or (self.all_place_types and self.selected_types != self.all_place_types))

    def _apply_filters(self):
        """Reapply all active filters (search text, type, MBID missing, coordinates missing, no parent)."""
        filters_active = self._filters_active()
        self.tree_widget.filter_items(self.filter_text, self.selected_types, self.mbid_missing_only, self.coords_missing_only, self.no_parent_only, expand_matches=filters_active)
        self.clear_filters_button.setVisible(filters_active)
        self._update_count_label()

    def _update_count_label(self):
        """Refresh the "N places" / "X of Y places" count label."""
        total = self.tree_widget.count_total()
        visible = self.tree_widget.count_visible()
        if visible == total:
            self.count_label.setText(f"{total} place{'s' if total != 1 else ''}")
        else:
            self.count_label.setText(f"{visible} of {total} places")

    def show_missing_coordinates(self):
        """Turn on the "No coordinates" chip (used by the map's "not on map" link)."""
        self.coords_missing_checkbox.setChecked(True)

    # ── selection / detail panel ──────────────────────────────────────────

    def select_place(self, place_id):
        """Select, reveal, and scroll to the given place. Clears the filters
        first if they currently hide it."""
        item = self._find_item(place_id)
        if item is None:
            return
        if item.isHidden():
            self._clear_filters()
        parent = item.parent()
        while parent:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree_widget.setCurrentItem(item)
        self.tree_widget.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.tree_widget.setFocus()

    def _ancestors_of(self, place):
        chain = []
        seen = {place.place_id}
        parent = self._places_by_id.get(place.parent_id)
        while parent is not None and parent.place_id not in seen:
            seen.add(parent.place_id)
            chain.append(parent)
            parent = self._places_by_id.get(parent.parent_id)
        return list(reversed(chain))

    def _on_selection_changed(self):
        if self._loading:
            return
        selected = self.tree_widget.selectedItems()
        if len(selected) > 1:
            self.detail_panel.show_multi(len(selected))
            return
        item = selected[0] if selected else None
        if item is None:
            self.detail_panel.clear()
            return
        place = item.data(0, Qt.UserRole)
        self.detail_panel.set_place(place, self._ancestors_of(place), item.data(0, COUNTS_ROLE) or (0, 0))

    def _selected_place(self):
        selected = self.tree_widget.selectedItems()
        return selected[0].data(0, Qt.UserRole) if len(selected) == 1 else None

    def _show_current_on_map(self):
        place = self._selected_place()
        if place is not None and self.parent_view is not None:
            self.parent_view.show_place_on_map(place.place_id)

    # ── menus ─────────────────────────────────────────────────────────────

    def _build_place_menu(self, selected_items):
        """Actions for the selected place(s); shared by the context menu and the panel's ⋯ button."""
        menu = QMenu(self)
        count = len(selected_items)
        if count == 1:
            # Capture the place now (p=place) so the action always targets
            # it, even if a reload swaps the tree items before the click.
            place = selected_items[0].data(0, Qt.UserRole)
            menu.addAction("Edit", lambda p=place: self.edit_place_for(p))
            menu.addAction("Merge", lambda p=place: self.merge_place(p))
            menu.addSeparator()
            menu.addAction("New Parent Place", lambda p=place: self.create_new_parent_place(p))
            menu.addAction("New Child Place", lambda p=place: self.create_new_child_place(p))
            menu.addSeparator()
        elif count > 1:
            menu.addAction(f"Edit {count} Places…", self.edit_selected_places)
        if count:
            menu.addAction(f"Delete {count} Places" if count > 1 else "Delete", self.delete_selected_places)
            menu.addSeparator()
        return menu

    def show_context_menu(self, position):
        """Show context menu for tree items."""
        item = self.tree_widget.itemAt(position)
        selected = self.tree_widget.selectedItems()
        if item is None:
            targets = []
        elif item in selected:
            targets = selected
        else:
            # Right-clicking an unselected row acts on that row alone.
            targets = [item]
        menu = self._build_place_menu(targets)
        menu.addAction("🔎 Find Duplicate Places…", self.find_fuzzy_matches)
        menu.exec_(self.tree_widget.viewport().mapToGlobal(position))

    def _show_more_menu(self):
        button = self.detail_panel.more_button
        menu = self._build_place_menu(self.tree_widget.selectedItems())
        menu.addAction("🔎 Find Duplicate Places…", self.find_fuzzy_matches)
        menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))

    # ── tree building ─────────────────────────────────────────────────────

    def _sort_key(self):
        if self.sort_mode == "associations":
            return lambda p: (-p.recursive_association_count, (p.place_name or "").lower())
        return lambda p: (p.place_name or "").lower()

    def _build_hierarchy(self, places):
        """Build a dict of parent-child relationships, each level sorted per self.sort_mode."""
        hierarchy = {}
        for place in places:
            # A parent that isn't in this load (deleted/filtered) would
            # otherwise orphan the subtree; show it at the top level instead.
            parent_id = place.parent_id if place.parent_id in self._places_by_id else None
            hierarchy.setdefault(parent_id, []).append(place)
        key = self._sort_key()
        for children in hierarchy.values():
            children.sort(key=key)
        return hierarchy

    def _add_places_to_tree(self, hierarchy, parent_id, parent_tree_item, seen=None):
        """Recursively add places to the tree widget."""
        seen = seen if seen is not None else set()
        for place in hierarchy.get(parent_id, []):
            if place.place_id in seen:  # guards against a parent_id cycle in the data
                continue
            seen.add(place.place_id)
            item = self._make_item(place)
            parent_tree_item.addChild(item)
            self._add_places_to_tree(hierarchy, place.place_id, item, seen)

    def _add_places_flat(self, places):
        """Add every place as a top-level item, sorted per self.sort_mode, with no nesting."""
        root = self.tree_widget.invisibleRootItem()
        for place in sorted(places, key=self._sort_key()):
            root.addChild(self._make_item(place))

    def _make_item(self, place):
        """Build a tree item with everything the row delegate paints precomputed into roles."""
        direct = place.association_count
        recursive = place.recursive_association_count
        gaps = []
        if place.place_latitude is None or place.place_longitude is None:
            gaps.append("no coordinates")
        if type_label(place.place_type) == NO_TYPE_LABEL:
            gaps.append("no type")

        item = QTreeWidgetItem([place.place_name or ""])
        item.setData(0, Qt.UserRole, place)
        item.setData(0, self._ID_ROLE, place.place_id)
        item.setData(0, TYPE_LABEL_ROLE, type_label(place.place_type))
        item.setData(0, COUNTS_ROLE, (direct, recursive))
        item.setData(0, WARNING_ROLE, ", ".join(gaps))
        item.setToolTip(0, self.create_tooltip(place, direct, recursive, gaps))
        return item

    def create_tooltip(self, place, direct, recursive, gaps):
        """Create detailed tooltip for place."""
        lines = [f"{place.place_name} ({type_label(place.place_type)})"]
        if direct == recursive:
            lines.append(f"{direct} connected item{'s' if direct != 1 else ''}")
        else:
            lines.append(f"{direct} connected directly, {recursive} including child places")
        if gaps:
            lines.append("⚠ Missing: " + ", ".join(gaps))
        return "\n".join(lines)

    # ── actions ───────────────────────────────────────────────────────────

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

    def edit_selected_places(self):
        """Bulk-edit every currently selected place in one dialog.

        Only fields the user actually touches are written, to every
        selected place, in a single batch update.
        """
        selected_items = self.tree_widget.selectedItems()
        if not selected_items:
            return

        places = [item.data(0, Qt.UserRole) for item in selected_items]
        dialog = PlaceEditDialog(self.controller, self, places)
        if dialog.exec_() != QDialog.Accepted:
            return

        changes = dialog.get_bulk_changes()
        if not changes:
            return

        place_ids = [place.place_id for place in places]
        success = self.controller.update.update_entities("Place", place_ids, **changes)
        if success:
            if self.parent_view:
                self.parent_view.refresh_views()
            logger.info(f"Batch-updated {len(places)} place(s), fields: {list(changes.keys())}")
        else:
            logger.error(f"Failed to batch-update {len(places)} place(s)")
            QMessageBox.critical(self, "Error", "Failed to update the selected places")

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
                self.controller.update.update_entity("Place", new_place.place_id, parent_id=place.parent_id)
            self.controller.update.update_entity("Place", place.place_id, parent_id=new_place.place_id)
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

            self.controller.update.update_entity("Place", new_place.place_id, parent_id=place.place_id)
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

        confirm = QMessageBox.question(self, "Confirm Delete", message, QMessageBox.Yes | QMessageBox.No)
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
            QMessageBox.critical(self, "Error", "Could not delete the following places:\n" + "\n".join(errors))
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

    def find_fuzzy_matches(self):
        """Scan every place for likely duplicates and open the review dialog.

        Blocks places by normalised-name prefix/last-token to avoid an
        all-pairs comparison, and requires both a name-similarity match and
        an ancestor-chain-similarity match (see place_fuzzy_match.py) so
        same-named places in different countries/regions aren't flagged.
        The scan runs in a background thread so the UI stays responsive.
        """
        try:
            places = self.controller.get.get_all_entities("Place")
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to load places: {e}")
            return

        if not places:
            show_status_message(self, "No places found in database.")
            return

        progress = QProgressDialog("Scanning for duplicate places…", "Cancel", 0, 1, self)
        progress.setWindowTitle("Duplicate Scan")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        worker = PlaceFuzzyMatchWorker(places, NAME_THRESHOLD, CHAIN_THRESHOLD)

        def _on_progress(current, total):
            progress.setRange(0, total)
            progress.setValue(current)
            progress.setLabelText(f"Scanning for duplicate places… ({current:,} / {total:,})")

        def _on_finished(matches):
            progress.close()
            if not matches:
                show_status_message(self, f"No similar place names found (threshold: {int(NAME_THRESHOLD * 100)}% similarity).")
                return
            dialog = FuzzyMatchDialog(matches, self.controller, self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_places()
                if self.parent_view:
                    self.parent_view.refresh_views()

        def _on_error(msg):
            progress.close()
            QMessageBox.critical(self, "Scan Error", f"Duplicate scan failed:\n{msg}")

        def _on_cancelled():
            worker.request_cancel()

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        progress.canceled.connect(_on_cancelled)

        # Keep a reference so the worker isn't garbage collected
        self._fuzzy_worker = worker
        worker.start()
