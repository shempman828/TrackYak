from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger


class _DropdownPopup(QFrame):
    """Frameless popup panel holding the checkbox list and select all/none controls."""

    hidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setFrameShape(QFrame.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_none_btn = QPushButton("Select None")
        header_layout.addWidget(self.select_all_btn)
        header_layout.addWidget(self.select_none_btn)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content_widget = QWidget()
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(content_widget)
        scroll_area.setMinimumHeight(150)
        scroll_area.setMaximumHeight(250)
        layout.addWidget(scroll_area)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hidden.emit()


class MultiSelectWidget(QWidget):
    """Dropdown widget for multi-selection using checkboxes.

    Presents as a single button summarizing the current selection; clicking
    it opens a popup panel with the checkboxes so the control no longer
    permanently occupies space in the layout.
    """

    selection_changed = Signal(list)  # Signal emitted when selection changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checkboxes = {}
        self.selected_items = set()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toggle_button = QPushButton("No items")
        self.toggle_button.setCheckable(True)
        self.toggle_button.clicked.connect(self._toggle_popup)
        layout.addWidget(self.toggle_button)

        self.popup = _DropdownPopup(self)
        self.popup.select_all_btn.clicked.connect(self.select_all)
        self.popup.select_none_btn.clicked.connect(self.select_none)
        self.popup.hidden.connect(self._on_popup_hidden)

    def _toggle_popup(self):
        if self.toggle_button.isChecked():
            self._show_popup()
        else:
            self.popup.hide()

    def _show_popup(self):
        pos = self.toggle_button.mapToGlobal(self.toggle_button.rect().bottomLeft())
        self.popup.setFixedWidth(max(self.toggle_button.width(), 220))
        self.popup.move(pos)
        self.popup.show()

    def _on_popup_hidden(self):
        self.toggle_button.setChecked(False)

    def set_items(self, items, default_selected=True):
        """Set the list of items with checkboxes."""
        # Clear existing checkboxes
        for i in reversed(range(self.popup.content_layout.count())):
            widget = self.popup.content_layout.itemAt(i).widget()
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
            self.popup.content_layout.addWidget(checkbox)
            self.checkboxes[item] = checkbox

            if default_selected:
                self.selected_items.add(item)

        self._update_button_text()
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

        self._update_button_text()
        self.selection_changed.emit(sorted(self.selected_items))

    def select_all(self):
        """Select all checkboxes and update the selection set."""
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(True)

        # Crucial: Synchronize the set with all available keys
        self.selected_items = set(self.checkboxes.keys())
        self._update_button_text()
        self.selection_changed.emit(sorted(self.selected_items))

    def select_none(self):
        """Deselect all checkboxes efficiently."""
        self.blockSignals(True)
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(False)
        self.blockSignals(False)

        self.selected_items.clear()
        self._update_button_text()
        self.selection_changed.emit([])  # Emit once at the end

    def get_selected_items(self):
        """Get list of selected items."""
        return sorted(self.selected_items)

    def set_selected_items(self, items):
        """Set specific items as selected."""
        for item, checkbox in self.checkboxes.items():
            checkbox.setChecked(item in items)
        self.selected_items = set(items)
        self._update_button_text()

    def _update_button_text(self):
        """Update the toggle button's label to summarize the current selection."""
        total = len(self.checkboxes)
        selected = len(self.selected_items)
        if total == 0:
            text = "No items"
        elif selected == total:
            text = f"All types ({total})"
        elif selected == 0:
            text = "No types selected"
        else:
            text = f"{selected} of {total} selected"
        self.toggle_button.setText(text)
