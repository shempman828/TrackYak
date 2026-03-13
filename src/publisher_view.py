from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.base_split_dialog import SplitDBDialog
from src.logger_config import logger
from src.publisher_detail import PublisherDetailTab
from src.publisher_merge_dialog import PublisherMergeDialog
from src.publisher_tree import PublisherTreeWidget


class PublisherView(QWidget):
    """Modernized publisher view with hierarchical tree and improved UI."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.publishers_tree = None
        self.detail_tab = None
        self.init_ui()
        self.load_publishers()

    def init_ui(self):
        """Initialize modern UI with splitter and toolbar."""
        self.setWindowTitle("Publishers")
        main_layout = QVBoxLayout(self)

        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)

        # Left panel - Publisher tree
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # Search bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search publishers...")
        self.search_bar.textChanged.connect(self.filter_publishers)
        left_layout.addWidget(self.search_bar)

        # Publisher Tree with context menu
        self.publishers_tree = PublisherTreeWidget(self.controller)
        self.publishers_tree.itemClicked.connect(self.on_publisher_selected)
        self.publishers_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.publishers_tree.customContextMenuRequested.connect(self.show_context_menu)
        left_layout.addWidget(self.publishers_tree)

        # Right panel - Detail view
        self.detail_tab = PublisherDetailTab(self.controller)

        # Add panels to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(self.detail_tab)
        splitter.setSizes([300, 700])

        main_layout.addWidget(splitter)

    def _trigger_rename(self, item):
        """Focus the tree and start inline editing."""
        self.publishers_tree.setFocus()
        self.publishers_tree.editItem(item, 0)

    def show_context_menu(self, position):
        """Show context menu for publisher tree items."""
        item = self.publishers_tree.itemAt(position)
        selected_items = self.publishers_tree.selectedItems()
        menu = QMenu(self)

        if item:
            # Only show single-item actions when exactly one item is selected
            if len(selected_items) == 1:
                rename_action = QAction("Rename Publisher", self)
                rename_action.triggered.connect(lambda: self._trigger_rename(item))
                menu.addAction(rename_action)

                edit_action = QAction("Edit Publisher", self)
                edit_action.triggered.connect(lambda: self.on_publisher_selected(item))
                menu.addAction(edit_action)

                merge_action = QAction("Merge Publisher...", self)
                merge_action.triggered.connect(self.initiate_merge)
                menu.addAction(merge_action)

                split_action = QAction("Split Publisher...", self)
                split_action.triggered.connect(self._split_publisher)
                menu.addAction(split_action)

            # Delete works for single or multiple selection
            count = len(selected_items)
            delete_label = (
                f"Delete {count} Publishers" if count > 1 else "Delete Publisher"
            )
            delete_action = QAction(delete_label, self)
            delete_action.triggered.connect(self._delete_selected_publisher)
            menu.addAction(delete_action)

            menu.addSeparator()

        else:
            # No item selected - global actions
            new_action = QAction("Add New Publisher", self)
            new_action.triggered.connect(self._create_new_publisher)
            menu.addAction(new_action)

            merge_action = QAction("Merge Publishers...", self)
            merge_action.triggered.connect(self.initiate_merge)
            menu.addAction(merge_action)

        menu.exec_(self.publishers_tree.mapToGlobal(position))

    def _create_new_publisher(self):
        """Create a new publisher with default values."""
        try:
            self.controller.add.add_entity("Publisher", publisher_name="New Publisher")
            self.load_publishers()
            logger.info("New publisher created successfully.")
        except Exception as e:
            logger.error(f"Error creating new publisher: {str(e)}")

    def _delete_selected_publisher(self):
        """Delete all currently selected publishers.

        Supports single and multi-selection. The user sees one confirmation
        dialog listing how many publishers will be deleted before anything
        is removed.
        """
        selected_items = self.publishers_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "No Selection", "Please select a publisher to delete."
            )
            return

        count = len(selected_items)
        if count == 1:
            publisher_name = selected_items[0].text(0)
            message = f"Are you sure you want to delete '{publisher_name}'?"
        else:
            names_preview = ", ".join(item.text(0) for item in selected_items[:5])
            if count > 5:
                names_preview += f", … (+{count - 5} more)"
            message = f"Are you sure you want to delete {count} publishers?\n\n{names_preview}"

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            message,
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            errors = []
            for item in selected_items:
                publisher_id = item.data(0, Qt.UserRole)
                try:
                    self.controller.delete.delete_entity("Publisher", publisher_id)
                except Exception as e:
                    errors.append(item.text(0))
                    logger.error(
                        f"Failed to delete publisher '{item.text(0)}': {str(e)}"
                    )

            self.load_publishers()
            self.detail_tab.show_empty_state()

            if errors:
                QMessageBox.warning(
                    self,
                    "Partial Delete",
                    "Could not delete the following publishers:\n" + "\n".join(errors),
                )

    def _split_publisher(self):
        """Split the selected publisher."""
        item = self.publishers_tree.currentItem()
        if not item:
            QMessageBox.warning(
                self, "No Selection", "Please select a publisher to split."
            )
            return

        publisher_id = item.data(0, Qt.UserRole)

        try:
            publisher_obj = self.controller.get.get_entity_object(
                "Publisher", publisher_id=publisher_id
            )
            if not publisher_obj:
                QMessageBox.warning(
                    self, "Not Found", "The selected publisher no longer exists."
                )
                return

            split_dialog = SplitDBDialog(
                self.controller.split,
                "Publisher",
                publisher_obj,
                self,
            )

            if split_dialog.exec_() == QDialog.Accepted:
                self.load_publishers()

        except Exception as e:
            logger.error(f"Error in _split_publisher(): {e}", exc_info=True)

    def _remove_parent(self):
        """Remove parent from selected publisher."""
        item = self.publishers_tree.currentItem()
        if not item:
            return

        publisher_id = item.data(0, Qt.UserRole)

        try:
            self.controller.update.update_entity(
                "Publisher", publisher_id, parent_id=None
            )
            self.load_publishers()
            logger.info("Parent relationship removed successfully.")
        except Exception as e:
            logger.error(f"Error removing parent: {str(e)}")

    def initiate_merge(self):
        """Open the merge dialog, pre-selecting the currently highlighted publisher."""
        publisher_obj = None
        item = self.publishers_tree.currentItem()
        if item:
            publisher_id = item.data(0, Qt.UserRole)
            try:
                publisher_obj = self.controller.get.get_entity_object(
                    "Publisher", publisher_id=publisher_id
                )
            except Exception as e:
                logger.error(f"Error fetching publisher for merge: {str(e)}")

        merge_dialog = PublisherMergeDialog(
            self.controller, self, publisher_obj=publisher_obj
        )
        if merge_dialog.exec_() == QDialog.Accepted:
            self.load_publishers()
            logger.info("Publishers merged successfully.")

    def load_publishers(self):
        """Load publishers into the hierarchical tree."""
        self.publishers_tree.load_publishers()

    def on_publisher_selected(self, item):
        """Handle publisher selection."""
        if item:
            publisher_id = item.data(0, Qt.UserRole)
            self.detail_tab.load_publisher_data(publisher_id)

    def filter_publishers(self, text):
        """Filter publishers based on search text."""
        self.publishers_tree.filter_items(text)
