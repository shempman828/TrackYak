"""Single-select replacement for a flat `QComboBox` when the choices form a
`parent_id` hierarchy (Genre today; any future entity with the same shape
can reuse this by passing its own `entity_type`/`id_attr`/`name_attr`).

Combines two pieces the codebase already has, in the `QToolButton` + `QMenu`
+ `QWidgetAction` popup shell established by `MultiSelectFilterButton`:

- A search field (`EntityCompleterEdit`) for typing a name, exactly like
  every other "pick one entity by typing" field in the app.
- A cascading submenu tree (`populate_entity_submenu`) for browsing by
  hierarchy, exactly like Role's "Change Parent" context menu.
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolButton, QWidgetAction

from src.common.widgets.entity_completer_edit import EntityCompleterEdit
from src.common.widgets.entity_submenu import populate_entity_submenu


class HierarchyPickerButton(QToolButton):
    """Button that opens a searchable, hierarchy-nested single-select popup."""

    selection_changed = Signal()

    def __init__(self, controller, entity_type: str, id_attr: str, name_attr: str, *, none_label: str = "(No parent)", parent=None):
        super().__init__(parent)
        self._controller = controller
        self._entity_type = entity_type
        self._id_attr = id_attr
        self._name_attr = name_attr
        self._none_label = none_label
        self._entities: list = []
        self._selected_id = None

        self.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._menu.aboutToShow.connect(self._on_about_to_show)

        # Parented to the button (not the menu) so QMenu.clear() -- used to
        # rebuild the hierarchy tree on every set_options() call -- leaves
        # this action, and the search field it holds, alone.
        self._search_edit = EntityCompleterEdit("Search...", self)
        self._search_edit.picked.connect(self._on_completer_picked)
        self._completer_action = QWidgetAction(self)
        self._completer_action.setDefaultWidget(self._search_edit)

        self._refresh_button_text()

    def set_options(self, entities: list) -> None:
        """Rebuild the search index and hierarchy tree from `entities`.

        No DB fetch -- `entities` is the caller's already-filtered valid-parent
        list, so an excluded id can never appear in either the search
        suggestions or the submenu tree.
        """
        self._entities = list(entities)
        index = {getattr(e, self._name_attr): getattr(e, self._id_attr) for e in self._entities}
        self._search_edit.set_index(index)

        self._menu.clear()
        self._menu.addAction(self._completer_action)
        self._menu.addSeparator()

        none_action = QAction(self._none_label, self._menu)
        none_action.triggered.connect(self._on_none_selected)
        self._menu.addAction(none_action)
        self._menu.addSeparator()

        populate_entity_submenu(
            self._menu, controller=self._controller, entity_type=self._entity_type, on_trigger=self._on_tree_selected, entities_override=self._entities, branch_self_label=lambda disp: disp
        )

        if self._selected_id not in self.available_ids():
            self._selected_id = None
        self._refresh_button_text()

    def set_selected_id(self, entity_id) -> None:
        if entity_id not in self.available_ids():
            entity_id = None
        self._selected_id = entity_id
        self._refresh_button_text()

    def selected_id(self):
        return self._selected_id

    def available_ids(self) -> list:
        return [getattr(e, self._id_attr) for e in self._entities]

    def _on_about_to_show(self) -> None:
        self._search_edit.reset()
        self._search_edit.setFocus()

    def _on_completer_picked(self) -> None:
        matched = self._search_edit.matched_id()
        if matched is not None:
            self._select(matched)

    def _on_none_selected(self) -> None:
        self._select(None)

    def _on_tree_selected(self) -> None:
        self._select(self.sender().data())

    def _select(self, entity_id) -> None:
        self._selected_id = entity_id
        self._refresh_button_text()
        self._menu.close()
        self.selection_changed.emit()

    def _refresh_button_text(self) -> None:
        if self._selected_id is None:
            self.setText(self._none_label)
            return
        for e in self._entities:
            if getattr(e, self._id_attr) == self._selected_id:
                self.setText(getattr(e, self._name_attr))
                return
        self.setText(self._none_label)
