from typing import Dict, List

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class OrganizationPreviewDialog(QDialog):
    """Dialog to preview and confirm file organization operations"""

    def __init__(self, parent, auto_ops: List[Dict], confirm_ops: List[Dict]):
        super().__init__(parent)
        self.auto_ops = auto_ops
        self.confirm_ops = confirm_ops
        self.approved_ops = auto_ops.copy()  # Auto-approve the obvious moves
        self._init_ui()
        self.setWindowTitle("Review File Organization")
        self.setMinimumSize(900, 600)

    def _init_ui(self):
        layout = QVBoxLayout()

        # Summary
        summary = QLabel(
            f"Found {len(self.auto_ops)} files to move automatically "
            f"and {len(self.confirm_ops)} files needing confirmation"
        )
        layout.addWidget(summary)

        # Operations list - set larger item height for multi-line display
        self.ops_list = QListWidget()
        self.ops_list.setUniformItemSizes(False)  # Allow variable heights
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

        # Add auto-approved operations (disabled for selection)
        for op in self.auto_ops:
            item = QListWidgetItem(self._format_operation_text(op, "AUTO"))
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)  # Not checkable
            item.setData(Qt.UserRole, op)
            item.setBackground(Qt.lightGray)
            item.setSizeHint(self._calculate_item_size(op))  # Set appropriate height
            self.ops_list.addItem(item)

        # Add operations needing confirmation (checkable)
        for op in self.confirm_ops:
            item = QListWidgetItem(self._format_operation_text(op, "CONFIRM"))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)  # Default to checked
            item.setData(Qt.UserRole, op)
            item.setSizeHint(self._calculate_item_size(op))  # Set appropriate height
            self.ops_list.addItem(item)

    def _calculate_item_size(self, op: Dict) -> QSize:
        """Calculate appropriate size for list item based on content"""
        # Estimate height based on path lengths (you can adjust these values)
        current_path_len = len(str(op["current_path"]))
        expected_path_len = len(str(op["expected_path"]))

        # Base height + extra for each line
        base_height = 60
        extra_height = (
            max(current_path_len, expected_path_len) // 50
        )  # Adjust divisor as needed

        return QSize(400, base_height + (extra_height * 10))

    def _format_operation_text(self, op: Dict, op_type: str) -> str:
        """Format operation for display in list with full paths"""
        similarity = op["similarity_percent"]

        # Show full path changes
        current_path = str(op["current_path"])
        expected_path = str(op["expected_path"])

        text = (
            f"[{op_type}] {similarity}% similar\n"
            f"FROM: {current_path}\n"
            f"TO:   {expected_path}"
        )
        return text

    def _confirm_all(self):
        """Select all confirmable operations"""
        for i in range(self.ops_list.count()):
            item = self.ops_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked)

    def _deselect_all(self):
        """Deselect all confirmable operations"""
        for i in range(self.ops_list.count()):
            item = self.ops_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Unchecked)

    def get_approved_operations(self) -> List[Dict]:
        """Get list of approved operations to execute"""
        approved = self.auto_ops.copy()  # Start with auto-approved

        # Add checked confirmable operations
        for i in range(self.ops_list.count()):
            item = self.ops_list.item(i)
            if (
                item.flags() & Qt.ItemIsUserCheckable
                and item.checkState() == Qt.Checked
            ):
                approved.append(item.data(Qt.UserRole))

        return approved
