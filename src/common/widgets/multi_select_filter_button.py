from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


class _SelectAllCheckBox(QCheckBox):
    """Tristate checkbox that only toggles Checked/Unchecked on click.

    The partial (mixed-selection) state is only ever set programmatically to
    reflect the list below it; a click always means "select all" or
    "deselect all", never "advance to partial".
    """

    def nextCheckState(self) -> None:
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class MultiSelectFilterButton(QToolButton):
    """Searchable, checkable multi-select filter button.

    Behaves like a spreadsheet column filter: click to open a popup, type to
    narrow the list of values, tick the ones wanted, OK to apply. An empty
    `committed_selection()` means "no filter" (nothing checked, or
    everything checked, are both treated as "all").
    """

    selection_changed = Signal()

    def __init__(self, parent=None, all_label: str = "All"):
        super().__init__(parent)
        self._all_label = all_label
        self._values: list[str] = []
        self._committed: set[str] = set()

        self.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._build_popup()
        self._refresh_button_text()

    def _build_popup(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search...")
        self._search_edit.textChanged.connect(self._apply_search_filter)
        layout.addWidget(self._search_edit)

        self._select_all_checkbox = _SelectAllCheckBox("Select All")
        self._select_all_checkbox.setTristate(True)
        self._select_all_checkbox.clicked.connect(self._on_select_all_clicked)
        layout.addWidget(self._select_all_checkbox)

        self._list_widget = QListWidget()
        self._list_widget.itemChanged.connect(self._update_select_all_state)
        layout.addWidget(self._list_widget)

        buttons = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self._commit_and_close)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self._revert_and_close)
        buttons.addWidget(ok_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        action = QWidgetAction(self._menu)
        action.setDefaultWidget(container)
        self._menu.addAction(action)

        self._menu.aboutToShow.connect(self._sync_checkboxes_to_committed)

    def set_values(self, values: list[str]) -> None:
        """Rebuild the checkbox list, keeping the committed selection for
        any values still present and dropping ones that are gone."""
        self._values = sorted(set(values), key=str.lower)
        self._committed = self._normalize({v for v in self._committed if v in self._values})

        self._list_widget.blockSignals(True)
        try:
            self._list_widget.clear()
            self._search_edit.clear()
            for value in self._values:
                item = QListWidgetItem(value)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                checked = not self._committed or value in self._committed
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                self._list_widget.addItem(item)
        finally:
            self._list_widget.blockSignals(False)

        self._update_select_all_state()
        self._refresh_button_text()

    def committed_selection(self) -> set[str]:
        """Empty set means "all" (no filtering should be applied)."""
        return set(self._committed)

    def button_text(self) -> str:
        if not self._committed:
            return self._all_label
        if len(self._committed) == 1:
            return next(iter(self._committed))
        return f"{len(self._committed)} selected"

    def _normalize(self, selected: set[str]) -> set[str]:
        """Nothing checked or everything checked both collapse to "all"."""
        if not selected or selected == set(self._values):
            return set()
        return selected

    def _apply_search_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())
        self._update_select_all_state()

    def _on_select_all_clicked(self, checked: bool) -> None:
        new_state = Qt.Checked if checked else Qt.Unchecked
        self._list_widget.blockSignals(True)
        try:
            for i in range(self._list_widget.count()):
                item = self._list_widget.item(i)
                if not item.isHidden():
                    item.setCheckState(new_state)
        finally:
            self._list_widget.blockSignals(False)
        self._update_select_all_state()

    def _update_select_all_state(self, *_args) -> None:
        visible_states = [
            self._list_widget.item(i).checkState()
            for i in range(self._list_widget.count())
            if not self._list_widget.item(i).isHidden()
        ]
        self._select_all_checkbox.blockSignals(True)
        try:
            if not visible_states or all(s == Qt.Checked for s in visible_states):
                self._select_all_checkbox.setCheckState(
                    Qt.Checked if visible_states else Qt.Unchecked
                )
            elif all(s == Qt.Unchecked for s in visible_states):
                self._select_all_checkbox.setCheckState(Qt.Unchecked)
            else:
                self._select_all_checkbox.setCheckState(Qt.PartiallyChecked)
        finally:
            self._select_all_checkbox.blockSignals(False)

    def _sync_checkboxes_to_committed(self) -> None:
        """Reset the popup to reflect the last-committed selection, e.g.
        after a Cancel or before the very first open."""
        self._search_edit.clear()
        self._list_widget.blockSignals(True)
        try:
            for i in range(self._list_widget.count()):
                item = self._list_widget.item(i)
                item.setHidden(False)
                checked = not self._committed or item.text() in self._committed
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self._list_widget.blockSignals(False)
        self._update_select_all_state()

    def _commit_and_close(self) -> None:
        checked = {
            self._list_widget.item(i).text()
            for i in range(self._list_widget.count())
            if self._list_widget.item(i).checkState() == Qt.Checked
        }
        self._committed = self._normalize(checked)
        self._refresh_button_text()
        self._menu.close()
        self.selection_changed.emit()

    def _revert_and_close(self) -> None:
        self._menu.close()

    def _refresh_button_text(self) -> None:
        self.setText(self.button_text())
