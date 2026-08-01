"""Generic completer widget + find-or-create helper shared by the various
"tag this track with X" editing tabs (genres, moods, places) and similar
entity-picker fields (e.g. artist influences). Extracted from near-identical
completer classes and find-or-create functions that were copy-pasted per
entity type with only names changed.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QStringListModel, Qt
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

    def event(self, e) -> bool:
        # Claim Enter/Return as a ShortcutOverride so a dialog's default
        # button (e.g. Save) doesn't *also* fire from the same keypress --
        # this field's returnPressed is meant to add an item, not submit
        # whatever dialog happens to host it.
        if e.type() == QEvent.ShortcutOverride and e.key() in (
            Qt.Key_Return,
            Qt.Key_Enter,
        ):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        # QLineEdit emits returnPressed() for Enter/Return but deliberately
        # leaves the event ignore()'d afterward (by Qt design, so a dialog's
        # default button still fires from a plain line edit) -- that ignored
        # event is what bubbles up to QDialog's own "Enter clicks the default
        # button" handling. Re-accept it here for the same reason as the
        # ShortcutOverride above: this field handles its own Enter.
        super().keyPressEvent(e)
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            e.accept()

    def set_index(self, display_to_id: dict) -> None:
        """Refresh the completer's backing model.

        Updates the existing QCompleter's model in place rather than
        replacing the QCompleter (setCompleter() deletes the old one, since
        it's parented to this widget) -- set_index() can run synchronously
        from returnPressed while the completer's own popup is still
        mid-dispatch of that same Enter keypress (it goes on to emit
        activated() after returnPressed's slot returns), so destroying it
        here was a use-after-free that crashed the app.
        """
        self._display_to_id = dict(display_to_id)
        sorted_keys = sorted(self._display_to_id.keys())
        existing = self.completer()
        if existing is not None and isinstance(existing.model(), QStringListModel):
            existing.model().setStringList(sorted_keys)
            return
        model = QStringListModel(sorted_keys, self)
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
    extra_lookup: Callable[[], object] | None = None,
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


# ── shared preload cache + size guard ───────────────────────────────────────
#
# A full-table preload (EntityCompleterEdit) is cheap for small tables and
# unpleasant for large ones -- this caps the preload and shares one cache
# per model across every tab/dialog that searches it in a session, instead
# of each one fetching (and re-fetching on every open) its own copy.

_PRELOAD_ROW_CAP = 2000
_entity_cache: dict[str, list | None] = {}


def get_cached_entities(
    controller, model_name: str, *, force_refresh: bool = False
) -> list | None:
    """
    Full row list for `model_name`, cached at module scope. Returns None
    -- instead of a potentially huge list -- once the table exceeds
    _PRELOAD_ROW_CAP rows, signaling callers to fall back to bounded
    on-demand search (BoundedSearchEdit) rather than stalling the UI with
    a full-table preload.

    NB: nothing in the app invalidates this cache when an entity is renamed
    elsewhere in the same session -- only register_cached_entity() (new
    entities) and an explicit invalidate_entity_cache() call refresh it.
    """
    if not force_refresh and model_name in _entity_cache:
        return _entity_cache[model_name]

    count = controller.get.count_entities(model_name)
    if count > _PRELOAD_ROW_CAP:
        _entity_cache[model_name] = None
        return None

    entities = controller.get.get_all_entities(model_name) or []
    _entity_cache[model_name] = entities
    return entities


def register_cached_entity(model_name: str, entity) -> None:
    """Hot-patch a newly created entity into the cached list for
    `model_name`, if one is cached (a no-op once a table has outgrown the
    preload cap and has no cached list to patch)."""
    cached = _entity_cache.get(model_name)
    if cached is not None:
        cached.append(entity)


def invalidate_entity_cache(model_name: str | None = None) -> None:
    """Drop the cached row list for `model_name` (or every cached list when
    omitted), forcing the next get_cached_entities() call to re-fetch."""
    if model_name is None:
        _entity_cache.clear()
    else:
        _entity_cache.pop(model_name, None)


def build_entity_search_widget(
    controller,
    model_name: str,
    name_field: str,
    id_field: str,
    placeholder_text: str = "",
    parent=None,
    index_builder: Callable[[list], dict] | None = None,
):
    """
    Returns an EntityCompleterEdit preloaded with the full `model_name`
    table when it's small enough (get_cached_entities), or a
    BoundedSearchEdit doing on-demand queries when it's not. Either way the
    caller wires up the same textChanged/returnPressed/text()/matched_id()/
    add_to_index()/reset() surface without needing to know which it got.

    `index_builder`, if given, replaces the default plain
    {name_field value: id_field value} dict -- e.g. to disambiguate
    same-named entities (see track_edit_roles.py's _build_artist_index).
    """
    entities = get_cached_entities(controller, model_name)
    if entities is not None:
        if index_builder is not None:
            index = index_builder(entities)
        else:
            index = {
                getattr(e, name_field): getattr(e, id_field)
                for e in entities
                if getattr(e, name_field, None)
            }
        widget = EntityCompleterEdit(placeholder_text, parent)
        widget.set_index(index)
        return widget
    return BoundedSearchEdit(
        controller, model_name, name_field, id_field, placeholder_text, parent
    )


class BoundedSearchEdit(QLineEdit):
    """
    Fallback for EntityCompleterEdit when a table is too large to preload:
    a real popup QCompleter whose backing model is (re)populated from an
    on-demand, capped DB query as the user types (min 2 chars) instead of
    a full-table preload. This gives the same autocomplete-suggestion UX
    as EntityCompleterEdit -- a dropdown of matches appears under the
    field -- just backed by a bounded query instead of an in-memory index.
    Exposes the same text()/matched_id()/add_to_index()/reset()/
    known_matches()/textChanged/returnPressed surface EntityCompleterEdit
    does (textChanged/returnPressed come from QLineEdit itself), so callers
    can treat either interchangeably.
    """

    _MAX_RESULTS = 50
    _MIN_CHARS = 2

    def __init__(
        self,
        controller,
        model_name: str,
        name_field: str,
        id_field: str,
        placeholder_text: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._controller = controller
        self._model_name = model_name
        self._name_field = name_field
        self._id_field = id_field
        self._matched_id = None
        self._last_matches: list = []
        self._display_to_id: dict = {}

        if placeholder_text:
            self.setPlaceholderText(f"{placeholder_text} (min {self._MIN_CHARS} chars)")

        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._on_completion_picked)
        self.setCompleter(completer)

        self.textEdited.connect(self._on_text_edited)

    def _on_text_edited(self, text: str) -> None:
        self._matched_id = None
        text = text.strip()
        if len(text) >= self._MIN_CHARS:
            self._last_matches = (
                self._controller.get.get_all_entities(
                    self._model_name, **{f"{self._name_field}__contains": text}
                )
                or []
            )[: self._MAX_RESULTS]
            self._display_to_id = {
                getattr(e, self._name_field): getattr(e, self._id_field)
                for e in self._last_matches
                if getattr(e, self._name_field, None)
            }
        else:
            self._last_matches = []
            self._display_to_id = {}
        # The model must be refreshed *before* re-triggering completion --
        # QLineEdit.setCompleter() already wires textEdited to update the
        # completer's prefix, but that wiring ran (and filtered the *old*
        # model) before this slot got a chance to load fresh matches, so
        # the popup needs an explicit nudge to show the new results now.
        self._model.setStringList(sorted(self._display_to_id.keys()))
        if self._display_to_id:
            self.completer().complete()

    def _on_completion_picked(self, text: str) -> None:
        self._matched_id = self._display_to_id.get(text)

    def matched_id(self):
        return self._matched_id

    def known_matches(self) -> list:
        """Entities matched by the current text's last on-demand query --
        the bounded-mode equivalent of a preloaded known_entities list, for
        find_or_create_by_name's case-insensitive duplicate check."""
        return self._last_matches

    def add_to_index(self, display: str, entity_id) -> None:
        # Nothing to hot-patch -- the next keystroke re-queries the DB, so a
        # freshly created row is already searchable without a local index.
        pass

    def reset(self) -> None:
        self.clear()
        self._matched_id = None
        self._last_matches = []
        self._display_to_id = {}
        self._model.setStringList([])
