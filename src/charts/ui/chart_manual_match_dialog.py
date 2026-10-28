"""Modal picker that matches a chart entry, or a group of them, to a library Track or Album."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from src.common.widgets.entity_completer_context import album_context_map, track_context_map
from src.common.widgets.entity_completer_edit import build_entity_search_widget

_ENTITY_FIELDS = {"Track": ("track_name", "track_id"), "Album": ("album_name", "album_id")}
_CONTEXT_BUILDERS = {"Track": track_context_map, "Album": album_context_map}


class ChartManualMatchDialog(QDialog):
    """Pick a Track or Album for raw chart fields; OK stays disabled until a candidate is picked, not just typed."""

    def __init__(self, controller, entity_type: str, raw_title: str, raw_performer: str, parent=None):
        super().__init__(parent)
        self._entity_type = entity_type
        name_field, id_field = _ENTITY_FIELDS[self._entity_type]

        self.setWindowTitle(f"Match to {self._entity_type}")
        self.setModal(True)
        # Wide enough for a suggestion's name plus its dimmed context hint in the popup.
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        for label_text, value in (("Chart Title:", raw_title), ("Chart Artist:", raw_performer)):
            value_label = QLabel(value)
            value_label.setTextFormat(Qt.PlainText)  # a title with "<...>" must not render as HTML
            form.addRow(label_text, value_label)
        layout.addLayout(form)

        self._search = build_entity_search_widget(controller, self._entity_type, name_field, id_field, f"Search {self._entity_type.lower()}s…", context_builder=_CONTEXT_BUILDERS[self._entity_type])
        layout.addWidget(self._search)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        self._search.textChanged.connect(self._update_ok_enabled)
        # textChanged misses a pick whose text equals what was typed; picked always fires.
        self._search.picked.connect(self._update_ok_enabled)
        layout.addWidget(buttons)

    def _update_ok_enabled(self, *_args) -> None:
        """Enable OK only when a candidate is picked."""
        self._ok_button.setEnabled(self._search.matched_id() is not None)

    def matched_entity_id(self):
        """Return the picked entity's id, or None."""
        return self._search.matched_id()

    def matched_entity_type(self) -> str:
        """Return the entity type this dialog matches to."""
        return self._entity_type
