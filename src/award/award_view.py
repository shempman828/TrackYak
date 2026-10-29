"""Awards view: filterable award tree with a single detail panel."""

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
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

from src.award.award_common import award_display_text, delete_awards
from src.award.award_detail import AwardDetailTab
from src.award.award_new import AddAwardDialog
from src.common.hierarchy_ops import is_hierarchy_descendant
from src.common.widgets.multi_select_filter_button import MultiSelectFilterButton
from src.foundation.logger_config import logger


class AwardView(QWidget):
    """Main view showing award list and a single detail panel."""

    award_updated = Signal()

    def __init__(self, controller: Any):
        super().__init__()
        self.controller = controller
        self.all_awards: list[Any] = []
        self._suppress_selection = False  # set while a reload or delete rebuilds the tree
        self.flat_view = False
        self._current_detail: AwardDetailTab | None = None
        self.init_ui()
        self.award_updated.connect(self.refresh_award_list)
        self.load_awards()

    def init_ui(self) -> None:
        """Build the layout: search/list on the left, one detail panel on the right."""
        main_layout = QHBoxLayout(self)

        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 10, 0)

        search_group = QGroupBox("Search Awards")
        search_layout = QVBoxLayout(search_group)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search awards by name, category, or year...")
        self.search_box.textChanged.connect(self._filter_awards)
        search_layout.addWidget(self.search_box)

        filter_layout = QHBoxLayout()

        self.year_filter = QComboBox()
        self.year_filter.addItem("All Years")
        self.year_filter.currentTextChanged.connect(self._filter_awards)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All Categories")
        self.category_filter.currentTextChanged.connect(self._filter_awards)

        self.name_filter = MultiSelectFilterButton()
        self.name_filter.selection_changed.connect(self._filter_awards)

        for text, widget in (("&Year:", self.year_filter), ("Ca&tegory:", self.category_filter), ("Award &Name:", self.name_filter)):
            label = QLabel(text)
            label.setBuddy(widget)
            filter_layout.addWidget(label)
            filter_layout.addWidget(widget)

        search_layout.addLayout(filter_layout)
        left_panel.addWidget(search_group)

        list_group = QGroupBox("Awards")
        list_layout = QVBoxLayout(list_group)

        list_header_layout = QHBoxLayout()
        list_header_layout.addStretch()
        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip("Toggle between the hierarchical tree and a flat list")
        self.flat_view_button.clicked.connect(self.toggle_flat_view)
        list_header_layout.addWidget(self.flat_view_button)
        list_layout.addLayout(list_header_layout)

        self.award_tree = QTreeWidget()
        self.award_tree.setMinimumWidth(300)
        self.award_tree.setHeaderLabels(["Award"])
        self.award_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.award_tree.setDragEnabled(True)
        self.award_tree.setAcceptDrops(True)
        self.award_tree.setDropIndicatorShown(True)
        self.award_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.award_tree.itemSelectionChanged.connect(self._on_award_selected)
        self.award_tree.dropEvent = self._award_tree_drop_event
        self.award_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.award_tree.customContextMenuRequested.connect(self._show_context_menu)
        list_layout.addWidget(self.award_tree)

        delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self.award_tree)
        delete_shortcut.activated.connect(self._delete_selected_award)

        left_panel.addWidget(list_group)
        main_layout.addLayout(left_panel)

        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel("Select an award to view details")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setProperty("textRole", "note")
        self.detail_layout.addWidget(self._placeholder)

        main_layout.addWidget(self.detail_container, stretch=1)

    def _show_context_menu(self, position) -> None:
        """Show the Add/Delete context menu for the award tree."""
        menu = QMenu(self)
        add_action = menu.addAction("Add New Award")
        add_action.triggered.connect(self._create_new_award)

        selected_items = self.award_tree.selectedItems()
        if selected_items:
            label = f"Delete {len(selected_items)} Awards" if len(selected_items) > 1 else "Delete Award"
            delete_action = menu.addAction(label)
            delete_action.triggered.connect(self._delete_selected_award)

        menu.exec(self.award_tree.mapToGlobal(position))

    # ─────────────────────────────────────────────── Loading / filtering

    def load_awards(self) -> None:
        """Load awards from the database, refresh the filters and reapply them."""
        awards = self.controller.get.get_all_entities("Award")
        self.all_awards = sorted(awards, key=lambda a: (-(a.award_year or 0), a.award_name.lower()))
        self._update_filters(self.all_awards)
        self._filter_awards()
        logger.info(f"Loaded {len(self.all_awards)} awards")

    def _update_filters(self, awards: list[Any]) -> None:
        """Repopulate the year, category and name filters, keeping current choices."""
        current_year = self.year_filter.currentText()
        current_category = self.category_filter.currentText()

        # Blocked so that clearing the combos does not filter against half-built choices.
        self.year_filter.blockSignals(True)
        self.category_filter.blockSignals(True)
        try:
            self.year_filter.clear()
            self.year_filter.addItem("All Years")
            for year in sorted({a.award_year for a in awards if a.award_year}, reverse=True):
                self.year_filter.addItem(str(year))

            self.category_filter.clear()
            self.category_filter.addItem("All Categories")
            for category in sorted({a.award_category for a in awards if a.award_category}):
                self.category_filter.addItem(category)

            self.name_filter.set_values(sorted({a.award_name for a in awards}))

            self.year_filter.setCurrentIndex(max(self.year_filter.findText(current_year), 0))
            self.category_filter.setCurrentIndex(max(self.category_filter.findText(current_category), 0))
        finally:
            self.year_filter.blockSignals(False)
            self.category_filter.blockSignals(False)

    def toggle_flat_view(self) -> None:
        """Toggle between the nested hierarchy and a flat list."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        # Drag-and-drop reparenting makes no sense against a flat list.
        self.award_tree.setDragEnabled(not self.flat_view)
        self._filter_awards()

    def _populate_award_list(self, awards: list[Any]) -> None:
        """Fill the tree with `awards`, nested by parent unless flat view is on."""
        self._suppress_selection = True
        try:
            self.award_tree.clear()
            if self.flat_view:
                for award in awards:
                    self._make_item(self.award_tree, award)
                return

            # An award whose parent is filtered out is shown as a root, so filtering never hides a match.
            shown_ids = {a.award_id for a in awards}
            children_by_parent: dict[int, list[Any]] = defaultdict(list)
            roots = []
            for award in awards:
                if award.parent_id in shown_ids:
                    children_by_parent[award.parent_id].append(award)
                else:
                    roots.append(award)

            def add_subtree(parent_item, award, seen: set) -> None:
                if award.award_id in seen:  # corrupt cyclic data must not recurse forever
                    return
                seen.add(award.award_id)
                item = self._make_item(parent_item, award)
                for child in children_by_parent.get(award.award_id, ()):
                    add_subtree(item, child, seen)

            seen: set = set()
            for award in roots:
                add_subtree(self.award_tree, award, seen)
            self.award_tree.expandAll()
        finally:
            self._restore_selection()
            self._suppress_selection = False

    @staticmethod
    def _make_item(parent, award: Any) -> QTreeWidgetItem:
        """Create a tree item for `award` under `parent`."""
        item = QTreeWidgetItem(parent, [award_display_text(award)])
        item.setData(0, Qt.UserRole, award.award_id)
        return item

    def _filter_awards(self) -> None:
        """Apply the search text and the filters to the award list."""
        search_text = self.search_box.text().strip().lower()
        year_filter = self.year_filter.currentText()
        category_filter = self.category_filter.currentText()

        filtered = self.all_awards
        if search_text:
            filtered = [
                a for a in filtered if search_text in a.award_name.lower() or (a.award_category and search_text in a.award_category.lower()) or (a.award_year and search_text in str(a.award_year))
            ]
        if year_filter != "All Years":
            filtered = [a for a in filtered if a.award_year and str(a.award_year) == year_filter]
        if category_filter != "All Categories":
            filtered = [a for a in filtered if a.award_category == category_filter]
        # An empty name selection means "all names".
        selected_names = self.name_filter.committed_selection()
        if selected_names:
            filtered = [a for a in filtered if a.award_name in selected_names]

        self._populate_award_list(filtered)

    def _find_item(self, award_id: int) -> QTreeWidgetItem | None:
        """Tree item for `award_id`, or None if it is filtered out."""
        iterator = QTreeWidgetItemIterator(self.award_tree)
        while item := iterator.value():
            if item.data(0, Qt.UserRole) == award_id:
                return item
            iterator += 1
        return None

    def _restore_selection(self) -> None:
        """Reselect the award shown in the detail panel after the tree is rebuilt."""
        if self._current_detail is None:
            return
        item = self._find_item(self._current_detail.award.award_id)
        if item is not None:
            self.award_tree.setCurrentItem(item)

    # ─────────────────────────────────────────────── Detail panel

    def _on_award_selected(self) -> None:
        """Show the single selected award in the detail panel."""
        if self._suppress_selection:
            return
        selected_items = self.award_tree.selectedItems()
        if len(selected_items) != 1:
            return

        award_id = selected_items[0].data(0, Qt.UserRole)
        award = self.controller.get.get_entity_object("Award", award_id=award_id)
        if not award:
            logger.warning(f"No award found with ID: {award_id}")
            return
        self._show_detail(award)

    def _show_detail(self, award: Any) -> None:
        """Replace the detail panel content with a new panel for `award`."""
        self._clear_detail()
        self._placeholder.hide()
        detail = AwardDetailTab(award, self.controller)
        detail.saved.connect(self.refresh_award_list)
        detail.deleted.connect(self._on_detail_deleted)
        self.detail_layout.addWidget(detail)
        self._current_detail = detail

    def _clear_detail(self) -> None:
        """Remove the current detail panel and show the placeholder again."""
        if self._current_detail is not None:
            self.detail_layout.removeWidget(self._current_detail)
            self._current_detail.setParent(None)
            self._current_detail.deleteLater()
            self._current_detail = None
        self._placeholder.show()

    def _on_detail_deleted(self) -> None:
        """Close the panel of a deleted award and reload the list."""
        self._clear_detail()
        self.load_awards()

    def refresh_award_list(self) -> None:
        """Reload the list and rebuild the open detail panel if it has no unsaved changes."""
        self.load_awards()
        if self._current_detail is None or self._current_detail.is_dirty():
            return
        updated = self.controller.get.get_entity_object("Award", award_id=self._current_detail.award.award_id)
        if updated:
            self._show_detail(updated)
        else:
            self._clear_detail()

    def _select_award(self, award_id: int) -> None:
        """Select `award_id` in the tree, or show it directly if the filters hide it."""
        item = self._find_item(award_id)
        if item is not None:
            self.award_tree.setCurrentItem(item)
            self.award_tree.scrollToItem(item)
            return
        award = self.controller.get.get_entity_object("Award", award_id=award_id)
        if award:
            self._show_detail(award)

    # ─────────────────────────────────────────────── Create / move / delete

    def _create_new_award(self) -> None:
        """Open the new-award dialog and select the created award."""
        dialog = AddAwardDialog(self.controller, parent=self)
        if dialog.exec() != QDialog.Accepted or dialog.created_award_id is None:
            return
        self.load_awards()
        self._select_award(dialog.created_award_id)

    @staticmethod
    def _drop_parent_id(target: QTreeWidgetItem | None, indicator) -> int | None:
        """New parent for a drop: the target when dropped on it, else the target's parent."""
        if target is None or indicator == QAbstractItemView.OnViewport:
            return None
        if indicator == QAbstractItemView.OnItem:
            return target.data(0, Qt.UserRole)
        # Above/below an item: the dragged awards become siblings of the target.
        parent = target.parent()
        return parent.data(0, Qt.UserRole) if parent else None

    def _top_selected_ids(self) -> list[int]:
        """IDs of selected awards that have no selected ancestor (those move with their parent)."""
        selected = self.award_tree.selectedItems()
        selected_ids = {item.data(0, Qt.UserRole) for item in selected}
        top_ids = []
        for item in selected:
            ancestor = item.parent()
            while ancestor is not None and ancestor.data(0, Qt.UserRole) not in selected_ids:
                ancestor = ancestor.parent()
            if ancestor is None:
                top_ids.append(item.data(0, Qt.UserRole))
        return top_ids

    def _reparent_awards(self, award_ids: list[int], new_parent_id: int | None) -> bool:
        """Move awards under `new_parent_id`, skipping cycles and no-ops; return True if any moved."""
        parent_by_id = {a.award_id: a.parent_id for a in self.all_awards}
        moved, failed = 0, []
        for award_id in award_ids:
            if award_id == new_parent_id or parent_by_id.get(award_id) == new_parent_id:
                continue
            if new_parent_id is not None and is_hierarchy_descendant(award_id, new_parent_id, self.all_awards, id_attr="award_id"):
                logger.warning(f"Rejected move: award {new_parent_id} is a descendant of {award_id}")
                continue
            if self.controller.update.update_entity("Award", award_id, parent_id=new_parent_id):
                moved += 1
            else:
                failed.append(award_id)

        if failed:
            QMessageBox.warning(self, "Move Failed", f"Could not move {len(failed)} award(s). See the log for details.")
        if moved:
            logger.info(f"Moved {moved} award(s) under parent {new_parent_id}")
            self.refresh_award_list()
        return bool(moved)

    def _award_tree_drop_event(self, event) -> None:
        """Reparent the dragged awards in the database instead of moving tree items."""
        target = self.award_tree.itemAt(event.position().toPoint())
        new_parent_id = self._drop_parent_id(target, self.award_tree.dropIndicatorPosition())
        # The tree is rebuilt from the DB, so Qt's own item move must not run.
        if self._reparent_awards(self._top_selected_ids(), new_parent_id):
            event.accept()
        else:
            event.ignore()

    def _delete_selected_award(self) -> None:
        """Delete all selected awards after confirmation."""
        selected_ids = {item.data(0, Qt.UserRole) for item in self.award_tree.selectedItems()}
        awards_to_delete = [a for a in self.all_awards if a.award_id in selected_ids]
        if not awards_to_delete:
            return

        # Child awards are not deleted: the DB sets their parent_id to NULL.
        has_children = any(a.parent_id in selected_ids for a in self.all_awards if a.award_id not in selected_ids)

        if len(awards_to_delete) == 1:
            award_name = awards_to_delete[0].award_name
            if has_children:
                message = f"'{award_name}' has child awards. Deleting it will make those child awards top-level awards; they will not be deleted.\n\nAre you sure you want to continue?"
                title = "Delete Award with Children"
            else:
                message = f"Are you sure you want to delete '{award_name}'?"
                title = "Delete Award"
        else:
            names = ", ".join(f"'{a.award_name}'" for a in awards_to_delete)
            message = f"Are you sure you want to delete {len(awards_to_delete)} awards: {names}?"
            if has_children:
                message += "\n\nSome of these have child awards, which will become top-level awards rather than being deleted."
            title = "Delete Multiple Awards"

        reply = QMessageBox.question(self, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        if self._current_detail is not None and self._current_detail.award.award_id in selected_ids:
            self._clear_detail()

        ok = delete_awards(self.controller, [a.award_id for a in awards_to_delete])
        self.load_awards()
        if not ok:
            QMessageBox.critical(self, "Error", "Failed to delete the award(s). See the log for details.")
            return
        logger.info(f"Deleted awards: {', '.join(a.award_name for a in awards_to_delete)}")
