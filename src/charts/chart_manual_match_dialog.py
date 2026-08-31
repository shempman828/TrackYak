"""
chart_manual_match_dialog.py

Small modal picker for manually matching a chart entry (or a group of them,
see chart_manual_match_actions.handle_bulk_manual_match_requested) to a
Track or Album. Opened via ChartEntryTable's and ChartRecommendationTable's
right-click context menus (see chart_week_browser_tab.py / chart_search_tab.py
/ chart_recommendations_tab.py for the wiring). Takes raw title/performer/
entity_type fields directly rather than a ChartEntry object, since a
ChartRecommendationTable row (docs/specs/chart_recommendations_manual_match.md)
has no single backing ChartEntry to point at -- it aggregates many. Reuses
build_entity_search_widget (src/common/entity_completer_edit.py) -- the
same search-and-pick completer already used for genre/mood/artist-influence
tagging -- rather than a bespoke picker: Track/Album tables are exactly the
"too large to preload" case that widget's BoundedSearchEdit fallback
already handles. Suggestions carry the shared dimmed secondary context
(artist/album for a Track, artist/year for an Album) via the
entity_completer_context builders, so same-named candidates are
distinguishable in the popup.
"""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from src.common.entity_completer_context import album_context_map, track_context_map
from src.common.entity_completer_edit import build_entity_search_widget

_ENTITY_FIELDS = {"Track": ("track_name", "track_id"), "Album": ("album_name", "album_id")}
_CONTEXT_BUILDERS = {"Track": track_context_map, "Album": album_context_map}


class ChartManualMatchDialog(QDialog):
    """After exec() == QDialog.Accepted, matched_entity_id()/
    matched_entity_type() give the picked entity. OK stays disabled until a
    candidate is actually picked from the completer (matched_id() set),
    not just typed -- an unresolved title string is never an acceptable
    match target."""

    def __init__(
        self, controller, entity_type: str, raw_title: str, raw_performer: str, parent=None
    ):
        super().__init__(parent)
        self._entity_type = entity_type
        name_field, id_field = _ENTITY_FIELDS[self._entity_type]

        self.setWindowTitle(f"Match to {self._entity_type}")
        self.setModal(True)
        # Wide enough that a suggestion's name and its right-aligned dimmed
        # context hint (artist/album, artist/year -- see ContextItemDelegate)
        # both fit in the completer popup without the name being hard-elided
        # down to a few characters. The popup tracks the line edit's width,
        # which tracks the dialog's.
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Chart Title:", QLabel(raw_title))
        form.addRow("Chart Artist:", QLabel(raw_performer))
        layout.addLayout(form)

        self._search = build_entity_search_widget(
            controller,
            self._entity_type,
            name_field,
            id_field,
            f"Search {self._entity_type.lower()}s…",
            context_builder=_CONTEXT_BUILDERS[self._entity_type],
        )
        layout.addWidget(self._search)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        self._search.textChanged.connect(self._update_ok_enabled)
        # textChanged alone misses a pick whose text equals what was already
        # typed (setText() no-ops, emits nothing) -- e.g. typing "Cloud Nine"
        # in full, then picking the one "Cloud Nine" suggestion. picked fires
        # on every pick regardless.
        self._search.picked.connect(self._update_ok_enabled)
        layout.addWidget(buttons)

    def _update_ok_enabled(self, *_args) -> None:
        self._ok_button.setEnabled(self._search.matched_id() is not None)

    def matched_entity_id(self):
        return self._search.matched_id()

    def matched_entity_type(self) -> str:
        return self._entity_type
