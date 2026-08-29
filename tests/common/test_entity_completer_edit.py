"""Regression test: pressing Enter with a completer suggestion highlighted
must commit *that* suggestion, not whatever partial text is still in the
field.

Both EntityCompleterEdit and BoundedSearchEdit wire their QCompleter
manually via setWidget() rather than QLineEdit.setCompleter(), so Qt does
no Return-vs-completer coordination on its own: the widgets' Return
handling used to fall straight through to QLineEdit's native keyPressEvent,
which fires returnPressed() -- and whatever add-this-entity slot is
connected to it -- with the text as typed. The completer's activated()
signal (which swaps in the highlighted suggestion) only arrived afterward,
so a caller like tag_association_tab._add() read the raw partial text and
matched_id()==None, creating a new entity out of the partial string instead
of using the one the user picked.
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.common.entity_completer_edit import (
    BoundedSearchEdit,
    ContextItemDelegate,
    EntityCompleterEdit,
    build_entity_search_widget,
    invalidate_entity_cache,
)


class _StubEntity:
    def __init__(self, genre_id, genre_name):
        self.genre_id = genre_id
        self.genre_name = genre_name


class _StubGet:
    def get_all_entities(self, model_name, **kwargs):
        return [_StubEntity(1, "Rock"), _StubEntity(2, "Rockabilly")]


class _StubController:
    def __init__(self):
        self.get = _StubGet()


def _highlight_first_suggestion(widget) -> None:
    """Select the popup's first row the way arrowing down to it would --
    done directly on the popup's model rather than via a synthetic Down
    keypress because the popup's real-desktop keyboard grab (what makes
    Down move its own selection instead of the line edit's) doesn't
    establish under the headless/offscreen QPA platform tests run under."""
    popup = widget._completer.popup()
    popup.setCurrentIndex(popup.model().index(0, 0))


def test_entity_completer_edit_enter_picks_highlighted_suggestion(qapp):
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2, "Pop": 3})

    captured = {}
    widget.returnPressed.connect(
        lambda: captured.update(text=widget.text(), matched_id=widget.matched_id())
    )

    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    QTest.keyClick(widget, Qt.Key_Return)

    assert captured == {"text": "Rock", "matched_id": 1}


def test_bounded_search_edit_enter_picks_highlighted_suggestion(qapp):
    widget = BoundedSearchEdit(_StubController(), "Genre", "genre_name", "genre_id")

    captured = {}
    widget.returnPressed.connect(
        lambda: captured.update(text=widget.text(), matched_id=widget.matched_id())
    )

    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    QTest.keyClick(widget, Qt.Key_Return)

    assert captured == {"text": "Rock", "matched_id": 1}


def test_entity_completer_edit_reset_clears_matched_id_before_signal(qapp):
    """reset()'s self.clear() is setText(""), which -- like the completion-
    pick path above -- emits textChanged synchronously. A textChanged
    listener (e.g. a dialog gating its OK button on matched_id()) must see
    matched_id() already cleared to None from inside that signal, not the
    stale id from before reset() was called."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2, "Pop": 3})
    widget._completer.activated.emit("Rock")
    assert widget.matched_id() == 1

    seen_during_signal = []
    widget.textChanged.connect(lambda _text: seen_during_signal.append(widget.matched_id()))

    widget.reset()

    assert seen_during_signal == [None]
    assert widget.matched_id() is None


def test_bounded_search_edit_reset_clears_matched_id_before_signal(qapp):
    widget = BoundedSearchEdit(_StubController(), "Genre", "genre_name", "genre_id")
    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    widget._on_completion_picked("Rock")
    assert widget.matched_id() == 1

    seen_during_signal = []
    widget.textChanged.connect(lambda _text: seen_during_signal.append(widget.matched_id()))

    widget.reset()

    assert seen_during_signal == [None]
    assert widget.matched_id() is None


# ── secondary context channel ───────────────────────────────────────────────


def test_set_index_without_context_map_is_unchanged(qapp):
    """AC1: set_index() with no context map leaves every row's context
    empty and behaves exactly as before."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Pop": 3})
    assert widget._display_to_context == {"Rock": "", "Pop": ""}


def test_set_index_stores_context_keyed_by_display(qapp):
    """AC1/AC2: context is handed in keyed by entity id but resolved to the
    display key -- including a disambiguated 'Name #id' key."""
    widget = EntityCompleterEdit()
    widget.set_index({"Foo #1": 1, "Foo #2": 2}, {1: "first band", 2: "second band"})
    assert widget._display_to_context == {"Foo #1": "first band", "Foo #2": "second band"}


def test_pick_sets_bare_name_not_context(qapp):
    """AC2: choosing a suggestion puts only the display key in the field --
    the context string never leaks into the line edit."""
    widget = EntityCompleterEdit()
    widget.set_index({"Wolves": 7}, {7: "Cornwall folk act"})
    widget._completer.activated.emit("Wolves")
    assert widget.text() == "Wolves"
    assert widget.matched_id() == 7


def test_delegate_reads_context_for_display_key(qapp):
    """AC2: the popup delegate resolves each row's dimmed context via the
    widget's live display->context lookup."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Pop": 3}, {1: "genre", 3: ""})
    delegate = widget._completer.popup().itemDelegate()
    assert isinstance(delegate, ContextItemDelegate)
    assert delegate._context_getter("Rock") == "genre"
    assert delegate._context_getter("Pop") == ""
    assert delegate._context_getter("unknown") == ""


def test_add_to_index_carries_context(qapp):
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1}, {1: "genre"})
    widget.add_to_index("Techno", 9, context="electronic")
    assert widget._display_to_context["Techno"] == "electronic"
    assert widget._display_to_context["Rock"] == "genre"


class _ContextGet:
    def __init__(self, rows):
        self._rows = rows

    def count_entities(self, model_name):
        return len(self._rows)

    def get_all_entities(self, model_name, **kwargs):
        if not kwargs:
            return list(self._rows)
        # crude __contains emulation for the bounded path
        ((key, needle),) = kwargs.items()
        field = key.split("__")[0]
        return [r for r in self._rows if needle.lower() in getattr(r, field).lower()]


class _ContextController:
    def __init__(self, rows):
        self.get = _ContextGet(rows)


def _genre_context_map(rows):
    return {r.genre_id: f"ctx-{r.genre_name}" for r in rows}


def test_build_entity_search_widget_applies_context_builder_preloaded(qapp):
    """AC3: context_builder runs against the preloaded list on the
    EntityCompleterEdit path."""
    rows = [_StubEntity(1, "Rock"), _StubEntity(2, "Jazz")]
    invalidate_entity_cache("CtxGenrePre")
    widget = build_entity_search_widget(
        _ContextController(rows),
        "CtxGenrePre",
        "genre_name",
        "genre_id",
        context_builder=_genre_context_map,
    )
    invalidate_entity_cache("CtxGenrePre")
    assert isinstance(widget, EntityCompleterEdit)
    assert widget._display_to_context == {"Rock": "ctx-Rock", "Jazz": "ctx-Jazz"}


def test_bounded_search_edit_applies_context_builder(qapp):
    """AC8: context shows for on-demand results and known_matches() is
    unaffected."""
    rows = [_StubEntity(1, "Rock"), _StubEntity(2, "Rockabilly"), _StubEntity(3, "Pop")]
    widget = BoundedSearchEdit(
        _ContextController(rows),
        "Genre",
        "genre_name",
        "genre_id",
        context_builder=_genre_context_map,
    )
    QTest.keyClicks(widget, "Rock")
    assert widget._display_to_context == {"Rock": "ctx-Rock", "Rockabilly": "ctx-Rockabilly"}
    assert {e.genre_name for e in widget.known_matches()} == {"Rock", "Rockabilly"}
