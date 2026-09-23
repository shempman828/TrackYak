"""navigation_customization.py — dialog for reordering the nav bar and
hiding entries the user doesn't want. Structurally a copy of
ColumnCustomizationDialog (src/track/track_columns.py): same drag-to-reorder,
checkbox-to-hide QListWidget interaction, applied to nav_tree entries instead
of track columns.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout

from src.foundation.logger_config import logger

# The eagerly-built default landing view -- see GUI._create_views /
# _load_navigation_state in main_window.py. Can never be hidden.
PINNED_VIEW = "Tracks"


class NavigationCustomizationDialog(QDialog):
    """Dialog for customizing navigation bar order and visibility."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Customize Navigation")
        self.setMinimumSize(400, 500)

        self.init_ui()
        self.load_current_state()

    def init_ui(self):
        layout = QVBoxLayout(self)

        instructions = QLabel(f'Drag items to reorder the navigation list. Check/uncheck to show/hide entries. "{PINNED_VIEW}" is always shown.')
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.item_list = QListWidget()
        self.item_list.setDragDropMode(QListWidget.InternalMove)
        self.item_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.item_list)

        controls_layout = QHBoxLayout()

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all)
        controls_layout.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        controls_layout.addWidget(self.deselect_all_btn)

        self.reset_btn = QPushButton("Reset to Default")
        self.reset_btn.clicked.connect(self.reset_to_default)
        controls_layout.addWidget(self.reset_btn)

        layout.addLayout(controls_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        button_box.accepted.connect(self.accept_changes)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Apply).clicked.connect(self.apply_changes)
        layout.addWidget(button_box)

    def _make_item(self, view_name, checked):
        item = QListWidgetItem(view_name)
        item.setData(Qt.UserRole, view_name)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        if view_name == PINNED_VIEW:
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
        return item

    def load_current_state(self):
        """Load current nav order/visibility state into the list."""
        self.item_list.clear()
        all_views = list(self.main_window._view_factories)
        order = list(getattr(self.main_window, "_nav_order", None) or all_views)
        hidden = set(getattr(self.main_window, "_nav_hidden", set()))

        for view_name in order:
            if view_name in all_views:
                self.item_list.addItem(self._make_item(view_name, view_name not in hidden))

        # Add any missing views (shouldn't happen, but just in case)
        for view_name in all_views:
            if view_name not in order:
                self.item_list.addItem(self._make_item(view_name, True))

    def select_all(self):
        """Select all nav entries."""
        for i in range(self.item_list.count()):
            item = self.item_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked)

    def deselect_all(self):
        """Deselect all nav entries (the pinned view stays checked)."""
        for i in range(self.item_list.count()):
            item = self.item_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Unchecked)

    def reset_to_default(self):
        """Reset to default nav order and visibility (all entries shown, factory order)."""
        self.item_list.clear()
        for view_name in self.main_window._view_factories:
            self.item_list.addItem(self._make_item(view_name, True))

    def get_selected_state(self):
        """Get the selected nav item order and hidden set."""
        order = []
        hidden = []

        for i in range(self.item_list.count()):
            item = self.item_list.item(i)
            view_name = item.data(Qt.UserRole)
            order.append(view_name)
            if view_name != PINNED_VIEW and item.checkState() != Qt.Checked:
                hidden.append(view_name)

        return {"order": order, "hidden": hidden}

    def apply_changes(self):
        """Apply changes without closing dialog."""
        state = self.get_selected_state()
        self._apply_state(state)

    def accept_changes(self):
        """Apply changes and close dialog."""
        state = self.get_selected_state()
        self._apply_state(state)
        self.accept()

    def _apply_state(self, state):
        """Apply nav order/visibility state to the main window."""
        try:
            self.main_window.apply_navigation_state(state["order"], state["hidden"])
            logger.info("Navigation customization applied and saved")
        except (RuntimeError, KeyError) as e:
            logger.error(f"Error applying navigation state: {e}")
            QMessageBox.warning(self, "Error", f"Failed to apply navigation changes:\n{e!s}")
