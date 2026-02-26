from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class MultiSelectWidget(QWidget):
    """Widget for multi-selection using checkboxes."""

    selection_changed = Signal(list)  # Signal emitted when selection changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checkboxes = {}
        self.selected_items = set()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header with select all/none buttons
        header_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_none_btn = QPushButton("Select None")
        self.select_all_btn.clicked.connect(self.select_all)
        self.select_none_btn.clicked.connect(self.select_none)

        header_layout.addWidget(self.select_all_btn)
        header_layout.addWidget(self.select_none_btn)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Scroll area for checkboxes
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(self.content_widget)
        layout.addWidget(scroll_area)

        # Set maximum height
        self.setMaximumHeight(200)

    def set_items(self, items, default_selected=True):
        """Set the list of items with checkboxes."""
        # Clear existing checkboxes
        for i in reversed(range(self.content_layout.count())):
            widget = self.content_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        self.checkboxes.clear()
        self.selected_items.clear()

        # Add checkboxes for each item
        for item in sorted(items):
            checkbox = QCheckBox(item)
            checkbox.setChecked(default_selected)
            checkbox.stateChanged.connect(
                lambda state, i=item: self.on_checkbox_changed(i, state)
            )
            self.content_layout.addWidget(checkbox)
            self.checkboxes[item] = checkbox

            if default_selected:
                self.selected_items.add(item)

        # Emit initial selection
        self.selection_changed.emit(sorted(self.selected_items))

    def on_checkbox_changed(self, item, state):
        """Handle checkbox state change correctly for PySide6."""
        # In PySide6, state 0 is Unchecked, 2 is Checked.
        # Using 'if state:' captures Checked (2) and avoids enum comparison issues.
        if state:
            self.selected_items.add(item)
        else:
            self.selected_items.discard(item)

        self.selection_changed.emit(sorted(self.selected_items))

    def select_all(self):
        """Select all checkboxes and update the selection set."""
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(True)

        # Crucial: Synchronize the set with all available keys
        self.selected_items = set(self.checkboxes.keys())
        self.selection_changed.emit(sorted(self.selected_items))

    def select_none(self):
        """Deselect all checkboxes efficiently."""
        self.blockSignals(True)
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(False)
        self.blockSignals(False)

        self.selected_items.clear()
        self.selection_changed.emit([])  # Emit once at the end

    def get_selected_items(self):
        """Get list of selected items."""
        return sorted(self.selected_items)

    def set_selected_items(self, items):
        """Set specific items as selected."""
        for item, checkbox in self.checkboxes.items():
            checkbox.setChecked(item in items)
        self.selected_items = set(items)
