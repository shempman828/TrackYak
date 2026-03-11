from typing import Any, List

from PySide6.QtCore import Qt, Signal
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
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.award_detail import AwardDetailTab
from src.logger_config import logger


class AwardView(QWidget):
    """Main view showing award list and detail tabs"""

    award_updated = Signal()

    def __init__(self, controller: Any):
        super().__init__()
        self.controller = controller
        self.all_awards: List[Any] = []  # Safe default in case load_awards() fails
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

        self.award_tree = QTreeWidget()
        self.award_tree.setMinimumWidth(300)
        self.award_tree.setHeaderLabels(["Award"])
        self.award_tree.setSelectionMode(QAbstractItemView.SingleSelection)
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

        # Only show delete action if an item is selected
        selected_item = self.award_tree.currentItem()
        if selected_item:
            delete_action = menu.addAction("Delete Award")
            delete_action.triggered.connect(self._delete_selected_award)

        # Show the menu at the cursor position
        menu.exec_(self.award_tree.mapToGlobal(position))

    @staticmethod
    def _format_tab_name(award_name: str) -> str:
        """Truncate an award name to fit a tab label."""
        return f"{award_name[:15]}..." if len(award_name) > 15 else award_name

    def _get_all_child_ids(self, parent_id: int) -> List[int]:
        """Recursively collect all descendant award IDs for a given parent."""
        child_ids = []
        for award in self.all_awards:
            if award.parent_id == parent_id:
                child_ids.append(award.award_id)
                child_ids.extend(self._get_all_child_ids(award.award_id))
        return child_ids

    def _is_descendant(self, award_id: int, potential_ancestor_id: int) -> bool:
        """Return True if potential_ancestor_id is an ancestor of award_id."""
        for award in self.all_awards:
            if award.award_id == award_id and award.parent_id is not None:
                if award.parent_id == potential_ancestor_id:
                    return True
                return self._is_descendant(award.parent_id, potential_ancestor_id)
        return False

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

        except Exception as e:
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

    def _populate_award_list(self, awards: List[Any]) -> None:
        """Populate the award tree widget with award data in hierarchical structure."""
        self.award_tree.clear()

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
            # Create display text with year and category if available
            display_text = award.award_name
            if award.award_year:
                display_text = f"[{award.award_year}] {display_text}"
            if award.award_category:
                display_text = f"{display_text} - {award.award_category}"

            item = QTreeWidgetItem(parent_item, [display_text])
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
        selected = self.award_tree.currentItem()
        if not selected:
            return

        award_id = selected.data(0, Qt.UserRole)
        try:
            award = self.controller.get.get_entity_object("Award", award_id=award_id)
            if not award:
                logger.warning(f"No award found with ID: {award_id}")
                QMessageBox.warning(
                    self, "Not Found", f"No award found with ID: {award_id}"
                )
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

        except Exception as e:
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
            QMessageBox.warning(self, "Invalid Input", "Award name is required.")
            return

        category = category_edit.text().strip() or None
        year_text = year_edit.text().strip()
        year = None
        if year_text:
            try:
                year = int(year_text)
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid Input", "Year must be a whole number."
                )
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

        except Exception as e:
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
                except Exception as e:
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
            if new_parent_id is not None and self._is_descendant(
                new_parent_id, dragged_award_id
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

        except Exception as e:
            logger.error(f"Error handling drop event: {e}")
            event.ignore()

    def _delete_selected_award(self) -> None:
        """Delete the currently selected award after confirmation."""
        selected_item = self.award_tree.currentItem()
        if not selected_item:
            return

        award_id = selected_item.data(0, Qt.UserRole)

        # Find the award object to check for children
        award_to_delete = None
        for award in self.all_awards:
            if award.award_id == award_id:
                award_to_delete = award
                break

        if not award_to_delete:
            return

        # Use the real award name, not the formatted display text from the tree
        award_name = award_to_delete.award_name

        # Check if award has children
        has_children = any(a.parent_id == award_id for a in self.all_awards)

        if has_children:
            reply = QMessageBox.warning(
                self,
                "Delete Award with Children",
                f"'{award_name}' has child awards. Deleting it will also delete all its children.\n\nAre you sure you want to continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        else:
            reply = QMessageBox.question(
                self,
                "Delete Award",
                f"Are you sure you want to delete '{award_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

        if reply == QMessageBox.Yes:
            try:
                # Collect IDs for this award and all its descendants
                awards_to_close = [award_id] + self._get_all_child_ids(award_id)

                # Close tabs for the award being deleted and its children
                for i in range(self.tab_widget.count() - 1, -1, -1):
                    tab = self.tab_widget.widget(i)
                    if hasattr(tab, "award") and tab.award.award_id in awards_to_close:
                        self.tab_widget.removeTab(i)

                # Delete the award (controller should handle cascade deletion)
                self.controller.delete.delete_entity("Award", award_id)

                # Refresh the award list
                self.load_awards()

                logger.info(f"Deleted award: {award_name} (ID: {award_id})")

            except Exception as e:
                logger.error(f"Error deleting award: {e}")
                QMessageBox.critical(self, "Error", f"Failed to delete award: {e}")
