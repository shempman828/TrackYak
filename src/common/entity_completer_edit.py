"""Generic completer widget + find-or-create helper shared by the various
"tag this track with X" editing tabs (genres, moods, places) and similar
entity-picker fields (e.g. artist influences). Extracted from near-identical
completer classes and find-or-create functions that were copy-pasted per
entity type with only names changed.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit


class EntityCompleterEdit(QLineEdit):
    """
    QLineEdit with a QCompleter over a caller-supplied {display_text: id}
    index. When the user picks a completion, the matched id is remembered
    directly so add-time can skip the name-lookup roundtrip entirely (and
    can't collide with a same-named entity). Any manual edit after a match
    clears the lock -- typing a new name always means "maybe create new."
    """

    def __init__(self, placeholder_text: str = "", parent=None):
        super().__init__(parent)
        if placeholder_text:
            self.setPlaceholderText(placeholder_text)
        self._display_to_id: dict = {}
        self._matched_id = None
        self.textEdited.connect(self._on_manual_edit)

    def set_index(self, display_to_id: dict) -> None:
        """Rebuild the completer's backing model."""
        self._display_to_id = dict(display_to_id)
        model = QStringListModel(sorted(self._display_to_id.keys()), self)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._on_completion_picked)
        self.setCompleter(completer)

    def add_to_index(self, display: str, entity_id) -> None:
        """Hot-register a newly created entity into the completer index
        without a full re-fetch round-trip -- rebuilds the completer model
        so the new entry is immediately searchable."""
        self._display_to_id[display] = entity_id
        self.set_index(self._display_to_id)

    def _on_completion_picked(self, text: str) -> None:
        self._matched_id = self._display_to_id.get(text)

    def _on_manual_edit(self, _text: str) -> None:
        self._matched_id = None

    def matched_id(self):
        return self._matched_id

    def reset(self) -> None:
        self.clear()
        self._matched_id = None


def find_or_create_by_name(
    controller,
    model_name: str,
    name_field: str,
    name: str,
    known_entities: list,
    *,
    extra_lookup: Optional[Callable[[], object]] = None,
):
    """
    Look up an entity by name (case-insensitive) among `known_entities` --
    an already-loaded list, not a fresh DB query, so an existing entity
    always wins over creating a same-named duplicate regardless of case.
    Creates one only if no match is found there and, if given,
    `extra_lookup` also misses (e.g. Genre's alias-table fallback).
    """
    lowered = name.strip().lower()
    for entity in known_entities:
        if (getattr(entity, name_field, "") or "").strip().lower() == lowered:
            return entity
    if extra_lookup is not None:
        found = extra_lookup()
        if found:
            return found
    return controller.add.add_entity(model_name, **{name_field: name})
