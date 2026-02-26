from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.logger_config import logger


class ColumnCustomizationDialog(QDialog):
    """Dialog for customizing column visibility and order."""

    def __init__(self, track_view, parent=None):
        super().__init__(parent)
        self.track_view = track_view
        self.setWindowTitle("Customize Columns")
        self.setMinimumSize(500, 600)

        self.init_ui()
        self.load_current_state()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Instructions
        instructions = QLabel(
            "Drag items to reorder columns. Check/uncheck to show/hide columns."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Create list widget for columns
        self.column_list = QListWidget()
        self.column_list.setDragDropMode(QListWidget.InternalMove)
        self.column_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.column_list)

        # Control buttons
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

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        button_box.accepted.connect(self.accept_changes)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Apply).clicked.connect(self.apply_changes)
        layout.addWidget(button_box)

    def load_current_state(self):
        """Load current column state into the list."""
        self.column_list.clear()
        state = self.track_view.get_column_state()

        # Get all available columns
        all_columns = list(self.track_view.columns.keys())

        # Add columns in current visual order
        for field_name in state["order"]:
            if field_name in self.track_view.columns:
                display_name = self.track_view.columns[field_name]
                item = QListWidgetItem(display_name)
                item.setData(Qt.UserRole, field_name)
                item.setCheckState(
                    Qt.Checked if field_name in state["visible"] else Qt.Unchecked
                )
                self.column_list.addItem(item)

        # Add any missing columns (shouldn't happen, but just in case)
        for field_name in all_columns:
            if field_name not in state["order"]:
                display_name = self.track_view.columns[field_name]
                item = QListWidgetItem(display_name)
                item.setData(Qt.UserRole, field_name)
                item.setCheckState(Qt.Checked)
                self.column_list.addItem(item)

    def select_all(self):
        """Select all columns."""
        for i in range(self.column_list.count()):
            item = self.column_list.item(i)
            item.setCheckState(Qt.Checked)

    def deselect_all(self):
        """Deselect all columns."""
        for i in range(self.column_list.count()):
            item = self.column_list.item(i)
            item.setCheckState(Qt.Unchecked)

    def reset_to_default(self):
        """Reset to default column visibility and order."""
        # Default visible columns (common fields)
        default_visible = [
            "track_file_name",
            "artist_name",
            "album_name",
            "title",
            "genre",
            "duration",
            "year",
        ]

        # Default order (alphabetical by display name)
        all_columns = list(self.track_view.columns.keys())
        default_order = sorted(all_columns, key=lambda x: self.track_view.columns[x])

        # Clear and reload with defaults
        self.column_list.clear()
        for field_name in default_order:
            display_name = self.track_view.columns[field_name]
            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, field_name)
            item.setCheckState(
                Qt.Checked if field_name in default_visible else Qt.Unchecked
            )
            self.column_list.addItem(item)

    def get_selected_state(self):
        """Get the selected column order and visibility."""
        visible_columns = []
        column_order = []

        for i in range(self.column_list.count()):
            item = self.column_list.item(i)
            field_name = item.data(Qt.UserRole)
            column_order.append(field_name)
            if item.checkState() == Qt.Checked:
                visible_columns.append(field_name)

        return {"visible": visible_columns, "order": column_order}

    def apply_changes(self):
        """Apply changes without closing dialog."""
        state = self.get_selected_state()
        self.apply_column_state(state)

    def accept_changes(self):
        """Apply changes and close dialog."""
        state = self.get_selected_state()
        self.apply_column_state(state)
        self.accept()

    def apply_column_state(self, state):
        """Apply column state to track view."""
        try:
            # Reorder columns
            self.track_view._reorder_columns(state["order"])

            # Set visibility
            all_columns = list(self.track_view.columns.keys())
            for i, field_name in enumerate(all_columns):
                self.track_view.table.setColumnHidden(
                    i, field_name not in state["visible"]
                )

            # Save to config
            self.track_view.save_column_state()

            logger.info("Column customization applied and saved")

        except Exception as e:
            logger.error(f"Error applying column state: {e}")
            QMessageBox.warning(
                self, "Error", f"Failed to apply column changes:\n{str(e)}"
            )
