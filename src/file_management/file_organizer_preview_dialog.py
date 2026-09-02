from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from src.foundation.logger_config import logger


class OrganizationPreviewDialog(QDialog):
    """Dialog to preview and confirm file organization operations"""

    def __init__(self, parent, operations: list[dict]):
        super().__init__(parent)
        self.operations = operations
        logger.debug(f"Previewing file organization: {len(operations)} operations")
        self._init_ui()
        self.setWindowTitle("Review File Organization")
        self.setMinimumSize(900, 600)

    def _init_ui(self):
        layout = QVBoxLayout()

        # Summary
        summary = QLabel(f"Found {len(self.operations)} files to move")
        layout.addWidget(summary)

        # Operations list - set larger item height for multi-line display
        self.ops_list = QListWidget()
        self.ops_list.setUniformItemSizes(False)  # Allow variable heights
        # Item heights vary a lot (see _calculate_item_size) and the default
        # ScrollPerItem mode jumps a full item -- as tall as 150px+ for a
        # long path -- per wheel notch. ScrollPerPixel fixes the per-item
        # snap, but Qt still derives the scrollbar's singleStep from item
        # height, so pin it to a small fixed step too (same fix as
        # track_edit_roles.py's _RolesTable / mood_autotag_dialog.py's
        # _WordTable) so every notch moves the same modest amount.
        self.ops_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.ops_list.verticalScrollBar().setSingleStep(24)
        self._populate_operations_list()
        layout.addWidget(self.ops_list)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_confirm_all = QPushButton("✓ Confirm All")
        btn_confirm_all.clicked.connect(self._confirm_all)
        btn_deselect_all = QPushButton("✗ Deselect All")
        btn_deselect_all.clicked.connect(self._deselect_all)

        btn_layout.addWidget(btn_confirm_all)
        btn_layout.addWidget(btn_deselect_all)
        btn_layout.addStretch()

        # Dialog buttons
        btn_ok = QPushButton("Execute Organization")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def _populate_operations_list(self):
        """Populate list with organization operations"""
        self.ops_list.clear()

        for op in self.operations:
            item = QListWidgetItem(self._format_operation_text(op))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)  # Default to checked
            item.setData(Qt.UserRole, op)
            item.setSizeHint(self._calculate_item_size(op))  # Set appropriate height
            self.ops_list.addItem(item)

    def _calculate_item_size(self, op: dict) -> QSize:
        """Calculate appropriate size for list item based on content"""
        # Estimate height based on path lengths (you can adjust these values)
        current_path_len = len(str(op["current_path"]))
        expected_path_len = len(str(op["expected_path"]))

        # Base height + extra for each line
        base_height = 60
        extra_height = max(current_path_len, expected_path_len) // 50  # Adjust divisor as needed

        return QSize(400, base_height + (extra_height * 10))

    def _format_operation_text(self, op: dict) -> str:
        """Format operation for display in list with full paths"""
        current_path = str(op["current_path"])
        expected_path = str(op["expected_path"])

        text = f"FROM: {current_path}\nTO:   {expected_path}"
        return text

    def _confirm_all(self):
        """Select all operations"""
        for i in range(self.ops_list.count()):
            item = self.ops_list.item(i)
            item.setCheckState(Qt.Checked)

    def _deselect_all(self):
        """Deselect all operations"""
        for i in range(self.ops_list.count()):
            item = self.ops_list.item(i)
            item.setCheckState(Qt.Unchecked)

    def get_approved_operations(self) -> list[dict]:
        """Get list of approved operations to execute"""
        approved = []

        for i in range(self.ops_list.count()):
            item = self.ops_list.item(i)
            if item.checkState() == Qt.Checked:
                approved.append(item.data(Qt.UserRole))

        logger.info(f"File organization approved: {len(approved)} operations")
        return approved
