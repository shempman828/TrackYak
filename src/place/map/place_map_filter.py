"""Type filter dropdown shared by the place map and list: a summary button that opens a checkbox popup."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from src.common.widgets.layout_utils import clear_layout
from src.common.widgets.qt_text import esc_amp

# Cap the dropdown's collapsed-button width so it stays compact in a filter
# bar instead of stretching to fill all available horizontal space.
_TOGGLE_BUTTON_MAX_WIDTH = 220


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
        """Report that the popup closed."""
        super().hideEvent(event)
        self.hidden.emit()


class MultiSelectWidget(QWidget):
    """Button that summarizes the selected types and opens a checkbox popup to change them."""

    selection_changed = Signal(list)  # Signal emitted when selection changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checkboxes = {}
        self.selected_items = set()
        self.init_ui()

    def init_ui(self):
        """Build the toggle button and its popup."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toggle_button = QPushButton("Type: All  ▾")
        self.toggle_button.setObjectName("TypeFilterButton")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.toggle_button.setMaximumWidth(_TOGGLE_BUTTON_MAX_WIDTH)
        self.toggle_button.clicked.connect(self._toggle_popup)
        layout.addWidget(self.toggle_button)

        self.popup = _DropdownPopup(self)
        self.popup.select_all_btn.clicked.connect(self.select_all)
        self.popup.select_none_btn.clicked.connect(self.select_none)
        self.popup.hidden.connect(self._on_popup_hidden)

    def _toggle_popup(self):
        """Open or close the popup with the button."""
        if self.toggle_button.isChecked():
            self._show_popup()
        else:
            self.popup.hide()

    def _show_popup(self):
        """Open the popup under the button."""
        pos = self.toggle_button.mapToGlobal(self.toggle_button.rect().bottomLeft())
        self.popup.setFixedWidth(max(self.toggle_button.width(), 220))
        self.popup.move(pos)
        self.popup.show()

    def _on_popup_hidden(self):
        """Release the button when the popup closes."""
        self.toggle_button.setChecked(False)

    def set_items(self, items, default_selected=True):
        """Replace the checkboxes with `items`, in the order given (callers pre-sort them)."""
        clear_layout(self.popup.content_layout)

        self.checkboxes.clear()
        self.selected_items.clear()

        # Add checkboxes for each item
        for item in items:
            checkbox = QCheckBox(esc_amp(item))
            checkbox.setChecked(default_selected)
            checkbox.stateChanged.connect(lambda state, i=item: self.on_checkbox_changed(i, state))
            self.popup.content_layout.addWidget(checkbox)
            self.checkboxes[item] = checkbox

            if default_selected:
                self.selected_items.add(item)

        self._update_button_text()
        # Emit initial selection
        self.selection_changed.emit(sorted(self.selected_items))

    def on_checkbox_changed(self, item, state):
        """Track one checkbox change and report the new selection."""
        if state:  # stateChanged sends an int: 0 is Unchecked, 2 is Checked
            self.selected_items.add(item)
        else:
            self.selected_items.discard(item)

        self._update_button_text()
        self.selection_changed.emit(sorted(self.selected_items))

    def _set_all_checked(self, checked):
        """Check or uncheck every box, then report the selection once."""
        for checkbox in self.checkboxes.values():
            checkbox.blockSignals(True)  # one selection_changed for the batch, not one per box
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self.selected_items = set(self.checkboxes) if checked else set()
        self._update_button_text()
        self.selection_changed.emit(sorted(self.selected_items))

    def select_all(self):
        """Select every item."""
        self._set_all_checked(True)

    def select_none(self):
        """Deselect every item."""
        self._set_all_checked(False)

    def get_selected_items(self):
        """Get list of selected items."""
        return sorted(self.selected_items)

    def set_selected_items(self, items):
        """Select exactly `items`, without emitting selection_changed."""
        for item, checkbox in self.checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(item in items)
            checkbox.blockSignals(False)
        self.selected_items = set(items)
        self._update_button_text()

    def _update_button_text(self):
        """Update the toggle button's label to summarize the current selection."""
        total = len(self.checkboxes)
        selected = len(self.selected_items)
        if total == 0 or selected == total:
            summary = "All"
        elif selected == 0:
            summary = "None"
        elif selected == 1:
            summary = next(iter(self.selected_items))
        else:
            summary = f"{selected} of {total}"
        self.toggle_button.setText(esc_amp(f"Type: {summary}  ▾"))
        # "filtered" lets the theme tint the button like an active filter chip.
        filtered = 0 < total != selected
        if self.toggle_button.property("filtered") != filtered:
            self.toggle_button.setProperty("filtered", filtered)
            self.toggle_button.style().unpolish(self.toggle_button)
            self.toggle_button.style().polish(self.toggle_button)
