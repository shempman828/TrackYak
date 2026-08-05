from typing import Any, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.award.award_detail import AwardDetailTab
from src.common.hierarchy_tree_style import is_hierarchy_descendant
from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class AwardView(QWidget):
    """Main view showing award list and detail tabs"""

    award_updated = Signal()

    def __init__(self, controller: Any):
        super().__init__()
        self.controller = controller
        self.all_awards: List[Any] = []  # Safe default in case load_awards() fails
        self._is_deleting = False  # Guard flag to block selection signal during delete
        self.flat_view = False
        self.init_ui()
        self.award_updated.connect(self.refresh_award_list)
        self.load_awards()

    def init_ui(self) -> None:
        """Initialize UI layout with a split view including search and detail tabs."""
        main_layout = QHBoxLayout(self)

        # Left panel with search box and award list
        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 10, 0)

        # Search section
        search_group = QGroupBox("Search Awards")
        search_layout = QVBoxLayout(search_group)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Search awards by name, category, or year..."
        )
        self.search_box.textChanged.connect(self._filter_awards)
        search_layout.addWidget(self.search_box)

        # Filter options
        filter_layout = QHBoxLayout()

        self.year_filter = QComboBox()
        self.year_filter.addItem("All Years")
        self.year_filter.currentTextChanged.connect(self._filter_awards)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All Categories")
        self.category_filter.currentTextChanged.connect(self._filter_awards)

        filter_layout.addWidget(QLabel("Year:"))
        filter_layout.addWidget(self.year_filter)
        filter_layout.addWidget(QLabel("Category:"))
        filter_layout.addWidget(self.category_filter)

        search_layout.addLayout(filter_layout)
        left_panel.addWidget(search_group)

        # Award tree
        list_group = QGroupBox("Awards")
        list_layout = QVBoxLayout(list_group)

        list_header_layout = QHBoxLayout()
        list_header_layout.addStretch()
        self.flat_view_button = QPushButton("Flat View")
        self.flat_view_button.setCheckable(True)
        self.flat_view_button.setChecked(False)
        self.flat_view_button.setToolTip(
            "Toggle between the hierarchical tree and a flat list"
        )
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
        # Enable context menu
        self.award_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.award_tree.customContextMenuRequested.connect(self._show_context_menu)
        list_layout.addWidget(self.award_tree)

        # Delete key shortcut (works when the tree has focus)
        delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self.award_tree)
        delete_shortcut.activated.connect(self._delete_selected_award)

        left_panel.addWidget(list_group)

        main_layout.addLayout(left_panel)

        # Detail tabs panel
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        main_layout.addWidget(self.tab_widget)

    def _show_context_menu(self, position):
        """Show context menu for award tree."""
        menu = QMenu(self)

        # Add actions
        add_action = menu.addAction("Add New Award")
        add_action.triggered.connect(self._create_new_award)

        # Only show delete action if one or more items are selected
        selected_items = self.award_tree.selectedItems()
        if selected_items:
            label = (
                f"Delete {len(selected_items)} Awards"
                if len(selected_items) > 1
                else "Delete Award"
            )
            delete_action = menu.addAction(label)
            delete_action.triggered.connect(self._delete_selected_award)

        # Show the menu at the cursor position
        menu.exec_(self.award_tree.mapToGlobal(position))

    @staticmethod
    def _format_tab_name(award_name: str) -> str:
        """Truncate an award name to fit a tab label."""
        return f"{award_name[:15]}..." if len(award_name) > 15 else award_name

    def load_awards(self) -> None:
        """Load awards from database and update filters."""
        try:
            awards = self.controller.get.get_all_entities("Award")
            # Sort awards by year (descending) then by name
            awards = sorted(
                awards, key=lambda a: (-(a.award_year or 0), a.award_name.lower())
            )

            self.all_awards = awards
            self._update_filters(awards)
            self._populate_award_list(awards)
            logger.info(f"Loaded {len(awards)} awards")

        except (SQLAlchemyError, AttributeError) as e:
            logger.error(f"Error loading awards: {e}")
            QMessageBox.critical(self, "Error", "Failed to load awards")

    def _update_filters(self, awards: List[Any]) -> None:
        """Update year and category filter dropdowns."""
        # Store current selections
        current_year = self.year_filter.currentText()
        current_category = self.category_filter.currentText()

        # Block signals so that clearing/repopulating the dropdowns doesn't
        # trigger _filter_awards mid-update with incomplete data
        self.year_filter.blockSignals(True)
        self.category_filter.blockSignals(True)

        try:
            # Update years
            self.year_filter.clear()
            self.year_filter.addItem("All Years")
            years = sorted(
                set(a.award_year for a in awards if a.award_year), reverse=True
            )
            for year in years:
                self.year_filter.addItem(str(year))

            # Update categories
            self.category_filter.clear()
            self.category_filter.addItem("All Categories")
            categories = sorted(
                set(a.award_category for a in awards if a.award_category)
            )
            for category in categories:
                self.category_filter.addItem(category)

            # Restore selections if possible
            if current_year in [
                self.year_filter.itemText(i) for i in range(self.year_filter.count())
            ]:
                self.year_filter.setCurrentText(current_year)
            if current_category in [
                self.category_filter.itemText(i)
                for i in range(self.category_filter.count())
            ]:
                self.category_filter.setCurrentText(current_category)

        finally:
            # Always re-enable signals, even if something above raised an error
            self.year_filter.blockSignals(False)
            self.category_filter.blockSignals(False)

    def toggle_flat_view(self) -> None:
        """Toggle between the nested hierarchy and a flat list."""
        self.flat_view = self.flat_view_button.isChecked()
        self.flat_view_button.setText("Tree View" if self.flat_view else "Flat View")
        # Drag-and-drop reparenting doesn't make sense against a flat list.
        self.award_tree.setDragEnabled(not self.flat_view)
        self._populate_award_list(self.all_awards)

    @staticmethod
    def _award_display_text(award: Any) -> str:
        """Display text with year and category if available."""
        display_text = award.award_name
        if award.award_year:
            display_text = f"[{award.award_year}] {display_text}"
        if award.award_category:
            display_text = f"{display_text} - {award.award_category}"
        return display_text

    def _populate_award_list(self, awards: List[Any]) -> None:
        """Populate the award tree widget with award data in hierarchical structure."""
        self.award_tree.clear()

        if self.flat_view:
            # `awards` is already sorted (year desc, then name) by load_awards().
            for award in awards:
                item = QTreeWidgetItem(self.award_tree, [self._award_display_text(award)])
                item.setData(0, Qt.UserRole, award.award_id)
            return

        # Create a dictionary to store awards by parent_id
        awards_by_parent = {}
        root_awards = []

        for award in awards:
            if award.parent_id is None:
                root_awards.append(award)
            else:
                if award.parent_id not in awards_by_parent:
                    awards_by_parent[award.parent_id] = []
                awards_by_parent[award.parent_id].append(award)

        # Helper function to recursively build tree
        def add_award_to_tree(parent_item, award):
            item = QTreeWidgetItem(parent_item, [self._award_display_text(award)])
            item.setData(0, Qt.UserRole, award.award_id)

            # Add children if any
            if award.award_id in awards_by_parent:
                for child_award in awards_by_parent[award.award_id]:
                    add_award_to_tree(item, child_award)

            return item

        # Add root awards to tree
        for award in root_awards:
            add_award_to_tree(self.award_tree, award)

        # Expand all items by default
        self.award_tree.expandAll()

    def _filter_awards(self) -> None:
        """Filter awards based on search text and filters."""
        search_text = self.search_box.text().lower()
        year_filter = self.year_filter.currentText()
        category_filter = self.category_filter.currentText()

        filtered = self.all_awards

        # Apply text search
        if search_text:
            filtered = [
                a
                for a in filtered
                if search_text in a.award_name.lower()
                or (a.award_category and search_text in a.award_category.lower())
                or (a.award_year and search_text in str(a.award_year))
            ]

        # Apply year filter
        if year_filter != "All Years":
            filtered = [
                a for a in filtered if a.award_year and str(a.award_year) == year_filter
            ]

        # Apply category filter
        if category_filter != "All Categories":
            filtered = [a for a in filtered if a.award_category == category_filter]

        self._populate_award_list(filtered)

    def _on_award_selected(self) -> None:
        """Handle award selection and load detail tab."""
        # Don't react to selection changes triggered by delete/reload
        if self._is_deleting:
            return

        selected_items = self.award_tree.selectedItems()
        # Only open a detail tab when exactly one item is selected
        if len(selected_items) != 1:
            return

        selected = selected_items[0]
        award_id = selected.data(0, Qt.UserRole)
        try:
            award = self.controller.get.get_entity_object("Award", award_id=award_id)
            if not award:
                logger.warning(f"No award found with ID: {award_id}")
                return

            # Check if tab already exists
            for i in range(self.tab_widget.count()):
                tab = self.tab_widget.widget(i)
                if hasattr(tab, "award") and tab.award.award_id == award_id:
                    self.tab_widget.setCurrentIndex(i)
                    return

            # Create new detail tab
            detail_tab = AwardDetailTab(award, self.controller)
            # Connect the save_requested signal to close the tab
            detail_tab.save_requested.connect(
                lambda: self._close_detail_tab(detail_tab)
            )
            self.tab_widget.addTab(detail_tab, self._format_tab_name(award.award_name))
            self.tab_widget.setCurrentWidget(detail_tab)

            logger.info(f"Opened detail tab for {award.award_name}")

        except (SQLAlchemyError, AttributeError) as e:
            logger.error(f"Error loading award details: {e}")
            QMessageBox.critical(self, "Error", "Failed to load award details")

    def _close_detail_tab(self, tab: "AwardDetailTab") -> None:
        """Close a specific detail tab."""
        index = self.tab_widget.indexOf(tab)
        if index != -1:
            self.tab_widget.removeTab(index)

    def _close_tab(self, index: int) -> None:
        """Close a detail tab."""
        self.tab_widget.removeTab(index)

    def _create_new_award(self) -> None:
        """Create a new award with a proper multi-field dialog."""

        dialog = QDialog(self)
        dialog.setWindowTitle("Create New Award")
        layout = QFormLayout(dialog)

        # Name field
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Required award name")
        layout.addRow("Award Name:", name_edit)

        # Category field
        category_edit = QLineEdit()
        category_edit.setPlaceholderText("Optional category")
        layout.addRow("Category:", category_edit)

        # Year field - plain text so any year can be entered
        year_edit = QLineEdit()
        year_edit.setPlaceholderText("Optional year (e.g. 1623, 2024)")
        layout.addRow("Year:", year_edit)

        # Parent selection
        parent_combo = QComboBox()
        parent_combo.addItem("No parent (root award)", None)

        # Populate with existing awards
        for award in self.all_awards:
            parent_combo.addItem(
                f"{award.award_name} (ID: {award.award_id})", award.award_id
            )

        layout.addRow("Parent Award:", parent_combo)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        name = name_edit.text().strip()
        if not name:
            show_status_message(self, "Award name is required.")
            return

        category = category_edit.text().strip() or None
        year_text = year_edit.text().strip()
        year = None
        if year_text:
            try:
                year = int(year_text)
            except ValueError:
                show_status_message(self, "Year must be a whole number.")
                return
        parent_id = parent_combo.currentData()

        try:
            award_data = {"award_name": name, "parent_id": parent_id}

            if category:
                award_data["award_category"] = category
            if year:
                award_data["award_year"] = year

            self.controller.add.add_entity("Award", **award_data)
            self.load_awards()

            logger.info(f"Created new award: {name}")

        except SQLAlchemyError as e:
            logger.error(f"Error creating new award: {e}")
            QMessageBox.critical(self, "Error", f"Failed to create new award: {e}")

    def refresh_award_list(self) -> None:
        """Refresh the award list and update any open detail tabs."""
        self.load_awards()

        # Update tab names for open detail tabs
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if hasattr(tab, "award"):
                # Reload the award data in the tab
                try:
                    updated_award = self.controller.get.get_entity_object(
                        "Award", award_id=tab.award.award_id
                    )
                    if updated_award:
                        tab.award = updated_award
                        self.tab_widget.setTabText(
                            i, self._format_tab_name(updated_award.award_name)
                        )
                except (SQLAlchemyError, RuntimeError) as e:
                    logger.error(f"Error refreshing tab {i}: {e}")

    def _award_tree_drop_event(self, event):
        """Handle drop events to set parent-child relationships."""
        try:
            # Get the dragged item and drop target
            dragged_item = self.award_tree.currentItem()
            if not dragged_item:
                event.ignore()
                return

            drop_target = self.award_tree.itemAt(event.pos())
            if not drop_target:
                # Dropping on empty space - make it a root award
                new_parent_id = None
            else:
                # Dropping on another award - make it the parent
                new_parent_id = drop_target.data(0, Qt.UserRole)

            dragged_award_id = dragged_item.data(0, Qt.UserRole)

            # Prevent an award from being its own parent
            if new_parent_id == dragged_award_id:
                event.ignore()
                return

            # Prevent circular chains (dropping onto a descendant)
            if new_parent_id is not None and is_hierarchy_descendant(
                dragged_award_id, new_parent_id, self.all_awards, id_attr="award_id"
            ):
                logger.warning(
                    f"Rejected drop: award {new_parent_id} is a descendant of {dragged_award_id}"
                )
                event.ignore()
                return

            # Update the parent_id in the database
            self.controller.update.update_entity(
                "Award", dragged_award_id, parent_id=new_parent_id
            )

            # Accept the drop event
            event.accept()

            # Reload the awards to reflect the new hierarchy
            self.load_awards()

            logger.info(
                f"Updated parent for award {dragged_award_id} to {new_parent_id}"
            )

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error handling drop event: {e}")
            event.ignore()

    def _delete_selected_award(self) -> None:
        """Delete all selected awards after confirmation."""
        selected_items = self.award_tree.selectedItems()
        if not selected_items:
            return

        # Collect the award IDs and objects for all selected items
        selected_ids = [item.data(0, Qt.UserRole) for item in selected_items]
        awards_to_delete = [a for a in self.all_awards if a.award_id in selected_ids]

        if not awards_to_delete:
            return

        # Only the explicitly selected awards are deleted. Any child awards are
        # not deleted -- the DB sets their parent_id to NULL, so they become
        # top-level awards instead of being wiped out along with the parent.
        all_ids_to_delete: List[int] = list(selected_ids)

        # Build confirmation message
        has_children = any(
            a.parent_id in selected_ids
            for a in self.all_awards
            if a.award_id not in selected_ids
        )

        if len(awards_to_delete) == 1:
            award_name = awards_to_delete[0].award_name
            if has_children:
                message = (
                    f"'{award_name}' has child awards. Deleting it will make those "
                    f"child awards top-level awards; they will not be deleted."
                    f"\n\nAre you sure you want to continue?"
                )
                title = "Delete Award with Children"
            else:
                message = f"Are you sure you want to delete '{award_name}'?"
                title = "Delete Award"
        else:
            names = ", ".join(f"'{a.award_name}'" for a in awards_to_delete)
            message = f"Are you sure you want to delete {len(awards_to_delete)} awards: {names}?"
            if has_children:
                message += (
                    "\n\nSome of these have child awards, which will become "
                    "top-level awards rather than being deleted."
                )
            title = "Delete Multiple Awards"

        reply = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Set guard flag so _on_award_selected ignores signals during reload
            self._is_deleting = True

            # Close any open tabs for awards being deleted
            for i in range(self.tab_widget.count() - 1, -1, -1):
                tab = self.tab_widget.widget(i)
                if hasattr(tab, "award") and tab.award.award_id in all_ids_to_delete:
                    self.tab_widget.removeTab(i)

            # Delete each award (delete associations first to avoid FK errors)
            deleted_names = []
            for award_id in all_ids_to_delete:
                try:
                    # Remove any AwardAssociation rows that reference this award
                    associations = self.controller.get.get_all_entities(
                        "AwardAssociation", award_id=award_id
                    )
                    for assoc in associations:
                        self.controller.delete.delete_entity(
                            "AwardAssociation", assoc.association_id
                        )
                except SQLAlchemyError as e:
                    logger.warning(
                        f"Could not remove associations for award {award_id}: {e}"
                    )

                self.controller.delete.delete_entity("Award", award_id)
                award_obj = next(
                    (a for a in self.all_awards if a.award_id == award_id), None
                )
                if award_obj:
                    deleted_names.append(award_obj.award_name)

            logger.info(f"Deleted awards: {', '.join(deleted_names)}")

            # Reload the list now that deletion is done
            self.load_awards()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error deleting award(s): {e}")
            QMessageBox.critical(self, "Error", f"Failed to delete award(s): {e}")
        finally:
            # Always clear the guard flag
            self._is_deleting = False
