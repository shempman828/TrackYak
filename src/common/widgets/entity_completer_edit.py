"""Generic entity-picker completer widgets and the find-or-create helper they feed."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QStringListModel, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QCompleter, QLineEdit, QStyle, QStyledItemDelegate, QStyleOptionViewItem
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


def _rank_by_query(text: str, keys) -> list[str]:
    """Order names so prefix matches outrank mid-string ones, then case-insensitively."""
    # QCompleter's MatchContains keeps the model's row order, so without this
    # "Additional Guitar" would sort ahead of "Guitar" for the query "guitar".
    lowered = text.strip().lower()
    return sorted(keys, key=lambda key: (0 if key.lower().startswith(lowered) else 1, key.lower(), key))


# Several entities can be typed into one field at once, e.g. "Bass;Lead Vocals".
_MULTI_ENTITY_DELIMITER = ";"

_ENTER_KEYS = (Qt.Key_Return, Qt.Key_Enter)


def _create_new_label(segment: str) -> str:
    """Text of the opt-in synthetic "Create new <segment>" popup row."""
    # Used by album fields, where same-name duplicates (reissues, split
    # releases) are legitimate; off everywhere else.
    return f'Create new "{segment.strip()}"'


def _apply_highlighted_completion(completer, on_pick: Callable[[str], None]) -> bool:
    """Apply the popup's highlighted suggestion via `on_pick`; True if one was applied."""
    # The completer is wired via setWidget(), not setCompleter(), so Qt does no
    # Return-vs-completer coordination: without this, returnPressed() fires with
    # the partial text before activated() swaps the suggestion in.
    if completer is None:
        return False
    popup = completer.popup()
    if popup is None or not popup.isVisible():
        return False
    index = popup.currentIndex()
    if not index.isValid():
        return False
    on_pick(index.data())
    return True


def split_entity_names(text: str) -> list[str]:
    """Split a field's text on the multi-entity delimiter, trimming and dropping empty parts."""
    return [part.strip() for part in text.split(_MULTI_ENTITY_DELIMITER) if part.strip()]


def _current_segment(text: str) -> str:
    """The part of `text` after its last delimiter -- what the user is typing now."""
    idx = text.rfind(_MULTI_ENTITY_DELIMITER)
    return text[idx + 1 :].lstrip() if idx != -1 else text.lstrip()


class ContextItemDelegate(QStyledItemDelegate):
    """Paints a suggestion as its name plus a right-aligned, dimmed context string."""

    # The context comes from context_getter(name), not the model, so the model
    # value fed back into the line edit stays the bare name.

    _PAD = 6

    def __init__(self, context_getter: Callable[[str], str], parent=None):
        super().__init__(parent)
        self._context_getter = context_getter

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = opt.text
        context = self._context_getter(name) or ""
        if not context:
            super().paint(painter, option, index)
            return

        # Let the style paint the row chrome (selection/hover background),
        # then draw both text runs ourselves so the context gets its own pen.
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        painter.save()
        rect = opt.rect.adjusted(self._PAD, 0, -self._PAD, 0)
        fm = painter.fontMetrics()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        active_role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text

        painter.setPen(opt.palette.color(QPalette.ColorGroup.Disabled, active_role))
        painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, context)
        rect.setRight(rect.right() - fm.horizontalAdvance(context) - self._PAD * 2)

        painter.setPen(opt.palette.color(active_role))
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(name, Qt.ElideRight, rect.width()))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(size.height() + 4)
        return size


class _CompleterPickMixin:
    """Pick/Enter/"Create new" behaviour shared by EntityCompleterEdit and BoundedSearchEdit."""

    # Hosts set: _completer, _display_to_id, _allow_create_new, and a `picked` Signal.
    _completer: QCompleter | None

    def _init_pick_state(self, allow_create_new: bool) -> None:
        self._display_to_id: dict = {}
        self._display_to_context: dict = {}
        self._matched_id = None
        self._allow_create_new = allow_create_new
        self._create_new_row: str | None = None  # exact synthetic string now in the model
        self._create_new_requested = False  # set once the user picks it

    def event(self, e) -> bool:
        # Claim Enter as a ShortcutOverride so the host dialog's default button
        # does not also fire: this field's Enter adds an item, not submits.
        if e.type() == QEvent.ShortcutOverride and e.key() in _ENTER_KEYS:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in _ENTER_KEYS and _apply_highlighted_completion(self._completer, self._on_completion_picked):
            e.accept()
            self.returnPressed.emit()
            return
        super().keyPressEvent(e)
        # QLineEdit leaves Enter ignored so it bubbles to QDialog's default
        # button; re-accept it, for the same reason as the ShortcutOverride.
        if e.key() in _ENTER_KEYS:
            e.accept()

    def _rows_with_create_new(self, segment: str) -> list[str]:
        """Ranked completion rows for `segment`, plus the "Create new" row when enabled."""
        rows = _rank_by_query(segment, self._display_to_id.keys())
        # Offered even on an exact name hit -- that is the case it exists for.
        if self._allow_create_new and segment.strip():
            self._create_new_row = _create_new_label(segment)
            rows.append(self._create_new_row)
        else:
            self._create_new_row = None
        return rows

    def _on_completion_picked(self, candidate: str) -> None:
        # Replace only the segment being completed, keeping earlier entries.
        text = self.text()
        idx = text.rfind(_MULTI_ENTITY_DELIMITER)
        prefix = text[: idx + 1] if idx != -1 else ""
        if self._allow_create_new and candidate == self._create_new_row:
            # Keep the typed name; only flag the force-create intent.
            self._matched_id = None
            self._create_new_requested = True
        else:
            # Set before setText(): its synchronous textChanged listeners read matched_id().
            self._matched_id = self._display_to_id.get(candidate)
            self._create_new_requested = False
            self.setText(prefix + candidate)
        if self._completer is not None:
            self._completer.popup().hide()
        # Emitted even when setText() was a no-op (same text) and fired no textChanged.
        self.picked.emit()

    def _clear_pick_state(self) -> None:
        self._matched_id = None
        self._create_new_requested = False

    def matched_id(self):
        """Id of the suggestion last picked, or None after any manual edit."""
        return self._matched_id

    def wants_new_entity(self) -> bool:
        """True when the user picked the "Create new X" row and has not edited since."""
        return self._create_new_requested

    def split_names(self) -> list[str]:
        """Every entity name typed into the field, split on the multi-entity delimiter."""
        return split_entity_names(self.text())

    def _make_completer(self, model: QStringListModel) -> QCompleter:
        # Wired via setWidget(), not setCompleter(): Qt's automatic wiring forces
        # the prefix to the whole field text, so "Bass;Le" would match nothing.
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setWidget(self)
        completer.activated.connect(self._on_completion_picked)
        completer.popup().setItemDelegate(ContextItemDelegate(lambda name: self._display_to_context.get(name, ""), completer.popup()))
        return completer


class EntityCompleterEdit(_CompleterPickMixin, QLineEdit):
    """QLineEdit with a completer over a preloaded {display_text: id} index."""

    picked = Signal()

    def __init__(self, placeholder_text: str = "", parent=None, *, allow_create_new: bool = False):
        super().__init__(parent)
        if placeholder_text:
            self.setPlaceholderText(placeholder_text)
        self._init_pick_state(allow_create_new)
        # Keyed by id as given; _display_to_context is the per-display lookup the delegate reads.
        self._context_by_id: dict = {}
        self._completer = None
        self.textEdited.connect(self._on_manual_edit)
        self.textEdited.connect(self._update_completions)

    def set_index(self, display_to_id: dict, context_by_id: dict | None = None) -> None:
        """Refresh the completer's backing model (and, if given, the id -> context map)."""
        self._display_to_id = dict(display_to_id)
        if context_by_id is not None:
            self._context_by_id = dict(context_by_id)
        self._display_to_context = {display: self._context_by_id.get(entity_id, "") for display, entity_id in self._display_to_id.items()}
        rows = self._rows_with_create_new(_current_segment(self.text()))
        # Update the model in place: this can run from returnPressed while the
        # popup is still dispatching that Enter, and replacing the completer
        # then was a use-after-free crash.
        if self._completer is not None and isinstance(self._completer.model(), QStringListModel):
            self._completer.model().setStringList(rows)
            return
        self._completer = self._make_completer(QStringListModel(rows, self))

    def add_to_index(self, display: str, entity_id, context: str = "") -> None:
        """Register a newly created entity so it is searchable at once, without a re-fetch."""
        self._display_to_id[display] = entity_id
        if context:
            self._context_by_id[entity_id] = context
        self.set_index(self._display_to_id, self._context_by_id)

    def _on_manual_edit(self, _text: str) -> None:
        self._clear_pick_state()

    def _update_completions(self, text: str) -> None:
        completer = self._completer
        if completer is None or not isinstance(completer.model(), QStringListModel):
            return
        segment = _current_segment(text)
        completer.model().setStringList(self._rows_with_create_new(segment))
        completer.setCompletionPrefix(segment)
        if completer.completionCount() > 0:
            completer.complete()
        elif completer.popup() is not None:
            completer.popup().hide()

    def reset(self) -> None:
        """Clear the field and every pick/create-new flag."""
        # Reset before clear(): its synchronous textChanged listeners read matched_id().
        self._clear_pick_state()
        self._create_new_row = None
        self.clear()


def find_or_create_by_name(controller, model_name: str, name_field: str, name: str, known_entities: list, *, extra_lookup: Callable[[], object] | None = None):
    """Return the case-insensitive match from `known_entities`/`extra_lookup`, else a new entity."""
    # Blank names never create a row; None is also what add_entity returns on failure.
    clean_name = (name or "").strip()
    if not clean_name:
        return None
    lowered = clean_name.lower()
    for entity in known_entities:
        if (getattr(entity, name_field, "") or "").strip().lower() == lowered:
            return entity
    if extra_lookup is not None:
        found = extra_lookup()
        if found:
            return found
    return controller.add.add_entity(model_name, **{name_field: clean_name})


# Preloads are capped and shared per model across every tab/dialog in a session.
_PRELOAD_ROW_CAP = 2000
_entity_cache: dict[str, list | None] = {}


def get_cached_entities(controller, model_name: str, *, force_refresh: bool = False) -> list | None:
    """Full cached row list for `model_name`, or None once the table exceeds _PRELOAD_ROW_CAP."""
    # Renames elsewhere are not seen; only register_cached_entity() and
    # invalidate_entity_cache() change the cache.
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
    """Append a newly created entity to `model_name`'s cached list, if one is cached."""
    cached = _entity_cache.get(model_name)
    if cached is not None:
        cached.append(entity)


def invalidate_entity_cache(model_name: str | None = None) -> None:
    """Drop the cached list for `model_name` (or all lists), forcing a re-fetch."""
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
    context_builder: Callable[[list], dict] | None = None,
    allow_create_new: bool = False,
):
    """Return a preloaded EntityCompleterEdit for small tables, else a BoundedSearchEdit."""
    # Both expose the same text()/matched_id()/add_to_index()/reset()/picked surface.
    # index_builder replaces the default {name: id} index (e.g. to disambiguate
    # same-named rows); context_builder maps rows to {id: dimmed context}.
    entities = get_cached_entities(controller, model_name)
    if entities is not None:
        index = index_builder(entities) if index_builder is not None else {getattr(e, name_field): getattr(e, id_field) for e in entities if getattr(e, name_field, None)}
        widget = EntityCompleterEdit(placeholder_text, parent, allow_create_new=allow_create_new)
        widget.set_index(index, context_builder(entities) if context_builder else None)
        return widget
    return BoundedSearchEdit(controller, model_name, name_field, id_field, placeholder_text, parent, context_builder=context_builder, allow_create_new=allow_create_new)


class BoundedSearchEdit(_CompleterPickMixin, QLineEdit):
    """EntityCompleterEdit fallback for large tables: suggestions come from a capped query per keystroke."""

    picked = Signal()

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
        context_builder: Callable[[list], dict] | None = None,
        allow_create_new: bool = False,
    ):
        super().__init__(parent)
        self._controller = controller
        self._model_name = model_name
        self._name_field = name_field
        self._id_field = id_field
        self._context_builder = context_builder
        self._init_pick_state(allow_create_new)
        self._last_matches: list = []

        if placeholder_text:
            self.setPlaceholderText(f"{placeholder_text} (min {self._MIN_CHARS} chars)")

        self._model = QStringListModel(self)
        self._completer = self._make_completer(self._model)
        self.textEdited.connect(self._on_text_edited)

    def _query_matches(self, segment: str) -> list:
        try:
            return self._controller.get.get_all_entities(self._model_name, limit=self._MAX_RESULTS, **{f"{self._name_field}__contains": segment}) or []
        except SQLAlchemyError as e:
            logger.error(f"BoundedSearchEdit: {self._model_name} search for {segment!r} failed: {e}")
            return []

    def _on_text_edited(self, text: str) -> None:
        self._clear_pick_state()
        segment = _current_segment(text).rstrip()
        if len(segment) >= self._MIN_CHARS:
            self._last_matches = self._query_matches(segment)[: self._MAX_RESULTS]
            self._display_to_id = {getattr(e, self._name_field): getattr(e, self._id_field) for e in self._last_matches if getattr(e, self._name_field, None)}
            context_by_id = (self._context_builder(self._last_matches) if self._context_builder else {}) or {}
            self._display_to_context = {display: context_by_id.get(entity_id, "") for display, entity_id in self._display_to_id.items()}
        else:
            self._last_matches = []
            self._display_to_id = {}
            self._display_to_context = {}
        self._model.setStringList(self._rows_with_create_new(segment))
        self._completer.setCompletionPrefix(segment)
        if self._display_to_id or self._create_new_row:
            self._completer.complete()
        elif self._completer.popup() is not None:
            self._completer.popup().hide()

    def known_matches(self) -> list:
        """Entities from the last on-demand query, for find_or_create_by_name's duplicate check."""
        return self._last_matches

    def add_to_index(self, display: str, entity_id, context: str = "") -> None:
        """No-op: the next keystroke re-queries the DB, so a new row is already searchable."""

    def reset(self) -> None:
        """Clear the field, the last query's results, and every pick/create-new flag."""
        # Reset before clear(): its synchronous textChanged listeners read matched_id().
        self._clear_pick_state()
        self._create_new_row = None
        self._last_matches = []
        self._display_to_id = {}
        self._display_to_context = {}
        self._model.setStringList([])
        self.clear()
