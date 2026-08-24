"""
chart_manual_match_dialog.py

Small modal picker for manually matching one ChartEntry to a Track or
Album, opened via ChartEntryTable's right-click context menu (see
chart_week_browser_tab.py / chart_search_tab.py for the wiring). Reuses
build_entity_search_widget (src/common/entity_completer_edit.py) -- the
same search-and-pick completer already used for genre/mood/artist-influence
tagging -- rather than a bespoke picker: Track/Album tables are exactly the
"too large to preload" case that widget's BoundedSearchEdit fallback
already handles.
"""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from src.common.entity_completer_edit import build_entity_search_widget
from src.db.db_tables.chart import ChartEntry

_ENTITY_FIELDS = {
    "Track": ("track_name", "track_id"),
    "Album": ("album_name", "album_id"),
}


class ChartManualMatchDialog(QDialog):
    """After exec() == QDialog.Accepted, matched_entity_id()/
    matched_entity_type() give the picked entity. OK stays disabled until a
    candidate is actually picked from the completer (matched_id() set),
    not just typed -- an unresolved title string is never an acceptable
    match target."""

    def __init__(self, controller, chart_entry: ChartEntry, parent=None):
        super().__init__(parent)
        self._entity_type = chart_entry.chart.matched_entity_type
        name_field, id_field = _ENTITY_FIELDS[self._entity_type]

        self.setWindowTitle(f"Match to {self._entity_type}")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Chart Title:", QLabel(chart_entry.raw_title))
        form.addRow("Chart Artist:", QLabel(chart_entry.raw_performer))
        layout.addLayout(form)

        self._search = build_entity_search_widget(
            controller,
            self._entity_type,
            name_field,
            id_field,
            f"Search {self._entity_type.lower()}s…",
        )
        layout.addWidget(self._search)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        self._search.textChanged.connect(self._update_ok_enabled)
        layout.addWidget(buttons)

    def _update_ok_enabled(self, _text: str) -> None:
        self._ok_button.setEnabled(self._search.matched_id() is not None)

    def matched_entity_id(self):
        return self._search.matched_id()

    def matched_entity_type(self) -> str:
        return self._entity_type
